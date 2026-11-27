export type Doc = { document_id: string; filename: string; title: string | null; status: string; page_count: number | null; chunks_count: number; last_error: string | null; uploaded_at: string; collection_ids?: string[] };
export type Source = { chunk_id: string; content: string; page_range: [number, number] | null; provenance: { page_number: number }[]; document_id?: string | null; document_name?: string | null; score?: number; rank?: number | null };
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
