export type Doc = { document_id: string; filename: string; title: string | null; status: string; page_count: number | null; chunks_count: number; last_error: string | null; uploaded_at: string };
export type Source = { chunk_id: string; content: string; page_range: [number, number] | null; provenance: { page_number: number }[] };
export type Answer = { answer: string; sources: Source[] };
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : `请求失败（${response.status}），请稍后重试。`);
  }
  return response.status === 204 ? undefined as T : response.json();
}
