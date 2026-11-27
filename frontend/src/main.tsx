import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { request, type Doc } from './api';
import { Reader } from './Reader';
import './style.css';
const statusNames: Record<string, string> = { uploaded: '待处理', queued: '排队中', parsing: '解析中', indexing: '建立索引', indexed: '可提问', failed: '处理失败', deleting: '删除中' };
const busy = (status: string) => ['queued', 'parsing', 'indexing', 'deleting'].includes(status);
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
