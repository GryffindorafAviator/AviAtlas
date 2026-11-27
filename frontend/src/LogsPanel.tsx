import React, { useEffect, useState } from 'react';
import { ScoreBadges } from './ScoreBadges';
import { request, type LogDetail, type LogStatus, type LogSummary, type Source } from './api';

const statusNames: Record<LogStatus, string> = { complete: '完成', no_evidence: '无依据', stopped: '已停止', failed: '失败' };
const timingNames: Record<string, string> = { retrieval: '检索', first_token: '首字', generation: '生成', total: '总计' };
const PAGE = 50;
const ms = (value?: number) => value === undefined ? '—' : value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;

function Detail({ id, onOpen }: { id: string; onOpen: (source: Source, page?: number) => void }) {
  const [log, setLog] = useState<LogDetail | null>(null), [error, setError] = useState('');
  useEffect(() => { request<LogDetail>(`/logs/${id}`).then(setLog).catch(e => setError((e as Error).message)); }, [id]);
  if (error) return <p className="error">{error}</p>;
  if (!log) return <p className="placeholder">正在加载…</p>;
  return <div className="log-detail">
    <dl className="log-facts">
      {log.search_query && log.search_query !== log.question && <><dt>检索词</dt><dd>{log.search_query}</dd></>}
      <dt>模型</dt><dd>{log.model || '未配置'}</dd>
      <dt>耗时</dt><dd>{Object.entries(log.timings_ms).map(([k, v]) => `${timingNames[k] || k} ${ms(v)}`).join(' · ')}</dd>
      <dt>范围</dt><dd>{log.document_ids.length} 份文档{log.skipped_document_ids.length ? `，跳过 ${log.skipped_document_ids.length} 份未处理完成的` : ''}</dd>
      {!!log.invalid_citations.length && <><dt>无效引用</dt><dd>{log.invalid_citations.map(n => `[${n}]`).join(' ')}</dd></>}
      {log.error && <><dt>错误</dt><dd className="error">{log.error}</dd></>}
    </dl>
    {log.answer && <><div className="eyebrow">回答</div><p className="chunk-text">{log.answer}</p></>}
    {!!log.sources.length && <><div className="eyebrow">检索到的来源 · {log.sources.length}</div><ol className="log-sources">{log.sources.map(s => {
      const page = s.page_range?.[0];
      const source = { chunk_id: s.chunk_id ?? '', content: '', page_range: s.page_range, provenance: [], document_id: s.document_id, document_name: s.document_name, scores: s.scores, ranks: s.ranks, section_title: s.section_title } as Source;
      return <li key={s.citation} className="panel-inset"><div className="search-hit-meta"><span className="source-number">{s.citation}</span><span className="source-name">{s.document_name || '已删除的文档'}</span><span>{page ? `第 ${page} 页` : s.section_title ? `§ ${s.section_title}` : ''}</span></div><ScoreBadges source={source}/>{s.document_id && <button className="btn text-button" onClick={() => onOpen(source, page)}>打开原文 ↗</button>}</li>;
    })}</ol></>}
  </div>;
}

export function LogsPanel({ onOpen }: { onOpen: (source: Source, page?: number) => void }) {
  const [items, setItems] = useState<LogSummary[]>([]), [total, setTotal] = useState(0), [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<LogStatus | ''>(''), [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState(''), [loading, setLoading] = useState(true), [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    request<{ items: LogSummary[]; total: number }>(`/logs?limit=${PAGE}&offset=${offset}${status ? `&status=${status}` : ''}`)
      .then(data => { if (active) { setItems(data.items); setTotal(data.total); setError(''); } })
      .catch(e => { if (active) setError((e as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [offset, status, reload]);
  async function clear() {
    if (!window.confirm('清空所有问答日志？对话记录不受影响。')) return;
    try { await request('/logs', { method: 'DELETE' }); setOffset(0); setOpen(null); setReload(n => n + 1); }
    catch (e) { setError((e as Error).message); }
  }
  return <section className="logs-panel" aria-label="问答日志">
    <div className="library-toolbar">
      <span>共 {total} 条 · 保留最近 2000 条</span>
      <div className="collection-actions">
        <label className="sr-only" htmlFor="log-status">状态</label>
        <select id="log-status" value={status} onChange={e => { setStatus(e.target.value as LogStatus | ''); setOffset(0); }}>
          <option value="">全部状态</option>{(Object.keys(statusNames) as LogStatus[]).map(s => <option key={s} value={s}>{statusNames[s]}</option>)}
        </select>
        <button className="btn" onClick={() => setReload(n => n + 1)}>刷新</button>
        <button className="btn btn-warn" disabled={!total} onClick={() => void clear()}>清空日志</button>
      </div>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {loading && !items.length ? <div className="panel-inset empty">正在加载日志…</div>
      : !items.length ? <div className="panel-inset empty"><h3>还没有问答日志</h3><p>在问答或阅读器里提问后，这里会记录每次检索和回答的情况。</p></div>
      : <ol className="log-list">{items.map(log => <li key={log.log_id} className={`panel-inset log-item log-${log.status}`}>
          <button className="log-row" aria-expanded={open === log.log_id} onClick={() => setOpen(open === log.log_id ? null : log.log_id)}>
            <span className="log-time">{new Date(log.created_at).toLocaleString('zh-CN', { hour12: false })}</span>
            <span className="log-question">{log.question}</span>
            <span className="format-tag">{log.kind === 'conversation' ? '对话' : '单次'}{log.mode === 'open' ? ' · 允许补充' : ''}</span>
            <span className={`status status-${log.status === 'complete' ? 'indexed' : log.status === 'failed' ? 'failed' : ''}`}>{statusNames[log.status]}</span>
            <span>{log.source_count} 来源</span><span>{ms(log.timings_ms.total)}</span>
          </button>
          {open === log.log_id && <Detail id={log.log_id} onOpen={onOpen}/>}
        </li>)}</ol>}
    {total > PAGE && <div className="pagination"><button className="btn" disabled={offset === 0} onClick={() => setOffset(n => n - PAGE)}>上一页</button><span>{offset / PAGE + 1} / {Math.ceil(total / PAGE)}</span><button className="btn" disabled={offset + PAGE >= total} onClick={() => setOffset(n => n + PAGE)}>下一页</button></div>}
  </section>;
}
