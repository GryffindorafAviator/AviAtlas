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
  return <div className="terminal">
    <header className="terminal-header">
      <a className="brand" href="#" onClick={() => setSelected(null)}>AVIATLAS // PERSONAL KNOWLEDGE LIBRARY</a>
      <h1>AVIATLAS</h1>
      <p>阅读、提问，并在原文中找到依据。</p>
    </header>
    <div className="status-bar">
      <span><i className="led ok" aria-hidden="true"/>个人工作空间</span>
      <span>文档 {String(total).padStart(3, '0')}</span>
      <span>{current ? 'MODE: DOCUMENT' : 'MODE: LIBRARY'}</span>
    </div>
    <main>
      <div className="page-heading">
        <div>{current && <button className="btn back-button" onClick={() => setSelected(null)}>← 返回图书馆</button>}
          <h2>{current ? '// DOCUMENT' : '// LIBRARY'}</h2>
          {current ? <p className="document-title">{current.filename}</p> : <p>我的图书馆 · 上传 PDF，打开阅读并提问。</p>}
        </div>
        {!current && <button className="btn btn-ok" disabled={uploading} onClick={() => input.current?.click()}>{uploading ? '上传中…' : '+ 上传 PDF'}</button>}
        <input className="sr-only" ref={input} type="file" accept="application/pdf,.pdf" onChange={e => void upload(e.target.files?.[0])}/>
      </div>
      {error && <div className="panel-inset error banner" role="alert">{error} <button className="btn" onClick={() => void refresh().then(() => setError('')).catch(e => setError(e.message))}>重新连接</button></div>}
      {current ? <Reader key={current.document_id} doc={current}/> : <>
        <div className="library-toolbar"><span>全部文档 / {total}</span><span>PDF · 按上传时间排序</span></div>
        {loading && !docs.length ? <div className="panel-inset empty">正在加载图书馆…</div> : !docs.length && !error ? <div className="panel-inset empty"><h3>上传第一份文档</h3><p>上传一份 PDF，打开阅读，向文档提问。</p><button className="btn btn-ok" disabled={uploading} onClick={() => input.current?.click()}>上传 PDF</button></div> : <div className="document-list">{docs.map(doc => <article className="panel-inset document-card" key={doc.document_id}>
          <div className="document-info"><h3>{doc.filename}</h3><span className="card-meta">{doc.page_count ? `${doc.page_count} 页 · ` : ''}{new Date(doc.uploaded_at).toLocaleDateString('zh-CN')}</span></div>
          <span className={`status status-${doc.status}`}><i className={`led ${doc.status === 'indexed' ? 'ok' : doc.status === 'failed' ? 'warn' : ''}`} aria-hidden="true"/>{statusNames[doc.status] || doc.status}</span>
          <div className="document-actions"><button className="btn" onClick={() => setSelected(doc.document_id)}>打开</button>{['failed','uploaded'].includes(doc.status) && <button className="btn btn-ok" disabled={working === doc.document_id} onClick={() => void action(doc)}>{doc.status === 'failed' ? '重试' : '开始处理'}</button>}<button className="btn btn-warn" disabled={busy(doc.status) || working === doc.document_id} onClick={() => void action(doc, true)}>删除</button></div>
          {doc.last_error && <p className="card-error">{doc.last_error}</p>}
        </article>)}</div>}
        {total > 20 && <div className="pagination"><button className="btn" disabled={offset === 0} onClick={() => setOffset(n => n - 20)}>上一页</button><span>{Math.floor(offset / 20) + 1} / {Math.ceil(total / 20)}</span><button className="btn" disabled={offset + 20 >= total} onClick={() => setOffset(n => n + 20)}>下一页</button></div>}
      </>}
    </main>
    <footer className="terminal-footer">AVIATLAS // READ · ASK · TRACE</footer>
  </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
