import React, { useState } from 'react';
import { hintText, request, sourcePage, type ClaimCheck, type Message, type Source, type VerificationReport, type VerificationStatus } from './api';

const statusNames: Record<VerificationStatus, string> = { supported: '原文支持', conflicting: '与原文冲突', insufficient_evidence: '证据不足', unverified: '未核查' };
const noteText: Record<string, string> = {
  quote_not_found: '模型给出的引文在原文中找不到，没有采纳。',
  no_verified_quote: '没有能在所引原文中找到的引文，不算作支持或冲突。',
  uncited: '回答没有为这句话标注来源。',
  source_removed: '部分来源所在的文档已删除。',
  not_judged: '模型没有判断这一句。',
};

export function LocalChecks({ checks }: { checks: ClaimCheck[] }) {
  if (!checks.length) return null;
  return <details className="local-checks"><summary>自动检查 · {checks.length} 处提示</summary><ul>
    {checks.map(c => <li key={c.claim}><q>{c.text}</q>{c.hints.map(h => <span key={h} className="check-hint">{hintText(h)}</span>)}</li>)}
  </ul></details>;
}

export function VerificationPanel({ conversationId, message, onOpenSource, canOpen }: { conversationId: string; message: Message; onOpenSource: (source: Source, page?: number) => void; canOpen: boolean }) {
  const [report, setReport] = useState<VerificationReport | null>(message.verification ?? null);
  const [running, setRunning] = useState(false), [error, setError] = useState('');
  const sources = new Map((message.sources ?? []).map((s, i) => [s.citation ?? i + 1, s]));
  async function verify() {
    setRunning(true); setError('');
    try { setReport(await request<VerificationReport>(`/conversations/${conversationId}/messages/${message.message_id}/verification`, { method: 'POST' })); }
    catch (e) { setError((e as Error).message); }
    finally { setRunning(false); }
  }
  const open = (citation: number) => { const s = sources.get(citation); if (s) onOpenSource(s, sourcePage(s)); };
  return <section className="verification" aria-label="引用核查">
    <div className="verification-head">
      <button className="btn" disabled={running} onClick={() => void verify()}>{running ? '正在核查…' : report ? '重新核查' : '核查引用'}</button>
      {report && <span className="verification-summary">{(Object.keys(statusNames) as VerificationStatus[]).filter(s => report.summary[s]).map(s => <span key={s} className={`verdict verdict-${s}`}>{statusNames[s]} {report.summary[s]}</span>)}</span>}
    </div>
    {error && <p className="error" role="alert">核查失败：{error}</p>}
    {report && <>
      {!report.claims.length && <p className="notice">回答中没有来自文档的说法需要核查。</p>}
      <ol className="verified-claims">{report.claims.map(c => <li key={c.index} className={`verified verified-${c.status}`}>
        <div><span className={`verdict verdict-${c.status}`}>{statusNames[c.status]}</span> <span className="claim-text">{c.text}</span></div>
        {c.reason && <p className="claim-reason">{c.reason}</p>}
        {c.evidence.map((e, i) => <blockquote key={i} className="claim-evidence"><span>{e.quote}</span><button className="btn text-button" disabled={!canOpen || !sources.has(e.citation)} onClick={() => open(e.citation)}>[{e.citation}] {e.document_name || '来源'}{e.page_range ? ` · 第 ${e.page_range[0]} 页` : ''} ↗</button></blockquote>)}
        {!!c.suggested_citations.length && <p className="claim-note">所引来源不支持；来源 {c.suggested_citations.map(n => `[${n}]`).join(' ')} 中有相关原文。</p>}
        {c.notes.filter(n => noteText[n]).map(n => <p key={n} className="claim-note">{noteText[n]}</p>)}
        {c.hints.map(h => <p key={h} className="claim-note">{hintText(h)}</p>)}
      </li>)}</ol>
      <p className="fine verification-limits">核查由模型完成{report.model ? `（${report.model}）` : ''}，只判断所引原文是否支持这句话，不代表结论本身正确，也没有对照其他独立来源。{report.unchecked_segments ? '标为“模型补充”的内容不在核查范围内。' : ''}</p>
    </>}
  </section>;
}
