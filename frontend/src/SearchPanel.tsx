import React, { useState } from 'react';
import { methodNames, postJson, sourcePage, type RetrievalMethod, type Scope, type SearchResponse, type Source } from './api';
import { ScoreBadges } from './ScoreBadges';

const excerpt = (text: string, limit = 320) => text.length > limit ? `${text.slice(0, limit)}…` : text;

export function SearchPanel({ scope, scopeName, onOpen }: { scope: Scope; scopeName: string; onOpen: (source: Source, page?: number) => void }) {
  const [query, setQuery] = useState(''), [data, setData] = useState<(SearchResponse & { method: RetrievalMethod }) | null>(null);
  const [searching, setSearching] = useState(false), [error, setError] = useState('');
  const [method, setMethod] = useState<RetrievalMethod>('hybrid');
  async function search(event: React.FormEvent) {
    event.preventDefault();
    if (!query.trim() || searching) return;
    setSearching(true); setError('');
    try { setData({ ...await postJson<SearchResponse>('/search', { ...scope, query: query.trim(), top_k: 10, method }), method }); }
    catch (e) { setError((e as Error).message); }
    finally { setSearching(false); }
  }
  return <section className="search-panel" aria-label="搜索文档内容">
    <form className="search-form" onSubmit={search} role="search">
      <label className="sr-only" htmlFor="search-query">搜索内容</label>
      <input id="search-query" className="input" value={query} onChange={e => setQuery(e.target.value)} placeholder={`在「${scopeName}」中搜索概念、术语或句子`} maxLength={1000}/>
      <label className="sr-only" htmlFor="search-method">检索方式</label>
      <select id="search-method" value={method} onChange={e => setMethod(e.target.value as RetrievalMethod)}>{(Object.keys(methodNames) as RetrievalMethod[]).map(m => <option key={m} value={m}>{methodNames[m]}</option>)}</select>
      <button className="btn btn-ok" disabled={searching || !query.trim()}>{searching ? '搜索中…' : '搜索'}</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {data && <>
      <div className="library-toolbar"><span>“{data.query}” · {data.results.length} 条结果 · {data.document_ids.length} 份文档</span><span>{methodNames[data.method]}{data.method === 'hybrid' ? ' · 语义与关键词融合排序' : ''}</span></div>
      {!!data.skipped_document_ids.length && <p className="notice">有 {data.skipped_document_ids.length} 份文档尚未处理完成，未参与搜索。</p>}
      {!data.results.length ? <div className="panel-inset empty"><h3>没有找到相关内容</h3><p>{data.document_ids.length ? '换一种说法，或扩大搜索范围。' : '范围内还没有处理完成的文档。'}</p></div>
        : <ol className="search-results">{data.results.map(r => {
          const p = sourcePage(r);
          return <li className="panel-inset search-hit" key={r.chunk_id}>
            <div className="search-hit-meta"><span className="source-number">{r.rank}</span><span className="source-name">{r.document_name || '文档'}</span><span>{p ? `第 ${p} 页` : '无页码'}</span></div>
            <p>{excerpt(r.content)}</p>
            <ScoreBadges source={r}/>
            <button className="btn text-button" onClick={() => onOpen(r, p)}>打开原文{p ? `第 ${p} 页` : ''} ↗</button>
          </li>;
        })}</ol>}
    </>}
  </section>;
}
