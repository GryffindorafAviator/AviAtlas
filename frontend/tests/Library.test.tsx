// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../src/main';
import type { Collection, Doc } from '../src/api';
vi.mock('../src/Reader', () => ({ Reader: ({ doc, initialPage }: { doc: Doc; initialPage: number }) => <div>Reader {doc.filename} page {initialPage}</div> }));
const listed: Doc = { document_id: 'd1', filename: 'listed.pdf', title: null, status: 'indexed', page_count: 9, chunks_count: 3, last_error: null, uploaded_at: '2026-11-26', collection_ids: ['c1'] };
const unlisted: Doc = { ...listed, document_id: 'd2', filename: 'other.pdf', collection_ids: [] };
const group: Collection = { collection_id: 'c1', name: 'Physics', document_count: 1, created_at: '', updated_at: '' };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
let calls: { url: string; body?: unknown }[] = [];
beforeEach(() => {
  calls = [];
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    calls.push({ url, body: options?.body ? JSON.parse(options.body as string) : undefined });
    if (url === '/api/v1/collections') return json({ items: [group] });
    if (url.startsWith('/api/v1/documents?')) return json({ items: [listed], total: 1 });
    if (url === '/api/v1/documents/d2') return json(unlisted);
    if (url === '/api/v1/search') return json({ query: 'rotation', document_ids: ['d1', 'd2'], skipped_document_ids: ['d3'], results: [
      { chunk_id: 'k1', document_id: 'd2', document_name: 'other.pdf', content: 'Rotation evidence', page_range: [7, 7], provenance: [{ page_number: 7 }], score: .91, rank: 1 }] });
    if (url === '/api/v1/ask') return json({ answer: 'Library answer [1].', mode: 'grounded', segments: [{ source_type: 'library', text: 'Library answer [1].' }], sources: [
      { chunk_id: 'k2', document_id: 'd1', document_name: 'listed.pdf', content: 'Ev', page_range: [5, 5], provenance: [{ page_number: 5 }] }], skipped_document_ids: [] });
    return json({ detail: 'unexpected' }, 500);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('searches the library and opens a result from another page of the list at its page', async () => {
  const user = userEvent.setup();
  render(<App/>);
  await screen.findByText('listed.pdf');
  await user.click(screen.getByRole('tab', { name: '搜索' }));
  await user.type(screen.getByRole('textbox', { name: '搜索内容' }), 'rotation{Enter}');
  await screen.findByText('Rotation evidence');
  expect(calls.find(c => c.url === '/api/v1/search')!.body).toEqual({ query: 'rotation', top_k: 10 });
  expect(screen.getByText(/1 份文档尚未处理完成/)).toBeTruthy();
  await user.click(screen.getByRole('button', { name: /打开原文第 7 页/ }));
  expect(await screen.findByText('Reader other.pdf page 7')).toBeTruthy();
});
it('asks within the active collection and opens cited sources', async () => {
  const user = userEvent.setup();
  render(<App/>);
  await user.click(await screen.findByRole('button', { name: /Physics/ }));
  await user.click(screen.getByRole('tab', { name: '问答' }));
  expect(screen.getByText('范围：Physics')).toBeTruthy();
  await user.type(screen.getByRole('textbox', { name: '问题' }), 'What links these?{Enter}');
  await screen.findByText('Library answer [1].');
  expect(calls.find(c => c.url === '/api/v1/ask')!.body).toEqual({ collection_id: 'c1', question: 'What links these?', mode: 'grounded', top_k: 5 });
  await user.click(screen.getByRole('button', { name: /查看第 5 页/ }));
  expect(await screen.findByText('Reader listed.pdf page 5')).toBeTruthy();
});
