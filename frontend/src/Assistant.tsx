import React, { useEffect, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ScoreBadges } from './ScoreBadges';
import {
  request, sameScope, sourcePage, splitSegments, streamEvents,
  type AnswerMode, type Conversation, type ConversationDetail, type Message, type Scope, type Segment, type Selection, type Source,
} from './api';

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
  onOpenSource: (source: Source, page?: number) => void;
  canOpen?: boolean;
};

const segmentLabels: Record<string, string> = { model: '模型补充 · 非文档内容，请自行核实', unlabeled: '未区分来源' };
const markdown = (text: string) => <Markdown remarkPlugins={[remarkGfm]} components={{ img: ({ alt }) => <span>{alt || '图片'}</span> }}>{text}</Markdown>;

function segmentsOf(m: Message): Segment[] {
  if (m.status === 'streaming') return splitSegments(m.content, m.mode ?? 'grounded');
  if (m.segments?.length) return m.segments;
  return m.content ? splitSegments(m.content, m.mode ?? 'grounded') : [];
}

function AnswerBody({ message }: { message: Message }) {
  return <div className="answer">
    {segmentsOf(message).map((s, i) => s.source_type === 'library'
      ? <div key={i}>{markdown(s.text)}</div>
      : <section key={i} className={`segment segment-${s.source_type}`}><div className="segment-label">{segmentLabels[s.source_type]}</div>{markdown(s.text)}</section>)}
    {!!message.invalid_citations?.length && <p className="notice citation-warning">回答引用了不存在的来源编号：{message.invalid_citations.map(n => `[${n}]`).join(' ')}</p>}
  </div>;
}

function AssistantMessage({ message: m, question, fallbackName, canOpen, onOpenSource }: { message: Message; question?: Message; fallbackName?: string; canOpen: boolean; onOpenSource: Props['onOpenSource'] }) {
  const sources = m.sources ?? [];
  const rewritten = m.search_query && question && !m.search_query.startsWith(question.content);
  return <>
    <div className="answer-label">✧ AVIATLAS{m.mode === 'open' ? ' · 允许模型补充' : ''}</div>
    {rewritten && <p className="search-query">检索：{m.search_query}</p>}
    {m.status === 'streaming' && !m.content && <p className="thinking">✧ {m.search_query ? '正在生成回答…' : '正在检索文档…'}</p>}
    <AnswerBody message={m}/>
    {m.status === 'stopped' && <p className="notice">已停止生成。</p>}
    {m.status === 'failed' && <p className="error">回答失败：{m.error || '请重试。'}</p>}
    {!!m.skipped_document_ids?.length && <p className="notice">有 {m.skipped_document_ids.length} 份文档尚未处理完成，未参与检索。</p>}
    {!!m.removed_source_count && <p className="notice">{m.removed_source_count} 条来源所在的文档已删除。</p>}
    {!!sources.length && <div className="sources"><div className="eyebrow">原文依据 · {sources.length}</div>{sources.map((s, index) => {
      const p = sourcePage(s);
      return <details className="panel-inset" key={`${s.chunk_id}-${index}`}><summary><span className="source-number">{s.citation ?? index + 1}</span><span className="source-name">{s.document_name || fallbackName || '文档'}</span><span>{p ? `第 ${p} 页` : '无页码'}</span></summary>{s.section_title && <p className="source-section">§ {s.section_title}</p>}<p>{s.content}</p><ScoreBadges source={s}/>{p ? <button className="btn text-button" disabled={!canOpen} onClick={() => onOpenSource(s, p)}>查看第 {p} 页 ↗</button> : !!s.block_ids?.length && <button className="btn text-button" disabled={!canOpen} onClick={() => onOpenSource(s)}>定位原文 ↗</button>}</details>;
    })}</div>}
  </>;
}

export function Assistant({ scope, title, subtitle, status, disabledNotice, placeholder, suggestions, fallbackName, selection, onClearSelection, onOpenSource, canOpen = true }: Props) {
  const [question, setQuestion] = useState(''), [mode, setMode] = useState<AnswerMode>('grounded');
  const [conversations, setConversations] = useState<Conversation[]>([]), [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]), [loadingHistory, setLoadingHistory] = useState(false);
  const [asking, setAsking] = useState(false), [error, setError] = useState('');
  const bottom = useRef<HTMLDivElement>(null), alive = useRef(true), abort = useRef<AbortController | null>(null), loadToken = useRef(0);
  const disabled = !!disabledNotice, scopeKey = JSON.stringify(scope);

  useEffect(() => { alive.current = true; return () => { alive.current = false; abort.current?.abort(); }; }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [messages, asking]);

  async function listConversations() {
    const { items } = await request<{ items: Conversation[] }>('/conversations');
    return items.filter(c => sameScope(c, scope));
  }
  async function open(id: string | null) {
    const token = ++loadToken.current;
    setActiveId(id); setMessages([]); setError('');
    if (!id) return;
    setLoadingHistory(true);
    try {
      const detail = await request<ConversationDetail>(`/conversations/${id}`);
      if (alive.current && token === loadToken.current) setMessages(detail.messages);
    } catch (e) { if (alive.current && token === loadToken.current) setError((e as Error).message); }
    finally { if (alive.current && token === loadToken.current) setLoadingHistory(false); }
  }
  useEffect(() => {
    listConversations().then(items => {
      if (!alive.current) return;
      setConversations(items);
      void open(items[0]?.conversation_id ?? null);
    }).catch(() => { if (alive.current) setError('无法加载对话记录。'); });
  }, [scopeKey]);

  const patch = (id: string, change: (m: Message) => Message) =>
    setMessages(list => list.map(m => m.message_id === id ? change(m) : m));

  async function ask() {
    if (!question.trim() || asking || disabled) return;
    const text = question.trim(), quoted = selection ?? undefined;
    setAsking(true); setError('');
    let assistantId: string | null = null;
    const controller = new AbortController();
    abort.current = controller;
    try {
      let id = activeId;
      if (!id) {
        const created = await request<Conversation>('/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(scope) });
        id = created.conversation_id;
        loadToken.current++;
        if (alive.current) { setConversations(list => [created, ...list]); setActiveId(id); }
      }
      await streamEvents(`/conversations/${id}/messages`, { question: text, mode, top_k: 5, ...(quoted ? { selection: quoted } : {}) }, (name, data) => {
        if (!alive.current) return;
        if (name === 'started') {
          assistantId = data.assistant.message_id;
          setMessages(list => [...list, data.user, data.assistant]);
          setQuestion('');
          if (quoted) onClearSelection?.();
        } else if (assistantId && name === 'retrieval') {
          patch(assistantId, m => ({ ...m, search_query: data.search_query, sources: data.sources, skipped_document_ids: data.skipped_document_ids }));
        } else if (assistantId && name === 'delta') {
          patch(assistantId, m => ({ ...m, content: m.content + data.text }));
        } else if (assistantId && (name === 'done' || name === 'error')) {
          if (data.message) patch(assistantId, () => data.message);
          if (name === 'error') setError(data.detail);
        }
      }, controller.signal);
    } catch (e) {
      if (!alive.current) return;
      if ((e as Error).name === 'AbortError') { if (assistantId) patch(assistantId, m => ({ ...m, status: 'stopped' })); }
      else setError((e as Error).message);
    } finally {
      abort.current = null;
      if (alive.current) {
        setAsking(false);
        listConversations().then(items => { if (alive.current) setConversations(items); }).catch(() => undefined);
      }
    }
  }

  async function remove() {
    if (!activeId || !window.confirm('删除这段对话？删除后无法恢复。')) return;
    try {
      await request(`/conversations/${activeId}`, { method: 'DELETE' });
      const rest = conversations.filter(c => c.conversation_id !== activeId);
      setConversations(rest);
      void open(rest[0]?.conversation_id ?? null);
    } catch (e) { setError((e as Error).message); }
  }

  return <aside className="assistant">
    <div className="assistant-title"><div><h2>{title}</h2><p>{subtitle}</p></div>{status}</div>
    <div className="conversation-bar">
      <label className="sr-only" htmlFor="conversation-select">对话</label>
      <select id="conversation-select" value={activeId ?? ''} disabled={asking} onChange={e => void open(e.target.value || null)}>
        {!activeId && <option value="">新对话</option>}
        {conversations.map(c => <option key={c.conversation_id} value={c.conversation_id}>{c.title || '未命名对话'}</option>)}
      </select>
      <button type="button" className="btn" disabled={asking || !activeId} onClick={() => void open(null)}>新对话</button>
      <button type="button" className="btn btn-warn" disabled={asking || !activeId} onClick={() => void remove()} aria-label="删除对话">删除</button>
    </div>
    <div className="mode-toggle" role="group" aria-label="回答方式">
      <button type="button" className={`btn ${mode === 'grounded' ? 'is-active' : ''}`} aria-pressed={mode === 'grounded'} onClick={() => setMode('grounded')}>仅依据文档</button>
      <button type="button" className={`btn ${mode === 'open' ? 'is-active' : ''}`} aria-pressed={mode === 'open'} onClick={() => setMode('open')}>允许模型补充</button>
    </div>
    <div className="conversation" aria-live="polite">
      {loadingHistory && <p className="thinking">正在加载对话…</p>}
      {!messages.length && !asking && !loadingHistory && <div className="welcome"><h3>让阅读多一点启发</h3><p>询问概念、方法或细节，可以接着追问。<br/>回答附带来源，方便回到原文核对。</p><div className="suggestions">{suggestions.map(q => <button className="btn" key={q} disabled={disabled} onClick={() => setQuestion(q)}>{q}<span>↗</span></button>)}</div></div>}
      {messages.map((m, i) => m.role === 'user'
        ? <article className="exchange question-turn" key={m.message_id}>
            {m.selection && <blockquote className="quoted panel-inset">{m.selection.text}</blockquote>}
            <div className="question panel-inset">{m.content}</div>
          </article>
        : <article className="exchange" key={m.message_id}>
            <AssistantMessage message={m} question={messages[i - 1]} fallbackName={fallbackName} canOpen={canOpen} onOpenSource={onOpenSource}/>
          </article>)}
      {asking && !messages.some(m => m.status === 'streaming') && <p className="thinking">✧ 正在检索文档并生成回答…</p>}<div ref={bottom}/>
    </div>
    <form className="composer" onSubmit={event => { event.preventDefault(); void ask(); }}>
      {disabledNotice && <p className="notice">{disabledNotice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {selection && <div className="selection-chip panel-inset"><div><span className="eyebrow">选中的原文{selection.page_number ? ` · 第 ${selection.page_number} 页` : ''}</span><p>{selection.text}</p></div><div className="selection-actions">{!question && <button type="button" className="btn text-button" onClick={() => setQuestion('请解释这段内容。')}>解释这段</button>}<button type="button" className="btn text-button" aria-label="取消引用选中内容" onClick={onClearSelection}>×</button></div></div>}
      <label className="sr-only" htmlFor="question">问题</label>
      <textarea className="input" id="question" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void ask(); }
      }} placeholder={messages.length ? '继续追问…' : placeholder} disabled={asking || disabled} rows={3}/>
      <div className="composer-footer"><span>Enter 发送 · Shift+Enter 换行</span>
        {asking
          ? <button className="btn btn-warn" type="button" onClick={() => abort.current?.abort()}>停止 ■</button>
          : <button className="btn btn-ok primary" type="button" onClick={() => void ask()} disabled={!question.trim() || disabled}>发送 ↑</button>}
      </div>
      <p className="fine">{mode === 'open' ? '模型补充的内容不来自你的文档，已单独标注。' : 'AI 回答可能存在错误，请结合原文核对。'}</p>
    </form>
  </aside>;
}
