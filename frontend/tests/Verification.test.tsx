// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Reader } from '../src/Reader';
import type { Doc, VerificationReport } from '../src/api';
import { chatServer, json, sse, turn } from './chatServer';

vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({ children, onLoadSuccess }: { children: React.ReactNode; onLoadSuccess?: (pdf: { numPages: number }) => void }) => {
    React.useEffect(() => { onLoadSuccess?.({ numPages: 10 }); }, []);
    return <div>{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => <div>PDF page {pageNumber}</div>,
}));
const doc: Doc = { document_id: 'd1', filename: 'paper.pdf', title: null, status: 'indexed', page_count: 10, chunks_count: 3, last_error: null, uploaded_at: '' };
const source = { chunk_id: 'k1', content: 'BLEU score of 28.4', page_range: [8, 8] as [number, number], provenance: [{ page_number: 8 }], document_name: 'paper.pdf' };
const report: VerificationReport = {
  verification_id: 'v1', message_id: 'x', method: 'citation_support', model: 'test/judge', created_at: '', unchecked_segments: 1,
  limitations: ['model_judgement'], summary: { supported: 1, conflicting: 1, insufficient_evidence: 0, unverified: 0 },
  claims: [
    { index: 1, text: 'It reaches 28.4 BLEU [1].', source_type: 'library', citations: [1], hints: [], status: 'supported', model_verdict: 'supported', reason: '原文写明 28.4。', notes: [], suggested_citations: [],
      evidence: [{ citation: 1, quote: 'BLEU score of 28.4', document_name: 'paper.pdf', page_range: [8, 8] }] },
    { index: 2, text: 'It took 41.0 days [1].', source_type: 'library', citations: [1], hints: ['number_not_in_source:41.0'], status: 'conflicting', model_verdict: 'contradicted', reason: '原文是 3.5 天。', notes: ['quote_not_found'], suggested_citations: [], evidence: [] }],
};
let calls: string[];
let verify: () => Response;
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
  calls = [];
  verify = () => json(report);
  vi.stubGlobal('fetch', chatServer({
    reply: b => {
      const events = turn(b, { content: 'It reaches 28.4 BLEU [1]. It took 41.0 days [1].', sources: [source] });
      const done = events.at(-1)![1] as { message: Record<string, unknown> };
      done.message.checks = [{ claim: 2, text: 'It took 41.0 days [1].', hints: ['number_not_in_source:41.0'] }];
      return sse(events);
    },
    other: (url, init) => { if (url.endsWith('/verification') && init?.method === 'POST') { calls.push(url); return verify(); } return undefined; },
  }).fetcher);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('shows automatic hints, runs a citation check, and opens the quoted source', async () => {
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'How fast?{Enter}');
  await user.click(await screen.findByText('自动检查 · 1 处提示'));
  expect(screen.getAllByText('数字 41.0 没有出现在所引原文中').length).toBe(1);
  await user.click(screen.getByRole('button', { name: '核查引用' }));
  const panel = await screen.findByRole('region', { name: '引用核查' });
  expect(calls[0]).toMatch(/\/conversations\/c\d+\/messages\/m\d+\/verification$/);
  expect(within(panel).getByText('原文支持 1')).toBeTruthy();
  expect(within(panel).getByText('与原文冲突 1')).toBeTruthy();
  expect(within(panel).getByText('BLEU score of 28.4')).toBeTruthy();
  expect(within(panel).getByText('模型给出的引文在原文中找不到，没有采纳。')).toBeTruthy();
  expect(within(panel).getByText(/不代表结论本身正确/).textContent).toContain('模型补充');
  await user.click(within(panel).getByRole('button', { name: /\[1\] paper\.pdf · 第 8 页/ }));
  expect(screen.getByText('PDF page 8')).toBeTruthy();
  expect(within(panel).getByRole('button', { name: '重新核查' })).toBeTruthy();
});

it('reports a failed check without showing a verdict', async () => {
  verify = () => json({ detail: 'The model did not return a readable verification result. Please retry.' }, 502);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'How fast?{Enter}');
  await user.click(await screen.findByRole('button', { name: '核查引用' }));
  expect((await screen.findByRole('alert')).textContent).toContain('核查失败');
  expect(screen.queryByText('原文支持 1')).toBeNull();
  expect(screen.getByRole('button', { name: '核查引用' })).toBeTruthy();
});
