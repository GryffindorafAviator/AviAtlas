import React, { useEffect, useRef, useState } from 'react';
import { formatNames, isSupportedFile, request, UPLOAD_ACCEPT, type Doc, type Collection, type Source } from './api';
import { Assistant } from './Assistant';
import { CollectionDialog } from './CollectionDialog';
import { Reader } from './Reader';
import { InspectorPanel } from './InspectorPanel';
import { SearchPanel } from './SearchPanel';
type Tab = 'documents' | 'search' | 'ask' | 'inspect';
const tabs: [Tab, string][] = [['documents', '文档'], ['search', '搜索'], ['ask', '问答'], ['inspect', '检索调试']];
const statusNames: Record<string, string> = { uploaded: '待处理', queued: '排队中', parsing: '解析中', indexing: '建立索引', indexed: '可提问', failed: '处理失败', deleting: '删除中' };
const busy = (status: string) => ['queued', 'parsing', 'indexing', 'deleting'].includes(status);
export function App() {
  const [docs, setDocs] = useState<Doc[]>([]), [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState(''), [loading, setLoading] = useState(true), [uploading, setUploading] = useState(false);
  const [working, setWorking] = useState<string | null>(null), [offset, setOffset] = useState(0), [total, setTotal] = useState(0);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState('');
  const [uploadCollection, setUploadCollection] = useState('');
  const [membershipDoc, setMembershipDoc] = useState<Doc | null>(null);
  const [collectionBusy, setCollectionBusy] = useState(false);
  const [collectionError, setCollectionError] = useState('');
  const [newName, setNewName] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [tab, setTab] = useState<Tab>('documents');
  const [openPage, setOpenPage] = useState(1), [openBlock, setOpenBlock] = useState<string | undefined>(), [openedDoc, setOpenedDoc] = useState<Doc | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const activeCollection = collections.find(c => c.collection_id === collectionId);
  const listUrl = `/documents?limit=20&offset=${offset}${collectionId ? `&collection_id=${collectionId}` : ''}`;
  const current = docs.find(d => d.document_id === selected) ?? (openedDoc?.document_id === selected ? openedDoc : undefined);
  const scope = collectionId ? { collection_id: collectionId } : {};
  const scopeName = activeCollection?.name || '全部文档';
  function openDocument(id: string | null, page = 1, block?: string) { setOpenPage(page); setOpenBlock(block); setSelected(id); }
  async function openSource(source: Source, page?: number) {
    const id = source.document_id;
    if (!id) return;
    if (!docs.some(d => d.document_id === id)) {
      try { setOpenedDoc(await request<Doc>(`/documents/${id}`)); }
      catch (e) { setError((e as Error).message); return; }
    }
    openDocument(id, page ?? 1, source.block_ids?.[0]);
  }
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { const data = await request<{ items: Doc[]; total: number }>(listUrl); if (active) { setDocs(data.items); setTotal(data.total); if (!data.items.length && offset > 0) setOffset(Math.max(0, offset - 20)); setError(''); } }
      catch (e) { if (active) setError((e as Error).message); }
      finally { if (active) { setLoading(false); timer = setTimeout(poll, 3000); } }
    }
    setLoading(true); void poll(); return () => { active = false; clearTimeout(timer); };
  }, [listUrl, refreshKey]);
  useEffect(() => {
    let active = true;
    request<{ items: Collection[] }>('/collections').then(data => {
      if (active) { setCollections(data.items); setCollectionError(''); }
    }).catch(e => { if (active) setCollectionError(e.message); });
    return () => { active = false; };
  }, [refreshKey]);
  function selectCollection(id: string) {
    setCollectionId(id); setUploadCollection(id); setSelected(null); setOffset(0); setDocs([]);
  }
  async function reloadCollections() {
    const data = await request<{ items: Collection[] }>('/collections'); setCollections(data.items);
  }
  async function createCollection(event: React.FormEvent) {
    event.preventDefault(); if (!newName.trim()) return;
    setCollectionBusy(true); setCollectionError('');
    try {
      const created = await request<Collection>('/collections', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: newName.trim() }) });
      setNewName(''); await reloadCollections(); selectCollection(created.collection_id);
    } catch (e) { setCollectionError((e as Error).message); }
    finally { setCollectionBusy(false); }
  }
  async function modifyCollection(remove = false) {
    if (!activeCollection) return;
    let name: string | null = null;
    if (remove) {
      if (!window.confirm(`删除分组「${activeCollection.name}」？分组内的文档会保留。`)) return;
    } else {
      name = window.prompt('新的分组名称', activeCollection.name);
      if (name === null) return;
      if (!name.trim()) { setCollectionError('分组名称不能为空。'); return; }
    }
    setCollectionBusy(true); setCollectionError('');
    try {
      await request(`/collections/${activeCollection.collection_id}`, { method: remove ? 'DELETE' : 'PATCH', ...(remove ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name!.trim() }) }) });
      if (remove) selectCollection('');
      await reloadCollections(); setRefreshKey(n => n + 1);
    } catch (e) { setCollectionError((e as Error).message); }
    finally { setCollectionBusy(false); }
  }
  async function refresh() { const data = await request<{ items: Doc[]; total: number }>(listUrl); setDocs(data.items); setTotal(data.total); if (!data.items.length && offset > 0) setOffset(Math.max(0, offset - 20)); }
  async function upload(file?: File) {
    if (!file) return; if (!isSupportedFile(file.name)) { setError('支持 PDF、DOCX、Markdown（.md）和纯文本（.txt）文件。'); return; }
    setUploading(true); setError('');
    try { const body = new FormData(); body.append('file', file); await request(`/documents/upload${uploadCollection ? `?collection_id=${uploadCollection}` : ''}`, { method: 'POST', body }); setOffset(0); setRefreshKey(n => n + 1); await reloadCollections(); }
    catch (e) { setError((e as Error).message); } finally { setUploading(false); if (input.current) input.current.value = ''; }
  }
  async function action(doc: Doc, remove = false) {
    if (remove && !window.confirm(`删除「${doc.filename}」及其索引？此操作无法撤销。`)) return;
    if (!remove && doc.needs_reprocess && !window.confirm(`重新处理「${doc.filename}」？处理期间这份文档暂时无法检索。`)) return;
    setWorking(doc.document_id); setError('');
    try { await request(`/documents/${doc.document_id}${remove ? '' : doc.status === 'failed' ? '/retry' : '/process'}`, { method: remove ? 'DELETE' : 'POST' }); await refresh(); await reloadCollections(); }
    catch (e) { setError((e as Error).message); } finally { setWorking(null); }
  }
  return <div className="terminal">
    <header className="terminal-header">
      <a className="brand" href="#" onClick={() => openDocument(null)}>AVIATLAS // PERSONAL KNOWLEDGE LIBRARY</a>
      <h1>AVIATLAS</h1>
      <p>阅读、提问，并在原文中找到依据。</p>
    </header>
    <div className="status-bar">
      <span><i className="led ok" aria-hidden="true"/>个人工作空间</span>
      <span>文档 {String(total).padStart(3, '0')}</span>
      <span>{current ? 'MODE: DOCUMENT' : 'MODE: LIBRARY'}</span>
    </div>
    <main className={current ? '' : 'collection-layout'}>
      {!current && <aside className="collection-sidebar" aria-label="文档分组"><h2>// COLLECTIONS</h2>
        <button className={`btn collection-link ${!collectionId ? 'is-active' : ''}`} aria-pressed={!collectionId} onClick={() => selectCollection('')}>全部文档</button>
        <div className="collection-links">{collections.map(c => <button key={c.collection_id} className={`btn collection-link ${collectionId === c.collection_id ? 'is-active' : ''}`} aria-pressed={collectionId === c.collection_id} onClick={() => selectCollection(c.collection_id)}><span>{c.name}</span><small>{c.document_count}</small></button>)}</div>
        <form className="collection-create" onSubmit={createCollection}><label htmlFor="collection-name">新建分组</label><input id="collection-name" className="input" maxLength={100} value={newName} onChange={e => setNewName(e.target.value)} placeholder="例如：机器学习" disabled={collectionBusy}/><button className="btn btn-ok" disabled={collectionBusy || !newName.trim()}>+ 新建分组</button></form>
        {collectionError && <p className="error" role="alert">{collectionError}</p>}
      </aside>}
      <section className="library-content">
      <div className="page-heading">
        <div>{current && <button className="btn back-button" onClick={() => openDocument(null)}>← 返回图书馆</button>}
          <h2>{current ? '// DOCUMENT' : '// LIBRARY'}</h2>
          {current ? <p className="document-title">{current.filename}</p> : <p>{activeCollection ? activeCollection.name : "我的图书馆"} · 上传 PDF、DOCX、Markdown 或 TXT，打开阅读并提问。</p>}
        </div>
        {!current && <div className="upload-controls"><label htmlFor="upload-collection">上传到</label><select id="upload-collection" value={uploadCollection} onChange={e => setUploadCollection(e.target.value)} disabled={uploading}><option value="">不加入分组</option>{collections.map(c => <option key={c.collection_id} value={c.collection_id}>{c.name}</option>)}</select><button className="btn btn-ok" disabled={uploading} onClick={() => input.current?.click()}>{uploading ? '上传中…' : '+ 上传文档'}</button></div>}
        <input className="sr-only" ref={input} type="file" accept={UPLOAD_ACCEPT} onChange={e => void upload(e.target.files?.[0])}/>
      </div>
      {error && <div className="panel-inset error banner" role="alert">{error} <button className="btn" onClick={() => void refresh().then(() => setError('')).catch(e => setError(e.message))}>重新连接</button></div>}
      {!current && <div className="library-tabs" role="tablist" aria-label="图书馆视图">{tabs.map(([id, label]) => <button key={id} role="tab" id={`tab-${id}`} aria-selected={tab === id} className={`btn ${tab === id ? 'is-active' : ''}`} onClick={() => setTab(id)}>{label}</button>)}<span className="tab-scope">范围：{scopeName}</span></div>}
      {current ? <Reader key={current.document_id} doc={current} initialPage={openPage} initialBlock={openBlock}/>
      : tab === 'search' ? <SearchPanel key={collectionId} scope={scope} scopeName={scopeName} onOpen={(s, p) => void openSource(s, p)}/>
      : tab === 'inspect' ? <InspectorPanel key={collectionId} scope={scope} scopeName={scopeName} onOpen={(s, p) => void openSource(s, p)}/>
      : tab === 'ask' ? <div className="library-assistant"><Assistant key={collectionId} scope={scope}
          title={`// 向${activeCollection ? '分组' : '图书馆'}提问`} subtitle={`跨「${scopeName}」中的文档检索并回答。`}
          disabledNotice={!loading && total === 0 ? '范围内还没有文档，请先上传。' : undefined}
          placeholder={`关于「${scopeName}」，你想了解什么？`} suggestions={['这些文档共同讨论了哪些主题？', '哪些文档提到了相同的概念？']}
          onOpenSource={(s, p) => void openSource(s, p)}/></div>
      : <>
        <div className="library-toolbar"><span>{activeCollection?.name || '全部文档'} / {total}</span>{activeCollection ? <div className="collection-actions"><button className="btn" disabled={collectionBusy} onClick={() => void modifyCollection()}>重命名</button><button className="btn btn-warn" disabled={collectionBusy} onClick={() => void modifyCollection(true)}>删除分组</button></div> : <span>按上传时间排序</span>}</div>
        {loading && !docs.length ? <div className="panel-inset empty">正在加载图书馆…</div> : !docs.length && !error ? <div className="panel-inset empty"><h3>{activeCollection ? "分组中还没有文档" : "上传第一份文档"}</h3><p>{activeCollection ? "上传文档，或在全部文档中通过“管理分组”加入已有文档。" : "上传一份文档，打开阅读，向文档提问。"}</p><button className="btn btn-ok" disabled={uploading} onClick={() => input.current?.click()}>上传文档</button></div> : <div className="document-list">{docs.map(doc => <article className="panel-inset document-card" key={doc.document_id}>
          <div className="document-info"><h3>{doc.filename}</h3><div className="document-groups">{collections.filter(c => doc.collection_ids?.includes(c.collection_id)).map(c => <span key={c.collection_id}>{c.name}</span>)}</div><span className="card-meta"><span className="format-tag">{formatNames[doc.source_format ?? 'pdf']}</span>{doc.ocr_page_count ? <span className="format-tag" title="这些页面没有文字层，内容由 OCR 识别，可能有错字">OCR {doc.ocr_page_count} 页</span> : null}{doc.page_count ? `${doc.page_count} 页 · ` : ''}{new Date(doc.uploaded_at).toLocaleDateString('zh-CN')}</span>{doc.needs_reprocess && <span className="card-meta reprocess-hint">用旧版分块处理，重新处理后表格与公式分块更准确。</span>}</div>
          <span className={`status status-${doc.status}`}><i className={`led ${doc.status === 'indexed' ? 'ok' : doc.status === 'failed' ? 'warn' : ''}`} aria-hidden="true"/>{statusNames[doc.status] || doc.status}</span>
          <div className="document-actions"><button className="btn" onClick={() => setMembershipDoc(doc)}>管理分组</button><button className="btn" onClick={() => openDocument(doc.document_id)}>打开</button>{(['failed','uploaded'].includes(doc.status) || doc.needs_reprocess) && <button className="btn btn-ok" disabled={working === doc.document_id} onClick={() => void action(doc)}>{doc.status === 'failed' ? '重试' : doc.needs_reprocess ? '重新处理' : '开始处理'}</button>}<button className="btn btn-warn" disabled={busy(doc.status) || working === doc.document_id} onClick={() => void action(doc, true)}>删除</button></div>
          {doc.last_error && <p className="card-error">{doc.last_error}</p>}
        </article>)}</div>}
        {total > 20 && <div className="pagination"><button className="btn" disabled={offset === 0} onClick={() => setOffset(n => n - 20)}>上一页</button><span>{Math.floor(offset / 20) + 1} / {Math.ceil(total / 20)}</span><button className="btn" disabled={offset + 20 >= total} onClick={() => setOffset(n => n + 20)}>下一页</button></div>}
      </>}
      </section>
    </main>
    {membershipDoc && <CollectionDialog doc={membershipDoc} collections={collections} onClose={() => setMembershipDoc(null)} onSaved={async () => { await reloadCollections(); await refresh(); setRefreshKey(n => n + 1); }}/>}
    <footer className="terminal-footer">AVIATLAS // READ · ASK · TRACE</footer>
  </div>;
}
