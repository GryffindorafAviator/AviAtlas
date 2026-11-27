import React, { useEffect, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import { ScoreBadges } from './ScoreBadges';
import { postJson, sourcePage, type Answer, type AnswerMode, type Scope, type Segment, type Selection, type Source } from './api';

type Message = { question: string; selection?: Selection; result: Answer };
type Props = {
  scope: Scope;
  title: string;
  subtitle: string;
  status?: React.ReactNode;
  disabledNotice?: string;
  placeholder: string;
  suggestions: string[];
  fallbackName?: string;
  selection?: Selection | null;
  onClearSelection?: () => void;
  onOpenSource: (source: Source, page: number) => void;
  canOpen?: boolean;
};

const segmentLabels: Record<string, string> = { model: '模型补充 · 非文档内容，请自行核实', unlabeled: '未区分来源' };
const markdown = (text: string) => <Markdown components={{ img: ({ alt }) => <span>{alt || '图片'}</span> }}>{text}</Markdown>;

function AnswerBody({ result }: { result: Answer }) {
  const segments: Segment[] = result.segments?.length ? result.segments : [{ source_type: 'library', text: result.answer }];
  return <div className="answer">
    {segments.map((s, i) => s.source_type === 'library'
      ? <div key={i}>{markdown(s.text)}</div>
      : <section key={i} className={`segment segment-${s.source_type}`}><div className="segment-label">{segmentLabels[s.source_type]}</div>{markdown(s.text)}</section>)}
    {!!result.invalid_citations?.length && <p className="notice citation-warning">回答引用了不存在的来源编号：{result.invalid_citations.map(n => `[${n}]`).join(' ')}</p>}
  </div>;
}

export function Assistant({ scope, title, subtitle, status, disabledNotice, placeholder, suggestions, fallbackName, selection, onClearSelection, onOpenSource, canOpen = true }: Props) {
  const [question, setQuestion] = useState(''), [mode, setMode] = useState<AnswerMode>('grounded');
  const [messages, setMessages] = useState<Message[]>([]);
  const [asking, setAsking] = useState(false), [error, setError] = useState('');
  const bottom = useRef<HTMLDivElement>(null), alive = useRef(true);
  const disabled = !!disabledNotice;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [messages, asking]);
  async function ask() {
    if (!question.trim() || asking || disabled) return;
    const text = question.trim(), quoted = selection ?? undefined;
    setAsking(true); setError('');
    try {
      const result = await postJson<Answer>('/ask', { ...scope, question: text, mode, top_k: 5, ...(quoted ? { selection: quoted } : {}) });
      if (alive.current) { setMessages(m => [...m, { question: text, selection: quoted, result }]); setQuestion(''); if (quoted) onClearSelection?.(); }
    } catch (e) { if (alive.current) setError((e as Error).message); }
    finally { if (alive.current) setAsking(false); }
  }
  return <aside className="assistant">
    <div className="assistant-title"><div><h2>{title}</h2><p>{subtitle}</p></div>{status}</div>
    <div className="mode-toggle" role="group" aria-label="回答方式">
      <button type="button" className={`btn ${mode === 'grounded' ? 'is-active' : ''}`} aria-pressed={mode === 'grounded'} onClick={() => setMode('grounded')}>仅依据文档</button>
      <button type="button" className={`btn ${mode === 'open' ? 'is-active' : ''}`} aria-pressed={mode === 'open'} onClick={() => setMode('open')}>允许模型补充</button>
    </div>
    <div className="conversation" aria-live="polite">
      {!messages.length && !asking && <div className="welcome"><h3>让阅读多一点启发</h3><p>询问概念、方法或细节。<br/>回答附带来源，方便回到原文核对。</p><div className="suggestions">{suggestions.map(q => <button className="btn" key={q} disabled={disabled} onClick={() => setQuestion(q)}>{q}<span>↗</span></button>)}</div></div>}
      {messages.map((m, i) => <article className="exchange" key={i}>
        {m.selection && <blockquote className="quoted panel-inset">{m.selection.text}</blockquote>}
        <div className="question panel-inset">{m.question}</div>
        <div className="answer-label">✧ AVIATLAS{m.result.mode === 'open' ? ' · 允许模型补充' : ''}</div>
        <AnswerBody result={m.result}/>
        {!!m.result.skipped_document_ids?.length && <p className="notice">有 {m.result.skipped_document_ids.length} 份文档尚未处理完成，未参与检索。</p>}
        {!!m.result.sources.length && <div className="sources"><div className="eyebrow">原文依据 · {m.result.sources.length}</div>{m.result.sources.map((s, index) => {
          const p = sourcePage(s);
          return <details className="panel-inset" key={s.chunk_id}><summary><span className="source-number">{index + 1}</span><span className="source-name">{s.document_name || fallbackName || '文档'}</span><span>{p ? `第 ${p} 页` : '无页码'}</span></summary><p>{s.content}</p><ScoreBadges source={s}/>{p && <button className="btn text-button" disabled={!canOpen} onClick={() => onOpenSource(s, p)}>查看第 {p} 页 ↗</button>}</details>;
        })}</div>}
      </article>)}
      {asking && <p className="thinking">✧ 正在检索文档并生成回答…</p>}<div ref={bottom}/>
    </div>
    <form className="composer" onSubmit={event => { event.preventDefault(); void ask(); }}>
      {disabledNotice && <p className="notice">{disabledNotice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {selection && <div className="selection-chip panel-inset"><div><span className="eyebrow">选中的原文{selection.page_number ? ` · 第 ${selection.page_number} 页` : ''}</span><p>{selection.text}</p></div><div className="selection-actions">{!question && <button type="button" className="btn text-button" onClick={() => setQuestion('请解释这段内容。')}>解释这段</button>}<button type="button" className="btn text-button" aria-label="取消引用选中内容" onClick={onClearSelection}>×</button></div></div>}
      <label className="sr-only" htmlFor="question">问题</label>
      <textarea className="input" id="question" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void ask(); }
      }} placeholder={placeholder} disabled={asking || disabled} rows={3}/>
      <div className="composer-footer"><span>Enter 发送 · Shift+Enter 换行</span><button className="btn btn-ok primary" type="button" onClick={() => void ask()} disabled={asking || !question.trim() || disabled}>{asking ? '发送中…' : '发送 ↑'}</button></div>
      <p className="fine">{mode === 'open' ? '模型补充的内容不来自你的文档，已单独标注。' : 'AI 回答可能存在错误，请结合原文核对。'}</p>
    </form>
  </aside>;
}
