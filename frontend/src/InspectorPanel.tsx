import React, { useState } from 'react';
import { methodNames, postJson, sourcePage, type DebugResponse, type RetrievalMethod, type Scope, type Source } from './api';
import { ScoreBadges } from './ScoreBadges';

const stageNames: Record<string, string> = { scope: '范围检查', dense: '语义召回', bm25: '关键词召回', fusion: '融合与组装', total: '总耗时' };
const configNames: Record<string, string> = { rrf_k: 'RRF k', candidate_k: '每路候选数', embedding_model: '向量模型', tokenizer: '分词器' };

function Column({ title, items, both, onOpen }: { title: string; items: Source[]; both: Set<string>; onOpen: (s: Source, p?: number) => void }) {
  return <section className="inspector-column" aria-label={title}>
    <h3>{title} <span>{items.length}</span></h3>
    {!items.length ? <p className="notice">没有候选结果。</p> : <ol>{items.map(s => {
      const p = sourcePage(s);
      return <li key={s.chunk_id} className={`panel-inset ${both.has(s.chunk_id) ? 'in-both' : ''}`}>
        <div className="search-hit-meta"><span className="source-number">{s.rank}</span><span className="source-name">{s.document_name || '文档'}</span><span>{p ? `p.${p}` : '—'}</span>{both.has(s.chunk_id) && <span className="both-tag">两路命中</span>}</div>
        <p>{s.content.length > 160 ? `${s.content.slice(0, 160)}…` : s.content}</p>
        <ScoreBadges source={s}/>
        <button className="btn text-button" onClick={() => onOpen(s, p)}>打开 ↗</button>
      </li>;
    })}</ol>}
  </section>;
}

/** Retrieval Inspector (System Design §16.3): every stage of one query, side by side. */
export function InspectorPanel({ scope, scopeName, onOpen }: { scope: Scope; scopeName: string; onOpen: (source: Source, page?: number) => void }) {
  const [query, setQuery] = useState(''), [method, setMethod] = useState<RetrievalMethod>('hybrid'), [topK, setTopK] = useState(10);
  const [data, setData] = useState<DebugResponse | null>(null), [running, setRunning] = useState(false), [error, setError] = useState('');
  async function run(event: React.FormEvent) {
    event.preventDefault();
    if (!query.trim() || running) return;
    setRunning(true); setError('');
    try { setData(await postJson<DebugResponse>('/search/debug', { ...scope, query: query.trim(), top_k: topK, method })); }
    catch (e) { setError((e as Error).message); }
    finally { setRunning(false); }
  }
  const denseIds = new Set(data?.dense.map(s => s.chunk_id)), both = new Set(data?.bm25.map(s => s.chunk_id).filter(id => denseIds.has(id)));
  return <section className="inspector" aria-label="检索调试">
    <form className="search-form" onSubmit={run}>
      <label className="sr-only" htmlFor="inspect-query">调试查询</label>
      <input id="inspect-query" className="input" value={query} onChange={e => setQuery(e.target.value)} placeholder={`在「${scopeName}」中调试一次检索`} maxLength={1000}/>
      <label className="sr-only" htmlFor="inspect-method">检索方式</label>
      <select id="inspect-method" value={method} onChange={e => setMethod(e.target.value as RetrievalMethod)}>{(Object.keys(methodNames) as RetrievalMethod[]).map(m => <option key={m} value={m}>{methodNames[m]}</option>)}</select>
      <label className="sr-only" htmlFor="inspect-topk">返回条数</label>
      <select id="inspect-topk" value={topK} onChange={e => setTopK(Number(e.target.value))}>{[5, 10, 20].map(n => <option key={n} value={n}>Top {n}</option>)}</select>
      <button className="btn btn-ok" disabled={running || !query.trim()}>{running ? '运行中…' : '运行'}</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {data && <>
      <div className="inspector-summary panel-inset">
        <div><span className="eyebrow">分词结果</span><p>{data.query_terms.length ? data.query_terms.map(t => <code key={t}>{t}</code>) : '—'}</p></div>
        <div><span className="eyebrow">各阶段耗时（毫秒）</span><p>{Object.entries(data.timings_ms).map(([k, v]) => <span key={k} className="timing">{stageNames[k] || k} {v}</span>)}</p></div>
        <div><span className="eyebrow">配置</span><p>{Object.entries(data.config).map(([k, v]) => <span key={k} className="timing">{configNames[k] || k}：{v}</span>)}</p></div>
        <div><span className="eyebrow">范围</span><p>{data.document_ids.length} 份文档参与检索{data.skipped_document_ids.length ? `，${data.skipped_document_ids.length} 份尚未处理完成` : ''}</p></div>
      </div>
      <div className={`inspector-columns ${data.method === 'hybrid' ? '' : 'single'}`}>
        {data.method !== 'bm25' && <Column title="语义候选" items={data.dense} both={both} onOpen={onOpen}/>}
        {data.method !== 'dense' && <Column title="关键词候选" items={data.bm25} both={both} onOpen={onOpen}/>}
        {data.method === 'hybrid' && <Column title="RRF 融合结果" items={data.results} both={both} onOpen={onOpen}/>}
      </div>
    </>}
  </section>;
}
