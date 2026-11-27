import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Document, Page, pdfjs } from 'react-pdf';
import Markdown from 'react-markdown';
import { request, type Doc, type Answer } from './api';
import 'react-pdf/dist/Page/TextLayer.css';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import './style.css';
pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString();
const statusNames: Record<string, string> = { uploaded: '待处理', queued: '排队中', parsing: '解析中', indexing: '建立索引', indexed: '可提问', failed: '处理失败', deleting: '删除中' };
const busy = (status: string) => ['queued', 'parsing', 'indexing', 'deleting'].includes(status);
function Reader({ doc }: { doc: Doc }) {
  const [page, setPage] = useState(1), [pages, setPages] = useState(0), [scale, setScale] = useState(1);
  const [width, setWidth] = useState(600), [question, setQuestion] = useState('');
  const [messages, setMessages] = useState<{ question: string; result: Answer }[]>([]);
  const [asking, setAsking] = useState(false), [error, setError] = useState('');
  const box = useRef<HTMLDivElement>(null), bottom = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const observer = new ResizeObserver(entries => setWidth(Math.max(240, entries[0].contentRect.width - 48)));
    if (box.current) observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [messages, asking]);
  const jump = (p: number) => { if (pages) setPage(Math.max(1, Math.min(pages, p))); };
  async function ask(event: React.FormEvent) {
    event.preventDefault(); if (!question.trim() || asking) return;
    const text = question.trim(); setAsking(true); setError('');
    try {
      const result = await request<Answer>('/documents/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: doc.document_id, question: text, top_k: 3 }) });
      if (alive.current) { setMessages(m => [...m, { question: text, result }]); setQuestion(''); }
    } catch (e) { if (alive.current) setError((e as Error).message); }
    finally { if (alive.current) setAsking(false); }
  }
  return <div className="workspace">
    <section className="reader" aria-label="PDF 阅读器">
      <div className="reader-toolbar"><span className="eyebrow">原始文档</span><div className="controls"><button disabled={page <= 1} onClick={() => jump(page - 1)} aria-label="上一页">←</button><label><input aria-label="页码" type="number" min="1" max={pages || 1} value={page} onChange={e => jump(Number(e.target.value) || 1)} /> / {pages || '—'}</label><button disabled={!pages || page >= pages} onClick={() => jump(page + 1)} aria-label="下一页">→</button><span className="divider"/><button onClick={() => setScale(s => Math.max(.5, s - .1))} aria-label="缩小">−</button><span>{Math.round(scale * 100)}%</span><button onClick={() => setScale(s => Math.min(2, s + .1))} aria-label="放大">+</button></div></div>
      <div className="pdf-stage" ref={box}><Document file={`/api/v1/documents/${doc.document_id}/file`} onLoadSuccess={({ numPages }) => setPages(numPages)} loading={<p className="placeholder">正在打开 PDF…</p>} error={<p className="error">无法打开 PDF。请确认原文件仍存在，再重新打开文档。</p>}><Page pageNumber={page} width={Math.min(width, 900) * scale} loading={<p>正在渲染页面…</p>} error={<p className="error">页面渲染失败，请尝试其他页。</p>} /></Document></div>
    </section>
    <aside className="assistant"><div className="assistant-title"><span className="spark">✧</span><div><h2>与文档对话</h2><p>从问题出发，回到原文。</p></div><span className="pill">文档问答</span></div>
      <div className="conversation" aria-live="polite">{!messages.length && !asking && <div className="welcome"><div className="welcome-icon">✧</div><h3>让阅读多一点启发</h3><p>询问文档中的概念、方法或细节。<br/>回答附带来源，方便回到原文核对。</p><div className="suggestions">{['文档讨论了哪些主要问题？', '文中提出的方法是什么？'].map(q => <button key={q} disabled={doc.status !== 'indexed'} onClick={() => setQuestion(q)}>{q}<span>↗</span></button>)}</div></div>}
      {messages.map((m, i) => <article className="exchange" key={i}><div className="question">{m.question}</div><div className="answer-label">✧ AVIATLAS</div><div className="answer"><Markdown components={{ img: ({ alt }) => <span>{alt || "图片"}</span> }}>{m.result.answer}</Markdown></div>{!!m.result.sources.length && <div className="sources"><div className="eyebrow">原文依据 · {m.result.sources.length}</div>{m.result.sources.map((s, index) => { const p = s.page_range?.[0] ?? s.provenance[0]?.page_number; return <details key={s.chunk_id}><summary><span className="source-number">{index + 1}</span><span className="source-name">{doc.filename}</span><span>{p ? `第 ${p} 页` : '无页码'}</span></summary><p>{s.content}</p>{p && <button className="text-button" disabled={!pages} onClick={() => jump(p)}>查看第 {p} 页 ↗</button>}</details>; })}</div>}</article>)}
      {asking && <p className="thinking">✧ 正在检索文档并生成回答…</p>}<div ref={bottom}/></div>
      <form className="composer" onSubmit={ask}>{doc.status !== 'indexed' && <p className="notice">{doc.status === 'failed' ? '文档处理失败，请在图书馆中重试。' : '文档准备完成后即可提问。'}</p>}{error && <p className="error" role="alert">{error}</p>}<label className="sr-only" htmlFor="question">关于这份文档的问题</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="关于这份文档，你想了解什么？" disabled={asking || doc.status !== 'indexed'} rows={3}/><div className="composer-footer"><span>仅使用当前文档 · 对话暂不保存</span><button className="primary" disabled={asking || !question.trim() || doc.status !== 'indexed'}>发送 ↑</button></div><p className="fine">AI 回答可能存在错误，请结合原文核对。</p></form>
    </aside>
  </div>;
}
function App() {
  const [docs, setDocs] = useState<Doc[]>([]), [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState(''), [loading, setLoading] = useState(true), [uploading, setUploading] = useState(false);
  const [working, setWorking] = useState<string | null>(null), [offset, setOffset] = useState(0), [total, setTotal] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const current = docs.find(d => d.document_id === selected);
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { const data = await request<{ items: Doc[]; total: number }>(`/documents?limit=20&offset=${offset}`); if (active) { setDocs(data.items); setTotal(data.total); setError(''); } }
      catch (e) { if (active) setError((e as Error).message); }
      finally { if (active) { setLoading(false); timer = setTimeout(poll, 3000); } }
    }
    setLoading(true); void poll(); return () => { active = false; clearTimeout(timer); };
  }, [offset]);
  async function refresh() { const data = await request<{ items: Doc[]; total: number }>(`/documents?limit=20&offset=${offset}`); setDocs(data.items); setTotal(data.total); if (!data.items.length && offset > 0) setOffset(Math.max(0, offset - 20)); }
  async function upload(file?: File) {
    if (!file) return; if (!file.name.toLowerCase().endsWith('.pdf')) { setError('请选择 PDF 文件。'); return; }
    setUploading(true); setError('');
    try { const body = new FormData(); body.append('file', file); await request('/documents/upload', { method: 'POST', body }); setOffset(0); await refresh(); }
    catch (e) { setError((e as Error).message); } finally { setUploading(false); if (input.current) input.current.value = ''; }
  }
  async function action(doc: Doc, remove = false) {
    if (remove && !window.confirm(`删除「${doc.filename}」及其索引？此操作无法撤销。`)) return;
    setWorking(doc.document_id); setError('');
    try { await request(`/documents/${doc.document_id}${remove ? '' : doc.status === 'failed' ? '/retry' : '/process'}`, { method: remove ? 'DELETE' : 'POST' }); await refresh(); }
    catch (e) { setError((e as Error).message); } finally { setWorking(null); }
  }
  return <><header className="topbar"><a className="brand" href="#" onClick={() => setSelected(null)}><span className="brand-mark">A</span>AviAtlas<span className="brand-sub">知识，有迹可循。</span></a><span className="local"><i/>个人工作空间</span></header><div className="app-shell"><nav className="sidebar"><div className="eyebrow">工作空间</div><button className="nav-item active" onClick={() => setSelected(null)}>▤ <span>我的图书馆</span><span className="nav-count">{total}</span></button><div className="sidebar-note"><span>从阅读到理解</span><p>收藏你的文档，<br/>探索其中的知识。</p></div><div className="sidebar-bottom">AVIATLAS <span>MVP / 01</span></div></nav><main>
      <div className="page-heading"><div><div className="breadcrumb"><button onClick={() => setSelected(null)}>我的图书馆</button>{current && <span> / 阅读空间</span>}</div><h1>{current ? current.filename : '我的图书馆'}</h1><p>{current ? '阅读、提问，并在原文中找到依据。' : '把散落的文档，变成随时可以探索的知识。'}</p></div>{!current && <button className="primary upload-button" disabled={uploading} onClick={() => input.current?.click()}>{uploading ? '上传中…' : '+ 上传 PDF'}</button>}<input className="sr-only" ref={input} type="file" accept="application/pdf,.pdf" onChange={e => void upload(e.target.files?.[0])}/></div>
      {error && <div className="error banner" role="alert">{error} <button onClick={() => void refresh().then(() => setError('')).catch(e => setError(e.message))}>重新连接</button></div>}
      {current ? <Reader key={current.document_id} doc={current}/> : <><div className="library-toolbar"><span>全部文档 <b>{total}</b></span><span>PDF · 按上传时间排序</span></div>{loading && !docs.length ? <div className="empty">正在加载图书馆…</div> : !docs.length && !error ? <div className="empty"><div className="empty-icon">▤</div><h2>你的第一份知识，从这里开始</h2><p>上传一份 PDF，打开阅读，向文档提问。</p><button className="primary" disabled={uploading} onClick={() => input.current?.click()}>上传第一份 PDF</button></div> : <div className="document-grid">{docs.map((doc, index) => <article className="document-card" key={doc.document_id}><button className="document-open" onClick={() => setSelected(doc.document_id)}><div className={`cover cover-${index % 3}`}><span className="cover-type">PDF DOCUMENT</span><div className="cover-lines"/><span className="cover-title">{doc.filename.replace(/\.pdf$/i, '')}</span><span className="cover-footer">AVIATLAS LIBRARY ↗</span></div><div className="card-info"><h2>{doc.filename}</h2><div className="card-meta">{doc.page_count ? `${doc.page_count} 页 · ` : ''}{new Date(doc.uploaded_at).toLocaleDateString('zh-CN')}</div></div></button><div className="card-footer"><span className={`status status-${doc.status}`}><i/>{statusNames[doc.status] || doc.status}</span><div>{['failed','uploaded'].includes(doc.status) && <button disabled={working === doc.document_id} onClick={() => void action(doc)}>{doc.status === 'failed' ? '重试' : '开始处理'}</button>}<button disabled={busy(doc.status) || working === doc.document_id} onClick={() => void action(doc, true)}>删除</button></div></div>{doc.last_error && <p className="card-error">{doc.last_error}</p>}</article>)}</div>}{total > 20 && <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(n => n - 20)}>上一页</button><span>{Math.floor(offset / 20) + 1} / {Math.ceil(total / 20)}</span><button disabled={offset + 20 >= total} onClick={() => setOffset(n => n + 20)}>下一页</button></div>}<footer className="library-footer">每一次提问，都有来源可寻。<span>支持 PDF · 本地文档库</span></footer></>}
    </main></div></>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
