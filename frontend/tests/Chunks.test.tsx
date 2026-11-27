// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Reader } from '../src/Reader';
import type { Chunk, Doc, DocumentChunks } from '../src/api';
import { chatServer, json } from './chatServer';

vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({ children, onLoadSuccess }: { children: React.ReactNode; onLoadSuccess?: (pdf: { numPages: number }) => void }) => {
    React.useEffect(() => { onLoadSuccess?.({ numPages: 10 }); }, []);
    return <div>{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => <div>PDF page {pageNumber}</div>,
}));
const doc: Doc = { document_id: 'd1', filename: 'book.pdf', title: null, status: 'indexed', page_count: 10, chunks_count: 2, last_error: null, uploaded_at: '2026-11-26', pending_corrections: 1 };
const chunk = (fields: Partial<Chunk>): Chunk => ({ chunk_id: 'c1', position: 1, content: 'Rotation text', original: 'Rotation text', edited: false, excluded: false, correction_id: null, block_ids: ['b1'], page_range: [3, 3], section_title: 'Rotation', kind: 'text', part: null, parts: null, ...fields });
let view: DocumentChunks;
let calls: { url: string; method: string; body?: unknown }[];
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
  view = { document_id: 'd1', status: 'indexed', chunks: [
    chunk({}), chunk({ chunk_id: 'c2', position: 2, content: 'Heat ftxt', original: 'Heat ftxt', page_range: [7, 7], section_title: 'Heat', kind: 'table', part: 1, parts: 2 })],
    orphaned: [{ correction_id: 'k1', original_text: 'Old heat text', text: 'My fixed heat text', excluded: false, context: { section_title: 'Heat', page_range: [7, 7] }, candidate_chunk_ids: ['c2'], updated_at: '' }] };
  calls = [];
  const server = chatServer({ other: (url, init) => {
    const method = init?.method ?? 'GET', body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/v1/documents/d1/chunks') return json(view);
    if (url.startsWith('/api/v1/documents/d1/chunks/') && method === 'PATCH') {
      const id = url.split('/').pop()!;
      view = { ...view, chunks: view.chunks.map(c => c.chunk_id !== id ? c : {
        ...c, ...('text' in body ? { content: body.text ?? c.original, edited: body.text !== null } : {}), ...('excluded' in body ? { excluded: body.excluded } : {}) }) };
      return json(view.chunks.find(c => c.chunk_id === id));
    }
    if (url === '/api/v1/documents/d1/corrections/k1/attach') { view = { ...view, orphaned: [], chunks: view.chunks.map(c => c.chunk_id === 'c2' ? { ...c, content: 'My fixed heat text', edited: true } : c) }; return json(view.chunks[1]); }
    if (url === '/api/v1/documents/d1/corrections/k1' && method === 'DELETE') { view = { ...view, orphaned: [] }; return new Response(null, { status: 204 }); }
    return undefined;
  } });
  vi.stubGlobal('fetch', server.fetcher);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function openChunks() {
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.click(screen.getByRole('tab', { name: /分块 · 1 条待处理/ }));
  await screen.findByText('Rotation text');
  return user;
}
const card = (text: string) => screen.getByText(text).closest('li')!;

it('edits, excludes, and restores a chunk', async () => {
  const user = await openChunks();
  expect(within(card('Heat ftxt')).getByText('表格 1/2')).toBeTruthy();
  await user.click(within(card('Rotation text')).getByRole('button', { name: '编辑' }));
  const box = screen.getByRole('textbox', { name: '分块内容' });
  await user.clear(box); await user.type(box, 'Rotation, fixed');
  await user.click(screen.getByRole('button', { name: '保存' }));
  expect(await screen.findByText('Rotation, fixed')).toBeTruthy();
  expect(calls.find(c => c.method === 'PATCH')).toMatchObject({ url: '/api/v1/documents/d1/chunks/c1', body: { text: 'Rotation, fixed' } });
  expect(within(card('Rotation, fixed')).getByText('已编辑')).toBeTruthy();
  await user.click(within(card('Rotation, fixed')).getByRole('button', { name: '排除' }));
  expect(await within(card('Rotation, fixed')).findByText('已排除')).toBeTruthy();
  expect(calls.filter(c => c.method === 'PATCH').at(-1)!.body).toEqual({ excluded: true });
  await user.click(screen.getByRole('button', { name: '已排除 1' }));
  expect(screen.queryByText('Heat ftxt')).toBeNull();
  await user.click(screen.getByRole('button', { name: '全部 2' }));
  await user.click(within(card('Rotation, fixed')).getByRole('button', { name: '恢复原文' }));
  expect(await screen.findByText('Rotation text')).toBeTruthy();
  expect(calls.filter(c => c.method === 'PATCH').at(-1)!.body).toEqual({ text: null });
});

it('applies an orphaned correction to a suggested chunk, or discards it', async () => {
  const user = await openChunks();
  const orphans = screen.getByRole('region', { name: '待处理的更正' });
  expect(within(orphans).getByText('My fixed heat text')).toBeTruthy();
  expect(within(orphans).getByText(/第 7 页 · § Heat/)).toBeTruthy();
  await user.click(within(orphans).getByRole('button', { name: '应用到这一块' }));
  expect(calls.find(c => c.url.endsWith('/attach'))!.body).toEqual({ chunk_id: 'c2' });
  expect(await screen.findByText('My fixed heat text')).toBeTruthy();
  expect(screen.queryByRole('region', { name: '待处理的更正' })).toBeNull();
});

it('discarding asks first, and locating a chunk jumps to its page', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const user = await openChunks();
  await user.click(screen.getByRole('button', { name: '放弃这条更正' }));
  expect(calls.some(c => c.method === 'DELETE' && c.url.endsWith('/corrections/k1'))).toBe(true);
  await user.click(within(card('Heat ftxt')).getByRole('button', { name: /定位原文/ }));
  expect(await screen.findByText('PDF page 7')).toBeTruthy();
});

it('edits are disabled while the document is reprocessing', async () => {
  const user = userEvent.setup();
  render(<Reader doc={{ ...doc, status: 'parsing' }}/>);
  await user.click(screen.getByRole('tab', { name: /分块/ }));
  await screen.findByText('Rotation text');
  expect(screen.getByText('文档处理中，完成后才能编辑分块。')).toBeTruthy();
  expect((within(card('Rotation text')).getByRole('button', { name: '编辑' }) as HTMLButtonElement).disabled).toBe(true);
});
