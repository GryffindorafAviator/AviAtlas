// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Reader } from '../src/Reader';
import type { Doc } from '../src/api';

vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({ children, onLoadSuccess }: { children: React.ReactNode; onLoadSuccess?: (pdf: { numPages: number }) => void }) => {
    React.useEffect(() => { onLoadSuccess?.({ numPages: 10 }); }, []);
    return <div>{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => <div>PDF page {pageNumber}</div>,
}));
const doc: Doc = { document_id: 'test-document', filename: 'book.pdf', title: null, status: 'indexed', page_count: 2, chunks_count: 5, last_error: null, uploaded_at: '2026-11-26' };
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('clicking Send posts the typed question and displays the answer', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ answer: 'A grounded answer [1].', sources: [] }), { status: 200 }));
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<React.StrictMode><Reader doc={doc}/></React.StrictMode>);
  await user.type(screen.getByRole('textbox'), 'What does this document explain?');
  await user.click(screen.getByRole('button', { name: /发送/ }));
  await screen.findByText('A grounded answer [1].');
  expect(fetcher).toHaveBeenCalledTimes(1);
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toBe('/api/v1/ask');
  expect(JSON.parse(options.body)).toEqual({ document_ids: [doc.document_id], question: 'What does this document explain?', mode: 'grounded', top_k: 5 });
});
it('shows pending state and recovers from an API error without losing the question', async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>(r => { resolve = r; }));
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'Explain attention');
  await user.click(screen.getByRole('button', { name: /发送/ }));
  expect(screen.getByText(/正在检索文档并生成回答/)).toBeTruthy();
  resolve(new Response(JSON.stringify({ detail: 'Provider unavailable' }), { status: 502 }));
  await screen.findByRole('alert');
  expect(screen.getByRole('alert').textContent).toContain('Provider unavailable');
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Explain attention');
  await waitFor(() => expect((screen.getByRole('button', { name: /发送/ }) as HTMLButtonElement).disabled).toBe(false));
});
it('Enter sends, Shift+Enter adds a newline, and empty questions do not send', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ answer: 'Keyboard answer', sources: [] }), { status: 200 }));
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.click(screen.getByRole('textbox'));
  await user.keyboard('{Enter}');
  expect(fetcher).not.toHaveBeenCalled();
  await user.type(screen.getByRole('textbox'), 'First line');
  await user.keyboard('{Shift>}{Enter}{/Shift}Second line');
  expect(fetcher).not.toHaveBeenCalled();
  await user.keyboard('{Enter}');
  await screen.findByText('Keyboard answer');
  expect(JSON.parse(fetcher.mock.calls[0][1].body).question).toBe('First line\nSecond line');
});
it('preserves input and submission when a status poll refreshes the document', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ answer: 'After refresh', sources: [] }), { status: 200 }));
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  const { rerender } = render(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'My question');
  rerender(<Reader doc={{ ...doc }}/>);
  await user.click(screen.getByRole('button', { name: /发送/ }));
  await screen.findByText('After refresh');
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it('disables submission until the document finishes indexing', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  const { rerender } = render(<Reader doc={{ ...doc, status: 'indexing' }}/>);
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: /发送/ }) as HTMLButtonElement).disabled).toBe(true);
  rerender(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'Ready');
  expect((screen.getByRole('button', { name: /发送/ }) as HTMLButtonElement).disabled).toBe(false);
  expect(fetcher).not.toHaveBeenCalled();
});

const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
it('open mode labels model-supplied content and warns about unknown citations', async () => {
  const fetcher = vi.fn().mockResolvedValue(json({ answer: 'x', mode: 'open', sources: [], invalid_citations: [7], segments: [
    { source_type: 'library', text: 'From the book [1].' }, { source_type: 'model', text: 'Background knowledge.' }] }));
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.click(screen.getByRole('button', { name: '允许模型补充' }));
  await user.type(screen.getByRole('textbox'), 'Why?{Enter}');
  await screen.findByText('From the book [1].');
  expect(JSON.parse(fetcher.mock.calls[0][1].body).mode).toBe('open');
  const model = screen.getByText('Background knowledge.').closest('section')!;
  expect(model.className).toContain('segment-model');
  expect(model.textContent).toContain('模型补充');
  expect(screen.getByText(/不存在的来源编号/).textContent).toContain('[7]');
});
it('asks about selected text and clears the selection after sending', async () => {
  const fetcher = vi.fn().mockResolvedValue(json({ answer: 'Explained.', sources: [] }));
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc} initialPage={3}/>);
  const page = await screen.findByText('PDF page 3');
  vi.spyOn(window, 'getSelection').mockReturnValue({ toString: () => '  angular velocity\n is a vector ', anchorNode: page } as unknown as globalThis.Selection);
  fireEvent.mouseUp(page);
  expect(screen.getByText('angular velocity is a vector')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: '解释这段' }));
  await user.click(screen.getByRole('button', { name: /发送/ }));
  await screen.findByText('Explained.');
  expect(JSON.parse(fetcher.mock.calls[0][1].body).selection).toEqual({ text: 'angular velocity is a vector', document_id: doc.document_id, page_number: 3 });
  expect(screen.queryByRole('button', { name: '取消引用选中内容' })).toBeNull();
  expect(screen.getAllByText('angular velocity is a vector')[0].closest('blockquote')).toBeTruthy();
});
it('ignores selections outside the page and lets the user discard a selection', async () => {
  vi.stubGlobal('fetch', vi.fn());
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  const page = await screen.findByText('PDF page 1');
  const spy = vi.spyOn(window, 'getSelection');
  spy.mockReturnValue({ toString: () => 'outside', anchorNode: document.body } as unknown as globalThis.Selection);
  fireEvent.mouseUp(page);
  expect(screen.queryByText('outside')).toBeNull();
  spy.mockReturnValue({ toString: () => 'inside', anchorNode: page } as unknown as globalThis.Selection);
  fireEvent.mouseUp(page);
  await user.click(screen.getByRole('button', { name: '取消引用选中内容' }));
  expect(screen.queryByText('inside')).toBeNull();
});
it('citation buttons jump the reader to the cited page', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ answer: 'See [1].', sources: [
    { chunk_id: 'c1', content: 'Evidence', page_range: [4, 4], provenance: [{ page_number: 4 }], document_name: 'book.pdf' }] })));
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await screen.findByText('PDF page 1');
  await user.type(screen.getByRole('textbox'), 'Where?{Enter}');
  await user.click(await screen.findByRole('button', { name: /查看第 4 页/ }));
  expect(screen.getByText('PDF page 4')).toBeTruthy();
});
