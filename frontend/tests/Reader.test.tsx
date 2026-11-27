// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Reader } from '../src/Reader';
import type { Doc } from '../src/api';

vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Page: () => <div>PDF page</div>,
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
  expect(url).toBe('/api/v1/documents/ask');
  expect(JSON.parse(options.body)).toEqual({ document_id: doc.document_id, question: 'What does this document explain?', top_k: 3 });
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
