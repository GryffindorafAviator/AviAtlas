import React, { useState } from 'react';
import { postJson, sourcePage, type Scope, type SearchResponse, type Source } from './api';

const excerpt = (text: string, limit = 320) => text.length > limit ? `${text.slice(0, limit)}…` : text;

export function SearchPanel({ scope, scopeName, onOpen }: { scope: Scope; scopeName: string; onOpen: (source: Source, page?: number) => void }) {
  const [query, setQuery] = useState(''), [data, setData] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false), [error, setError] = useState('');
  async function search(event: React.FormEvent) {
    event.preventDefault();
    if (!query.trim() || searching) return;
    setSearching(true); setError('');
    try { setData(await postJson<SearchResponse>('/search', { ...scope, query: query.trim(), top_k: 10 })); }
    catch (e) { setError((e as Error).message); }
    finally { setSearching(false); }
  }
  return <section className="search-panel" aria-label="搜索文档内容">
    <form className="search-form" onSubmit={search} role="search">
      <label className="sr-only" htmlFor="search-query">搜索内容</label>
      <input id="search-query" className="input" value={query} onChange={e => setQuery(e.target.value)} placeholder={`在「${scopeName}」中搜索概念、术语或句子`} maxLength={1000}/>
      <button className="btn btn-ok" disabled={searching || !query.trim()}>{searching ? '搜索中…' : '搜索'}</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {data && <>
      <div className="library-toolbar"><span>“{data.query}” · {data.results.length} 条结果 · {data.document_ids.length} 份文档</span><span>按语义相关度排序</span></div>
      {!!data.skipped_document_ids.length && <p className="notice">有 {data.skipped_document_ids.length} 份文档尚未处理完成，未参与搜索。</p>}
      {!data.results.length ? <div className="panel-inset empty"><h3>没有找到相关内容</h3><p>{data.document_ids.length ? '换一种说法，或扩大搜索范围。' : '范围内还没有处理完成的文档。'}</p></div>
        : <ol className="search-results">{data.results.map(r => {
          const p = sourcePage(r);
          return <li className="panel-inset search-hit" key={r.chunk_id}>
            <div className="search-hit-meta"><span className="source-number">{r.rank}</span><span className="source-name">{r.document_name || '文档'}</span><span>{p ? `第 ${p} 页` : '无页码'}</span>{typeof r.score === 'number' && <span title="语义相似度">相关度 {r.score.toFixed(2)}</span>}</div>
            <p>{excerpt(r.content)}</p>
            <button className="btn text-button" onClick={() => onOpen(r, p)}>打开原文{p ? `第 ${p} 页` : ''} ↗</button>
          </li>;
        })}</ol>}
    </>}
  </section>;
}
