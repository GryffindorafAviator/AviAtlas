import React, { useEffect, useState } from 'react';
import { request, type Chunk, type Doc, type DocumentChunks, type OrphanedCorrection } from './api';

type Filter = 'all' | 'edited' | 'excluded';
const json = (method: string, body?: unknown): RequestInit =>
  ({ method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
const location = (c: { page_range?: [number, number] | null; section_title?: string | null }) =>
  [c.page_range ? (c.page_range[0] === c.page_range[1] ? `第 ${c.page_range[0]} 页` : `第 ${c.page_range[0]}–${c.page_range[1]} 页`) : null,
   c.section_title ? `§ ${c.section_title}` : null].filter(Boolean).join(' · ');

function Orphan({ orphan, chunks, busy, onAttach, onDismiss }: { orphan: OrphanedCorrection; chunks: Map<string, Chunk>; busy: boolean; onAttach: (chunk: string) => void; onDismiss: () => void }) {
  const candidates = orphan.candidate_chunk_ids.map(id => chunks.get(id)).filter((c): c is Chunk => !!c);
  return <article className="panel-inset orphan">
    <div className="chunk-meta"><span className="format-tag">{orphan.excluded ? '排除' : '编辑'}</span><span>{location(orphan.context) || '原位置未知'}</span></div>
    {orphan.text && <><div className="eyebrow">你的更正</div><p className="chunk-text">{orphan.text}</p></>}
    <details><summary>原来生成的文本</summary><p className="chunk-text">{orphan.original_text}</p></details>
    {candidates.length
      ? <><div className="eyebrow">可能对应的新分块</div>{candidates.map(c => <div className="candidate" key={c.chunk_id}><p className="chunk-text">#{c.position} {c.content.slice(0, 240)}{c.content.length > 240 ? '…' : ''}</p><button className="btn btn-ok" disabled={busy} onClick={() => onAttach(c.chunk_id)}>应用到这一块</button></div>)}</>
      : <p className="notice">没有找到来自相同段落的新分块。</p>}
    <button className="btn btn-warn" disabled={busy} onClick={onDismiss}>放弃这条更正</button>
  </article>;
}

export function ChunkEditor({ doc, onLocate }: { doc: Doc; onLocate: (chunk: Chunk) => void }) {
  const [data, setData] = useState<DocumentChunks | null>(null), [error, setError] = useState('');
  const [filter, setFilter] = useState<Filter>('all'), [query, setQuery] = useState('');
  const [editing, setEditing] = useState<string | null>(null), [draft, setDraft] = useState(''), [busy, setBusy] = useState(false);
  const editable = doc.status === 'indexed';
  const base = `/documents/${doc.document_id}`;
  async function load() {
    try { setData(await request<DocumentChunks>(`${base}/chunks`)); setError(''); }
    catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { void load(); }, [doc.document_id, doc.status]);
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); await load(); return true; }
    catch (e) { setError((e as Error).message); return false; }
    finally { setBusy(false); }
  }
  const save = (chunk: Chunk, body: { text?: string | null; excluded?: boolean }) => run(() => request(`${base}/chunks/${chunk.chunk_id}`, json('PATCH', body)));
  if (!data) return <div className="chunk-stage">{error ? <p className="error" role="alert">{error}</p> : <p className="placeholder">正在加载分块…</p>}</div>;
  const counts = { all: data.chunks.length, edited: data.chunks.filter(c => c.edited).length, excluded: data.chunks.filter(c => c.excluded).length };
  const needle = query.trim().toLowerCase();
  const shown = data.chunks.filter(c => (filter === 'all' || (filter === 'edited' ? c.edited : c.excluded)) && (!needle || c.content.toLowerCase().includes(needle)));
  const byId = new Map(data.chunks.map(c => [c.chunk_id, c]));
  return <div className="chunk-stage">
    <div className="chunk-toolbar">
      <div className="mode-toggle" role="group" aria-label="分块筛选">{(['all', 'edited', 'excluded'] as Filter[]).map(f => <button key={f} type="button" className={`btn ${filter === f ? 'is-active' : ''}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>{{ all: '全部', edited: '已编辑', excluded: '已排除' }[f]} {counts[f]}</button>)}</div>
      <label className="sr-only" htmlFor="chunk-filter">筛选分块</label>
      <input id="chunk-filter" className="input" placeholder="筛选分块内容" value={query} onChange={e => setQuery(e.target.value)}/>
    </div>
    {!editable && <p className="notice">文档处理中，完成后才能编辑分块。</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {!!data.orphaned.length && <section className="orphans" aria-label="待处理的更正">
      <h3>{data.orphaned.length} 条更正需要处理</h3>
      <p className="notice">重新处理后，这些更正对应的原文变了，没有自动套用。可以应用到新的分块，或者放弃。</p>
      {data.orphaned.map(o => <Orphan key={o.correction_id} orphan={o} chunks={byId} busy={busy || !editable}
        onAttach={chunk => void run(() => request(`${base}/corrections/${o.correction_id}/attach`, json('POST', { chunk_id: chunk })))}
        onDismiss={() => { if (window.confirm('放弃这条更正？')) void run(() => request(`${base}/corrections/${o.correction_id}`, json('DELETE'))); }}/>)}
    </section>}
    <ol className="chunk-list">{shown.map(c => <li key={c.chunk_id} className={`panel-inset chunk-card${c.excluded ? ' is-excluded' : ''}`}>
      <div className="chunk-meta"><span className="source-number">{c.position}</span><span>{location(c) || '—'}</span>
        {c.kind === 'table' && <span className="format-tag">表格{c.parts ? ` ${c.part}/${c.parts}` : ''}</span>}
        {c.edited && <span className="format-tag tag-edited">已编辑</span>}{c.excluded && <span className="format-tag tag-excluded">已排除</span>}</div>
      {editing === c.chunk_id
        ? <form onSubmit={async e => { e.preventDefault(); if (await save(c, { text: draft })) setEditing(null); }}>
            <label className="sr-only" htmlFor={`edit-${c.chunk_id}`}>分块内容</label>
            <textarea id={`edit-${c.chunk_id}`} className="input" rows={Math.min(16, Math.max(4, draft.split('\n').length + 1))} value={draft} onChange={e => setDraft(e.target.value)}/>
            <div className="chunk-actions"><button className="btn btn-ok" disabled={busy || !draft.trim()}>保存</button><button type="button" className="btn" onClick={() => setEditing(null)}>取消</button></div>
            {c.edited && <details><summary>原来生成的文本</summary><p className="chunk-text">{c.original}</p></details>}
          </form>
        : <>
            <p className="chunk-text">{c.content}</p>
            {c.edited && <details><summary>原来生成的文本</summary><p className="chunk-text">{c.original}</p></details>}
            <div className="chunk-actions">
              <button className="btn" disabled={busy || !editable} onClick={() => { setEditing(c.chunk_id); setDraft(c.content); }}>编辑</button>
              <button className="btn" disabled={busy || !editable} onClick={() => void save(c, { excluded: !c.excluded })}>{c.excluded ? '恢复检索' : '排除'}</button>
              {c.edited && <button className="btn" disabled={busy || !editable} onClick={() => void save(c, { text: null })}>恢复原文</button>}
              <button className="btn text-button" onClick={() => onLocate(c)}>定位原文 ↗</button>
            </div>
          </>}
    </li>)}</ol>
    {!shown.length && <p className="placeholder">没有符合条件的分块。</p>}
  </div>;
}
