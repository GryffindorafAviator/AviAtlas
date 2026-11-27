import React, { useEffect, useRef, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import Markdown from 'react-markdown';
import { request, type Doc, type Answer } from './api';
import 'react-pdf/dist/Page/TextLayer.css';
import 'react-pdf/dist/Page/AnnotationLayer.css';
pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString();
const statusNames: Record<string, string> = { uploaded: '待处理', queued: '排队中', parsing: '解析中', indexing: '建立索引', indexed: '可提问', failed: '处理失败', deleting: '删除中' };
export function Reader({ doc }: { doc: Doc }) {
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
  async function ask() {
    if (!question.trim() || asking || doc.status !== 'indexed') return;
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
    <aside className="assistant"><div className="assistant-title"><span className="spark">✧</span><div><h2>与文档对话</h2><p>从问题出发，回到原文。</p></div><span className="pill">{statusNames[doc.status] || doc.status}</span></div>
      <div className="conversation" aria-live="polite">{!messages.length && !asking && <div className="welcome"><div className="welcome-icon">✧</div><h3>让阅读多一点启发</h3><p>询问文档中的概念、方法或细节。<br/>回答附带来源，方便回到原文核对。</p><div className="suggestions">{['文档讨论了哪些主要问题？', '文中提出的方法是什么？'].map(q => <button key={q} disabled={doc.status !== 'indexed'} onClick={() => setQuestion(q)}>{q}<span>↗</span></button>)}</div></div>}
      {messages.map((m, i) => <article className="exchange" key={i}><div className="question">{m.question}</div><div className="answer-label">✧ AVIATLAS</div><div className="answer"><Markdown components={{ img: ({ alt }) => <span>{alt || "图片"}</span> }}>{m.result.answer}</Markdown></div>{!!m.result.sources.length && <div className="sources"><div className="eyebrow">原文依据 · {m.result.sources.length}</div>{m.result.sources.map((s, index) => { const p = s.page_range?.[0] ?? s.provenance[0]?.page_number; return <details key={s.chunk_id}><summary><span className="source-number">{index + 1}</span><span className="source-name">{doc.filename}</span><span>{p ? `第 ${p} 页` : '无页码'}</span></summary><p>{s.content}</p>{p && <button className="text-button" disabled={!pages} onClick={() => jump(p)}>查看第 {p} 页 ↗</button>}</details>; })}</div>}</article>)}
      {asking && <p className="thinking">✧ 正在检索文档并生成回答…</p>}<div ref={bottom}/></div>
      <form className="composer" onSubmit={event => { event.preventDefault(); void ask(); }}>{doc.status !== 'indexed' && <p className="notice">{doc.status === 'failed' ? '文档处理失败，请在图书馆中重试。' : '文档准备完成后即可提问。'}</p>}{error && <p className="error" role="alert">{error}</p>}<label className="sr-only" htmlFor="question">关于这份文档的问题</label><textarea id="question" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
          event.preventDefault(); void ask();
        }
      }} placeholder="关于这份文档，你想了解什么？" disabled={asking || doc.status !== 'indexed'} rows={3}/><div className="composer-footer"><span>Enter 发送 · Shift+Enter 换行</span><button type="button" onClick={() => void ask()} className="primary" disabled={asking || !question.trim() || doc.status !== 'indexed'}>{asking ? '发送中…' : '发送 ↑'}</button></div><p className="fine">AI 回答可能存在错误，请结合原文核对。</p></form>
    </aside>
  </div>;
}
