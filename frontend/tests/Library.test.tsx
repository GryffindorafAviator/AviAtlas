// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
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
      { chunk_id: 'k1', document_id: 'd2', document_name: 'other.pdf', content: 'Rotation evidence', page_range: [7, 7], provenance: [{ page_number: 7 }], score: .0325, rank: 1, retrieval_method: 'hybrid',
        scores: { dense: .81, bm25: 7.25, rrf: .0325 }, ranks: { dense: 2, bm25: 1 }, matched_terms: ['rotation'] }] });
    if (url === '/api/v1/search/debug') return json({ query: 'euler', method: 'hybrid', query_terms: ['euler', 'angles'], document_ids: ['d1'], skipped_document_ids: [],
      timings_ms: { scope: 1.2, dense: 8.5, bm25: 2.1, fusion: .3, total: 12.4 }, config: { rrf_k: 60, candidate_k: 20, embedding_model: 'm', tokenizer: 'jieba-search-v1' },
      dense: [{ chunk_id: 'a', document_id: 'd1', document_name: 'listed.pdf', content: 'Shared hit', page_range: [2, 2], provenance: [], rank: 1, scores: { dense: .7 }, ranks: { dense: 1 } },
              { chunk_id: 'b', document_id: 'd1', document_name: 'listed.pdf', content: 'Dense only', page_range: [3, 3], provenance: [], rank: 2, scores: { dense: .5 }, ranks: { dense: 2 } }],
      bm25: [{ chunk_id: 'a', document_id: 'd1', document_name: 'listed.pdf', content: 'Shared hit', page_range: [2, 2], provenance: [], rank: 1, scores: { bm25: 4 }, ranks: { bm25: 1 } }],
      results: [{ chunk_id: 'a', document_id: 'd1', document_name: 'listed.pdf', content: 'Shared hit', page_range: [2, 2], provenance: [], rank: 1, scores: { dense: .7, bm25: 4, rrf: .0328 }, ranks: { dense: 1, bm25: 1 } }] });
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
  expect(calls.find(c => c.url === '/api/v1/search')!.body).toEqual({ query: 'rotation', top_k: 10, method: 'hybrid' });
  expect(screen.getByText('语义 #2 · 0.810')).toBeTruthy();
  expect(screen.getByText('关键词 #1 · 7.25')).toBeTruthy();
  expect(screen.getByText('命中词：rotation')).toBeTruthy();
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

it('sends the chosen retrieval method', async () => {
  const user = userEvent.setup();
  render(<App/>);
  await screen.findByText('listed.pdf');
  await user.click(screen.getByRole('tab', { name: '搜索' }));
  await user.selectOptions(screen.getByLabelText('检索方式'), 'bm25');
  await user.type(screen.getByRole('textbox', { name: '搜索内容' }), 'rotation{Enter}');
  await screen.findByText('Rotation evidence');
  expect(calls.find(c => c.url === '/api/v1/search')!.body).toMatchObject({ method: 'bm25' });
  expect(screen.getByText('关键词检索', { selector: 'span' })).toBeTruthy();
});
it('the retrieval inspector shows terms, timings, every candidate list, and overlap', async () => {
  const user = userEvent.setup();
  render(<App/>);
  await screen.findByText('listed.pdf');
  await user.click(screen.getByRole('tab', { name: '检索调试' }));
  await user.type(screen.getByRole('textbox', { name: '调试查询' }), 'euler{Enter}');
  const dense = await screen.findByRole('region', { name: '语义候选' });
  expect(screen.getByText('angles')).toBeTruthy();
  expect(screen.getByText('总耗时 12.4')).toBeTruthy();
  expect(screen.getByText('RRF k：60')).toBeTruthy();
  expect(within(dense).getAllByRole('listitem')).toHaveLength(2);
  expect(within(screen.getByRole('region', { name: '关键词候选' })).getAllByRole('listitem')).toHaveLength(1);
  expect(within(screen.getByRole('region', { name: 'RRF 融合结果' })).getByText('融合 · 0.0328')).toBeTruthy();
  expect(within(dense).getAllByText('两路命中')).toHaveLength(1);
  await user.click(within(dense).getAllByRole('button', { name: /打开/ })[1]);
  expect(await screen.findByText('Reader listed.pdf page 3')).toBeTruthy();
});
