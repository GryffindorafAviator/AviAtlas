import { vi } from 'vitest';
import type { Conversation, ConversationDetail, Message, Source } from '../src/api';

export const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const encode = (events: [string, unknown][]) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
export const sse = (events: [string, unknown][]) => new Response(encode(events), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });

let counter = 0;
export const message = (fields: Partial<Message> & Pick<Message, 'role' | 'content'>): Message =>
  ({ message_id: `m${++counter}`, status: 'complete', created_at: '2026-11-27T00:00:00Z', mode: 'grounded', ...fields });
export const conversation = (fields: Partial<Conversation> = {}): Conversation =>
  ({ conversation_id: `c${++counter}`, title: 'Earlier', document_ids: null, collection_id: null, created_at: '', updated_at: '', message_count: 0, ...fields });

export type Reply = { content: string; mode?: 'grounded' | 'open'; segments?: Message['segments']; sources?: Source[]; invalid_citations?: number[]; search_query?: string };

/** The events one successful turn streams, splitting the answer into two deltas. */
export function turn(body: { question: string; mode?: string; selection?: Message['selection'] }, reply: Reply): [string, unknown][] {
  const user = message({ role: 'user', content: body.question, selection: body.selection ?? null });
  const assistant = message({ role: 'assistant', content: '', status: 'streaming', mode: reply.mode ?? 'grounded' });
  const half = Math.ceil(reply.content.length / 2);
  const sources = (reply.sources ?? []).map((s, i) => ({ ...s, citation: i + 1 }));
  return [
    ['started', { user, assistant }],
    ['retrieval', { search_query: reply.search_query ?? body.question, sources, document_ids: [], skipped_document_ids: [] }],
    ['delta', { text: reply.content.slice(0, half) }],
    ['delta', { text: reply.content.slice(half) }],
    ['done', { message: { ...assistant, status: 'complete', content: reply.content, segments: reply.segments ?? [], sources, invalid_citations: reply.invalid_citations ?? [], search_query: reply.search_query ?? body.question } }],
  ];
}

/** A stream that sends `events` and then stays open until the request is aborted. */
export function hangingStream(events: [string, unknown][], signal?: AbortSignal | null) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(encode(events)));
      signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')));
    },
  });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

type Options = {
  conversations?: Conversation[];
  details?: Record<string, Message[]>;
  reply?: (body: any, init: RequestInit) => Response | Promise<Response>;
  other?: (url: string, init?: RequestInit) => Response | undefined;
};

/** Fake API for conversations; everything else goes to `other`. */
export function chatServer({ conversations = [], details = {}, reply, other }: Options = {}) {
  const calls: { url: string; method: string; body?: any }[] = [];
  const fetcher = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET', body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/v1/conversations' && method === 'GET') return json({ items: conversations });
    if (url === '/api/v1/conversations' && method === 'POST') {
      const created = conversation({ title: null, document_ids: body.document_ids ?? null, collection_id: body.collection_id ?? null });
      conversations = [created, ...conversations];
      return json(created, 201);
    }
    const match = url.match(/^\/api\/v1\/conversations\/([^/]+)(\/messages)?$/);
    if (match && match[2]) return reply ? reply(body, init) : json({ detail: 'no reply configured' }, 500);
    if (match && method === 'DELETE') { conversations = conversations.filter(c => c.conversation_id !== match[1]); return new Response(null, { status: 204 }); }
    if (match) {
      const found = conversations.find(c => c.conversation_id === match[1]);
      return found ? json({ ...found, messages: details[match[1]] ?? [] } satisfies ConversationDetail) : json({ detail: 'Conversation not found.' }, 404);
    }
    return other?.(url, init) ?? json({ detail: 'unexpected' }, 500);
  });
  return { fetcher, calls, posts: () => calls.filter(c => c.url.endsWith('/messages')) };
}
