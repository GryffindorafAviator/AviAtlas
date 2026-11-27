// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../src/main';
import { CollectionDialog } from '../src/CollectionDialog';
import type { Collection, Doc } from '../src/api';
vi.mock('../src/Reader', () => ({ Reader: () => <div>Reader</div> }));
const doc: Doc = { document_id: 'd1', filename: 'book.pdf', title: null, status: 'indexed', page_count: 2, chunks_count: 2, last_error: null, uploaded_at: '2026-11-26', collection_ids: [] };
const group: Collection = { collection_id: 'c1', name: 'Physics', document_count: 0, created_at: '', updated_at: '' };
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
it('creates a group, manages membership, filters documents, and deletes only the group', async () => {
  let collections: Collection[] = [], ids: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/v1/collections' && options?.method === 'POST') {
      collections = [{ ...group, name: JSON.parse(options.body as string).name }]; return json(collections[0], 201);
    }
    if (url === '/api/v1/collections') return json({ items: collections.map(c => ({ ...c, document_count: ids.length ? 1 : 0 })) });
    if (url === '/api/v1/collections/c1' && options?.method === 'DELETE') { collections = []; ids = []; return new Response(null, { status: 204 }); }
    if (url === '/api/v1/documents/d1/collections') { ids = JSON.parse(options!.body as string).collection_ids; return json({ collection_ids: ids }); }
    if (url.startsWith('/api/v1/documents?')) {
      const items = url.includes('collection_id=c1') && !ids.includes('c1') ? [] : [{ ...doc, collection_ids: ids }];
      return json({ items, total: items.length });
    }
    throw new Error(`Unexpected request ${url}`);
  }));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const user = userEvent.setup(); render(<App/>);
  await screen.findByText('book.pdf');
  await user.type(screen.getByLabelText('新建分组'), 'Physics');
  await user.click(screen.getByRole('button', { name: '+ 新建分组' }));
  await screen.findByText('分组中还没有文档');
  await user.click(screen.getByRole('button', { name: '全部文档' }));
  await screen.findByText('book.pdf');
  await user.click(screen.getByRole('button', { name: '管理分组' }));
  const dialog = screen.getByRole('dialog');
  await user.click(within(dialog).getByRole('checkbox', { name: 'Physics' }));
  await user.click(within(dialog).getByRole('button', { name: '保存分组' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await user.click(screen.getByRole('button', { name: 'Physics 1' }));
  await screen.findByText('book.pdf');
  await user.click(screen.getByRole('button', { name: '删除分组' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: /Physics/ })).toBeNull());
  expect(screen.getByText('book.pdf')).toBeTruthy();
});
it('upload uses the selected collection without a second membership request', async () => {
  let uploadUrl = '';
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/v1/collections') return json({ items: [group] });
    if (url.startsWith('/api/v1/documents/upload')) { uploadUrl = url; expect(options?.body).toBeInstanceOf(FormData); return json({ document_id: 'd1', status: 'queued' }); }
    return json({ items: [], total: 0 });
  }));
  const user = userEvent.setup(); const { container } = render(<App/>);
  await user.click(await screen.findByRole('button', { name: 'Physics 0' }));
  await user.upload(container.querySelector('input[type=file]') as HTMLInputElement, new File(['%PDF'], 'new.pdf', { type: 'application/pdf' }));
  await waitFor(() => expect(uploadUrl).toBe('/api/v1/documents/upload?collection_id=c1'));
});
it('failed membership saves preserve the selection and keep the dialog open', async () => {
  const onClose = vi.fn(), onSaved = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'Collection not found.' }, 404)));
  const user = userEvent.setup();
  render(<CollectionDialog doc={doc} collections={[group]} onClose={onClose} onSaved={onSaved}/>);
  await user.click(screen.getByRole('checkbox', { name: 'Physics' }));
  await user.click(screen.getByRole('button', { name: '保存分组' }));
  expect((await screen.findByRole('alert')).textContent).toContain('Collection not found.');
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
  expect(onSaved).not.toHaveBeenCalled(); expect(onClose).not.toHaveBeenCalled();
});
