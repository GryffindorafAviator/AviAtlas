export type Doc = { document_id: string; filename: string; title: string | null; status: string; page_count: number | null; chunks_count: number; last_error: string | null; uploaded_at: string; collection_ids?: string[]; source_format?: SourceFormat; text_layer?: string | null; ocr_page_count?: number; needs_reprocess?: boolean; edited_chunks?: number; excluded_chunks?: number; pending_corrections?: number };
export type SourceFormat = 'pdf' | 'docx' | 'markdown' | 'text';
export const formatNames: Record<SourceFormat, string> = { pdf: 'PDF', docx: 'DOCX', markdown: 'MD', text: 'TXT' };
export const UPLOAD_ACCEPT = '.pdf,.docx,.md,.markdown,.txt';
export const isSupportedFile = (name: string) => /\.(pdf|docx|md|markdown|txt)$/i.test(name);
export type ContentBlock = { block_id: string; type: string; content: string; page_number: number | null };
export type Source = { chunk_id: string; content: string; page_range: [number, number] | null; provenance: { page_number: number }[]; document_id?: string | null; document_name?: string | null; score?: number; rank?: number | null; retrieval_method?: string | null; scores?: Record<string, number>; ranks?: Record<string, number>; matched_terms?: string[]; citation?: number; block_ids?: string[]; section_title?: string | null };
export type RetrievalMethod = 'hybrid' | 'dense' | 'bm25';
export const methodNames: Record<RetrievalMethod, string> = { hybrid: '混合检索', dense: '语义检索', bm25: '关键词检索' };
export type DebugResponse = { query: string; method: RetrievalMethod; query_terms: string[]; document_ids: string[]; skipped_document_ids: string[]; dense: Source[]; bm25: Source[]; results: Source[]; timings_ms: Record<string, number>; config: Record<string, string | number> };
export type SourceType = 'library' | 'model' | 'unlabeled';
export type AnswerMode = 'grounded' | 'open';
export type Segment = { source_type: SourceType; text: string };
export type Answer = { answer: string; sources: Source[]; mode?: AnswerMode; segments?: Segment[]; invalid_citations?: number[]; skipped_document_ids?: string[] };
export type Scope = { document_ids?: string[]; collection_id?: string };
export type Selection = { text: string; document_id?: string; page_number?: number };
export type SearchResponse = { query: string; results: Source[]; document_ids: string[]; skipped_document_ids: string[] };
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : `请求失败（${response.status}），请稍后重试。`);
  }
  return response.status === 204 ? undefined as T : response.json();
}
export const postJson = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const sourcePage = (s: Source) => s.page_range?.[0] ?? s.provenance[0]?.page_number;

export type Collection = { collection_id: string; name: string; document_count: number; created_at: string; updated_at: string };

export type MessageStatus = 'complete' | 'streaming' | 'failed' | 'stopped';
export type Message = {
  message_id: string; role: 'user' | 'assistant'; content: string; status: MessageStatus; created_at: string;
  mode?: AnswerMode | null; selection?: Selection | null; segments?: Segment[]; sources?: Source[]; invalid_citations?: number[];
  search_query?: string | null; removed_source_count?: number; skipped_document_ids?: string[]; error?: string | null;
  checks?: ClaimCheck[]; verification?: VerificationReport | null;
};
export type Conversation = { conversation_id: string; title: string | null; document_ids: string[] | null; collection_id: string | null; created_at: string; updated_at: string; message_count: number };
export type ConversationDetail = Conversation & { messages: Message[] };

export function sameScope(c: Conversation, s: Scope) {
  if (s.document_ids) return !!c.document_ids && c.document_ids.length === s.document_ids.length && s.document_ids.every(id => c.document_ids!.includes(id));
  return !c.document_ids && (c.collection_id ?? null) === (s.collection_id ?? null);
}

/** POST and read Server-Sent Events until the stream ends. Errors before the stream starts throw like `request`. */
export async function streamEvents(path: string, body: unknown, onEvent: (name: string, data: any) => void, signal?: AbortSignal) {
  const response = await fetch(`/api/v1${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(body), signal });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : `请求失败（${response.status}），请稍后重试。`);
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '';
  const flush = (block: string) => {
    let name = 'message', data = '';
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    if (data) onEvent(name, JSON.parse(data));
  };
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let end;
    while ((end = buffer.indexOf('\n\n')) >= 0) { flush(buffer.slice(0, end)); buffer = buffer.slice(end + 2); }
    if (done) break;
  }
  if (buffer.trim()) flush(buffer);
}

const MARKER = /^[ \t]*\[(LIBRARY|MODEL)\][ \t]*:?[ \t]*/gm;
/** Client-side twin of answering/segments.py, for text that is still streaming. */
export function splitSegments(raw: string, mode: AnswerMode = 'grounded'): Segment[] {
  const text = raw.replace(/\[[A-Z]*$/, '');  // hide a marker that has only partly arrived
  const parts = text.split(MARKER), preamble = parts[0].trim(), out: Segment[] = [];
  if (parts.length === 1) return preamble ? [{ source_type: mode === 'grounded' ? 'library' : 'unlabeled', text: preamble }] : [];
  if (preamble) out.push({ source_type: 'unlabeled', text: preamble });
  for (let i = 1; i < parts.length; i += 2) {
    const body = parts[i + 1]?.trim(), type = parts[i] === 'LIBRARY' ? 'library' : 'model';
    if (!body) continue;
    const last = out[out.length - 1];
    if (last?.source_type === type) last.text += `\n\n${body}`; else out.push({ source_type: type, text: body });
  }
  return out;
}

export type Chunk = {
  chunk_id: string; position: number; content: string; original: string; edited: boolean; excluded: boolean;
  correction_id: string | null; block_ids: string[]; page_range: [number, number] | null; section_title: string | null;
  kind: string; part: number | null; parts: number | null;
};
export type OrphanedCorrection = { correction_id: string; original_text: string; text: string | null; excluded: boolean; context: { section_title?: string | null; page_range?: [number, number] | null }; candidate_chunk_ids: string[]; updated_at: string };
export type DocumentChunks = { document_id: string; status: string; chunks: Chunk[]; orphaned: OrphanedCorrection[] };
export type LogStatus = 'complete' | 'no_evidence' | 'stopped' | 'failed';
export type LogSummary = { log_id: string; created_at: string; kind: 'ask' | 'conversation'; question: string; mode: string; status: LogStatus; source_count: number; model: string | null; timings_ms: Record<string, number>; error: string | null };
export type LogSource = { citation: number; document_id: string | null; document_name: string | null; chunk_id: string | null; page_range: [number, number] | null; section_title: string | null; rank: number | null; retrieval_method: string | null; scores: Record<string, number>; ranks: Record<string, number> };
export type LogDetail = LogSummary & { search_query: string | null; scope: Record<string, unknown>; document_ids: string[]; skipped_document_ids: string[]; sources: LogSource[]; answer: string; segments: Segment[]; invalid_citations: number[]; conversation_id: string | null };
/** A document reprocessing in the background still serves its previous index. */
export const canAsk = (d: Doc) => d.status === 'indexed' || (['queued', 'parsing', 'indexing'].includes(d.status) && d.chunks_count > 0);

export type ClaimCheck = { claim: number; text: string; hints: string[] };
export type VerificationStatus = 'supported' | 'conflicting' | 'insufficient_evidence' | 'unverified';
export type VerifiedClaim = {
  index: number; text: string; source_type: string; citations: number[]; hints: string[]; status: VerificationStatus;
  model_verdict: string | null; reason: string | null; notes: string[]; suggested_citations: number[];
  evidence: { citation: number; quote: string; document_name: string | null; page_range: [number, number] | null }[];
};
export type VerificationReport = {
  verification_id: string; message_id: string; method: string; model: string | null; created_at: string;
  claims: VerifiedClaim[]; summary: Record<VerificationStatus, number>; unchecked_segments: number; limitations: string[];
};
/** Readable text for a local check hint such as `number_not_in_source:41.0`. */
export function hintText(hint: string) {
  const [kind, detail] = hint.split(':');
  if (kind === 'uncited') return '这句话没有标注来源';
  if (kind === 'unknown_citation') return `引用了不存在的来源 ${detail.split(',').map(n => `[${n}]`).join(' ')}`;
  if (kind === 'number_not_in_source') return `数字 ${detail.split(',').join('、')} 没有出现在所引原文中`;
  return hint;
}
