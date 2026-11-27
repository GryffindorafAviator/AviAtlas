// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Reader } from '../src/Reader';
import type { Doc } from '../src/api';
import { chatServer, conversation, hangingStream, json, message, sse, turn } from './chatServer';

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
const answering = (reply: Parameters<typeof turn>[1]) => chatServer({ reply: b => sse(turn(b, reply)) });
const lastPost = (server: ReturnType<typeof chatServer>) => server.posts().at(-1)!.body;

it('clicking Send starts a document conversation and streams the answer', async () => {
  const server = answering({ content: 'A grounded answer [1].' });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  render(<React.StrictMode><Reader doc={doc}/></React.StrictMode>);
  await user.type(screen.getByRole('textbox'), 'What does this document explain?');
  await user.click(screen.getByRole('button', { name: /发送/ }));
  await screen.findByText('A grounded answer [1].');
  expect(server.calls.filter(c => c.method === 'POST').map(c => c.url)).toEqual(['/api/v1/conversations', expect.stringMatching(/\/conversations\/c\d+\/messages$/)]);
  expect(server.calls.find(c => c.method === 'POST')!.body).toEqual({ document_ids: [doc.document_id] });
  expect(lastPost(server)).toEqual({ question: 'What does this document explain?', mode: 'grounded', top_k: 5 });
  expect(screen.getByText('What does this document explain?')).toBeTruthy();
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('');
});
it('shows pending state and recovers from an API error without losing the question', async () => {
  let resolve!: (response: Response) => void;
  const server = chatServer({ reply: () => new Promise<Response>(r => { resolve = r; }) });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'Explain attention');
  await user.click(screen.getByRole('button', { name: /发送/ }));
  expect(await screen.findByText(/正在检索文档并生成回答/)).toBeTruthy();
  resolve(json({ detail: 'An answer is still being generated in this conversation.' }, 409));
  await screen.findByRole('alert');
  expect(screen.getByRole('alert').textContent).toContain('still being generated');
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Explain attention');
  await waitFor(() => expect((screen.getByRole('button', { name: /发送/ }) as HTMLButtonElement).disabled).toBe(false));
});
it('Enter sends, Shift+Enter adds a newline, and empty questions do not send', async () => {
  const server = answering({ content: 'Keyboard answer' });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.click(screen.getByRole('textbox'));
  await user.keyboard('{Enter}');
  await user.type(screen.getByRole('textbox'), 'First line');
  await user.keyboard('{Shift>}{Enter}{/Shift}Second line');
  expect(server.posts()).toHaveLength(0);
  await user.keyboard('{Enter}');
  await screen.findByText('Keyboard answer');
  expect(lastPost(server).question).toBe('First line\nSecond line');
});
it('preserves input and submission when a status poll refreshes the document', async () => {
  const server = answering({ content: 'After refresh' });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  const { rerender } = render(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'My question');
  rerender(<Reader doc={{ ...doc }}/>);
  await user.click(screen.getByRole('button', { name: /发送/ }));
  await screen.findByText('After refresh');
  expect(server.posts()).toHaveLength(1);
});
it('disables submission until the document finishes indexing', async () => {
  const server = chatServer(); vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  const { rerender } = render(<Reader doc={{ ...doc, status: 'indexing' }}/>);
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: /发送/ }) as HTMLButtonElement).disabled).toBe(true);
  rerender(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'Ready');
  expect((screen.getByRole('button', { name: /发送/ }) as HTMLButtonElement).disabled).toBe(false);
  expect(server.posts()).toHaveLength(0);
});
it('open mode labels model-supplied content and warns about unknown citations', async () => {
  const server = answering({ content: '[LIBRARY]\nFrom the book [1].\n[MODEL]\nBackground knowledge.', mode: 'open', invalid_citations: [7],
    segments: [{ source_type: 'library', text: 'From the book [1].' }, { source_type: 'model', text: 'Background knowledge.' }] });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.click(screen.getByRole('button', { name: '允许模型补充' }));
  await user.type(screen.getByRole('textbox'), 'Why?{Enter}');
  await screen.findByText(/不存在的来源编号/);
  expect(lastPost(server).mode).toBe('open');
  expect(screen.getByText('From the book [1].')).toBeTruthy();
  const model = screen.getByText('Background knowledge.').closest('section')!;
  expect(model.className).toContain('segment-model');
  expect(model.textContent).toContain('模型补充');
  expect(screen.getByText(/不存在的来源编号/).textContent).toContain('[7]');
});
it('asks about selected text and clears the selection after sending', async () => {
  const server = answering({ content: 'Explained.' });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc} initialPage={3}/>);
  const page = await screen.findByText('PDF page 3');
  vi.spyOn(window, 'getSelection').mockReturnValue({ toString: () => '  angular velocity\n is a vector ', anchorNode: page } as unknown as globalThis.Selection);
  fireEvent.mouseUp(page);
  expect(screen.getByText('angular velocity is a vector')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: '解释这段' }));
  await user.click(screen.getByRole('button', { name: /发送/ }));
  await screen.findByText('Explained.');
  expect(lastPost(server).selection).toEqual({ text: 'angular velocity is a vector', document_id: doc.document_id, page_number: 3 });
  expect(screen.queryByRole('button', { name: '取消引用选中内容' })).toBeNull();
  expect(screen.getAllByText('angular velocity is a vector')[0].closest('blockquote')).toBeTruthy();
});
it('ignores selections outside the page and lets the user discard a selection', async () => {
  vi.stubGlobal('fetch', chatServer().fetcher);
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
  vi.stubGlobal('fetch', answering({ content: 'See [1].', sources: [
    { chunk_id: 'c1', content: 'Evidence', page_range: [4, 4], provenance: [{ page_number: 4 }], document_name: 'book.pdf' }] }).fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await screen.findByText('PDF page 1');
  await user.type(screen.getByRole('textbox'), 'Where?{Enter}');
  await user.click(await screen.findByRole('button', { name: /查看第 4 页/ }));
  expect(screen.getByText('PDF page 4')).toBeTruthy();
});
it('reopens the latest conversation for this document and follows up in it', async () => {
  const mine = conversation({ title: 'Rotation', document_ids: [doc.document_id] });
  const other = conversation({ title: 'Elsewhere', document_ids: ['another-doc'] });
  const library = conversation({ title: 'Library chat' });
  const server = chatServer({ conversations: [mine, other, library], details: { [mine.conversation_id]: [
    message({ role: 'user', content: 'What is rotation?' }),
    message({ role: 'assistant', content: 'Turning about an axis [1].', segments: [{ source_type: 'library', text: 'Turning about an axis [1].' }], removed_source_count: 1 })] },
    reply: b => sse(turn(b, { content: 'It is a vector.', search_query: 'angular velocity of rotation' })) });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  expect(await screen.findByText('Turning about an axis [1].')).toBeTruthy();
  expect(screen.getByText('1 条来源所在的文档已删除。')).toBeTruthy();
  const picker = screen.getByLabelText('对话') as HTMLSelectElement;
  expect([...picker.options].map(o => o.text)).toEqual(['Rotation']);
  await user.type(screen.getByRole('textbox'), 'And its velocity?{Enter}');
  await screen.findByText('It is a vector.');
  expect(server.calls.filter(c => c.url === '/api/v1/conversations' && c.method === 'POST')).toHaveLength(0);
  expect(server.posts()[0].url).toBe(`/api/v1/conversations/${mine.conversation_id}/messages`);
  expect(screen.getByText('检索：angular velocity of rotation')).toBeTruthy();
});
it('stop aborts the stream and keeps the partial answer', async () => {
  const server = chatServer({ reply: (b, init) => {
    const events = turn(b, { content: 'Partial answer' }).slice(0, 3);
    return hangingStream(events, init.signal);
  } });
  vi.stubGlobal('fetch', server.fetcher);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await user.type(screen.getByRole('textbox'), 'Long question{Enter}');
  expect(await screen.findByText('Partial')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: /停止/ }));
  expect(await screen.findByText('已停止生成。')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByRole('button', { name: /发送/ })).toBeTruthy();
});
it('starts a new conversation and deletes the current one', async () => {
  const first = conversation({ title: 'First', document_ids: [doc.document_id] });
  const second = conversation({ title: 'Second', document_ids: [doc.document_id] });
  const server = chatServer({ conversations: [first, second], details: {
    [first.conversation_id]: [message({ role: 'user', content: 'first question' })],
    [second.conversation_id]: [message({ role: 'user', content: 'second question' })] } });
  vi.stubGlobal('fetch', server.fetcher);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const user = userEvent.setup();
  render(<Reader doc={doc}/>);
  await screen.findByText('first question');
  await user.click(screen.getByRole('button', { name: '新对话' }));
  expect(screen.queryByText('first question')).toBeNull();
  expect(screen.getByText('让阅读多一点启发')).toBeTruthy();
  await user.selectOptions(screen.getByLabelText('对话'), first.conversation_id);
  await screen.findByText('first question');
  await user.click(screen.getByRole('button', { name: '删除对话' }));
  await screen.findByText('second question');
  expect(server.calls.some(c => c.method === 'DELETE' && c.url.endsWith(first.conversation_id))).toBe(true);
  expect([...(screen.getByLabelText('对话') as HTMLSelectElement).options].map(o => o.text)).toEqual(['Second']);
});
