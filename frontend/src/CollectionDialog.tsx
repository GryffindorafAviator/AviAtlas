import React, { useEffect, useRef, useState } from 'react';
import { request, type Collection, type Doc } from './api';

export function CollectionDialog({ doc, collections, onClose, onSaved }: {
  doc: Doc; collections: Collection[]; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [ids, setIds] = useState<string[]>(doc.collection_ids || []);
  const [saving, setSaving] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    const element = dialog.current!; element.showModal();
    return () => element.close();
  }, []);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setSaving(true); setError('');
    try {
      await request(`/documents/${doc.document_id}/collections`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ collection_ids: ids }),
      });
      await onSaved(); onClose();
    } catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  }
  return <dialog className="collection-dialog" ref={dialog} aria-labelledby="membership-title" onCancel={event => { event.preventDefault(); if (!saving) onClose(); }}>
    <form onSubmit={save}><h2 id="membership-title">// 管理分组</h2><p className="dialog-document">{doc.filename}</p><p className="dialog-hint">可选择多个分组。取消全部勾选可移出所有分组，文档会保留。</p>
      {error && <p className="error" role="alert">{error}</p>}
      {!collections.length ? <p>暂无分组，请先在左侧新建分组。</p> : <fieldset disabled={saving} className="membership-options"><legend className="sr-only">文档所属分组</legend>{collections.map(c => <label key={c.collection_id}><input type="checkbox" checked={ids.includes(c.collection_id)} onChange={e => setIds(previous => e.target.checked ? [...previous, c.collection_id] : previous.filter(id => id !== c.collection_id))}/><span>{c.name}</span></label>)}</fieldset>}
      <div className="dialog-actions"><button type="button" className="btn" disabled={saving} onClick={onClose}>取消</button><button type="submit" className="btn btn-ok" disabled={saving}>{saving ? '保存中…' : '保存分组'}</button></div>
    </form>
  </dialog>;
}
