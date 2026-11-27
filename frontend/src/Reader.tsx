import React, { useEffect, useRef, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Assistant } from './Assistant';
import { ChunkEditor } from './ChunkEditor';
import { canAsk, formatNames, request, type Chunk, type ContentBlock, type Doc, type Selection, type Source } from './api';
import 'react-pdf/dist/Page/TextLayer.css';
import 'react-pdf/dist/Page/AnnotationLayer.css';
pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString();
const statusNames: Record<string, string> = { uploaded: '待处理', queued: '排队中', parsing: '解析中', indexing: '建立索引', indexed: '可提问', failed: '处理失败', deleting: '删除中' };
const MAX_SELECTION = 2000;

/** Selected text inside `box`, normalized and capped, or null. */
function selectedText(box: HTMLElement | null) {
  const selected = window.getSelection(), text = selected?.toString().replace(/\s+/g, ' ').trim();
  if (!text || !selected?.anchorNode || !box?.contains(selected.anchorNode)) return null;
  return text.slice(0, MAX_SELECTION);
}

function TextBlock({ block, markdown }: { block: ContentBlock; markdown: boolean }) {
  if (block.type === 'heading') return <h3>{block.content}</h3>;
  if (block.type === 'equation' || block.type === 'code') return <pre className={`text-${block.type}`}>{block.content}</pre>;
  if (block.type === 'table' || markdown) return <Markdown remarkPlugins={[remarkGfm]}>{block.content}</Markdown>;
  return <p>{block.content}</p>;
}

function TextView({ doc, focus, box, onSelect }: { doc: Doc; focus: string[]; box: React.RefObject<HTMLDivElement>; onSelect: () => void }) {
  const [blocks, setBlocks] = useState<ContentBlock[] | null>(null), [error, setError] = useState('');
  const ready = canAsk(doc);
  useEffect(() => {
    if (!ready) return;
    let active = true;
    request<{ blocks: ContentBlock[] }>(`/documents/${doc.document_id}/content`)
      .then(data => { if (active) { setBlocks(data.blocks); setError(''); } })
      .catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [doc.document_id, ready]);
  useEffect(() => {
    if (focus.length && blocks) document.getElementById(`block-${focus[0]}`)?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, [focus, blocks]);
  const cited = new Set(focus), markdown = doc.source_format === 'markdown';
  return <div className="text-stage" ref={box} onMouseUp={onSelect} onKeyUp={onSelect}>
    {error ? <p className="error">{error}</p>
      : !ready ? <p className="placeholder">文档处理完成后即可阅读。</p>
      : !blocks ? <p className="placeholder">正在打开文档…</p>
      : !blocks.length ? <p className="placeholder">没有提取到文字内容。</p>
      : blocks.map(b => <div key={b.block_id} id={`block-${b.block_id}`} className={`text-block text-block-${b.type}${cited.has(b.block_id) ? ' is-cited' : ''}`}><TextBlock block={b} markdown={markdown}/></div>)}
  </div>;
}

export function Reader({ doc, initialPage = 1, initialBlock }: { doc: Doc; initialPage?: number; initialBlock?: string }) {
  const isPdf = (doc.source_format ?? 'pdf') === 'pdf';
  const [page, setPage] = useState(initialPage), [pages, setPages] = useState(0), [scale, setScale] = useState(1);
  const [width, setWidth] = useState(600), [selection, setSelection] = useState<Selection | null>(null);
  const [focus, setFocus] = useState<string[]>(initialBlock ? [initialBlock] : []);
  const [view, setView] = useState<'document' | 'chunks'>('document');
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!isPdf || view !== 'document') return;
    const observer = new ResizeObserver(entries => setWidth(Math.max(240, entries[0].contentRect.width - 48)));
    if (box.current) observer.observe(box.current);
    return () => observer.disconnect();
  }, [isPdf, view]);
  useEffect(() => { if (pages && page > pages) setPage(pages); }, [pages, page]);
  const jump = (p: number) => { if (pages) setPage(Math.max(1, Math.min(pages, p))); };
  function captureSelection() {
    const text = selectedText(box.current);
    if (text) setSelection({ text, document_id: doc.document_id, ...(isPdf ? { page_number: page } : {}) });
  }
  function openSource(source: Source, p?: number) {
    if (isPdf) { if (p) jump(p); }
    else if (source.block_ids?.length) setFocus([...source.block_ids]);
  }
  function locate(chunk: Chunk) {
    setView('document');
    if (isPdf) { if (chunk.page_range) jump(chunk.page_range[0]); }
    else setFocus([...chunk.block_ids]);
  }
  const ready = canAsk(doc);
  return <div className="workspace">
    <section className="reader" aria-label={isPdf ? 'PDF 阅读器' : '文档阅读器'}>
      <div className="reader-tabs" role="tablist" aria-label="阅读视图">
        <button role="tab" aria-selected={view === 'document'} className={`btn ${view === 'document' ? 'is-active' : ''}`} onClick={() => setView('document')}>原文</button>
        <button role="tab" aria-selected={view === 'chunks'} className={`btn ${view === 'chunks' ? 'is-active' : ''}`} onClick={() => setView('chunks')}>分块{doc.pending_corrections ? ` · ${doc.pending_corrections} 条待处理` : ''}</button>
      </div>
      {view === 'chunks' ? <ChunkEditor doc={doc} onLocate={locate}/> : isPdf
        ? <>
          <div className="reader-toolbar"><h2>// READER</h2><div className="controls"><button className="btn" disabled={page <= 1} onClick={() => jump(page - 1)} aria-label="上一页">←</button><label><input aria-label="页码" type="number" min="1" max={pages || 1} value={page} onChange={e => jump(Number(e.target.value) || 1)} /> / {pages || '—'}</label><button className="btn" disabled={!pages || page >= pages} onClick={() => jump(page + 1)} aria-label="下一页">→</button><span className="divider"/><button className="btn" onClick={() => setScale(s => Math.max(.5, s - .1))} aria-label="缩小">−</button><span>{Math.round(scale * 100)}%</span><button className="btn" onClick={() => setScale(s => Math.min(2, s + .1))} aria-label="放大">+</button></div></div>
          <div className="pdf-stage" ref={box} onMouseUp={captureSelection} onKeyUp={captureSelection}><Document file={`/api/v1/documents/${doc.document_id}/file`} onLoadSuccess={({ numPages }) => setPages(numPages)} loading={<p className="placeholder">正在打开 PDF…</p>} error={<p className="error">无法打开 PDF。请确认原文件仍存在，再重新打开文档。</p>}><Page pageNumber={page} width={Math.min(width, 900) * scale} loading={<p>正在渲染页面…</p>} error={<p className="error">页面渲染失败，请尝试其他页。</p>} /></Document></div>
        </>
        : <>
          <div className="reader-toolbar"><h2>// READER · {formatNames[doc.source_format ?? 'text']}</h2><div className="controls"><a className="btn" href={`/api/v1/documents/${doc.document_id}/file`} download={doc.filename}>下载原文件</a></div></div>
          <TextView doc={doc} focus={focus} box={box} onSelect={captureSelection}/>
        </>}
      {view === 'document' && <p className="reader-hint">选中{isPdf ? '页面上' : ''}的文字，即可针对这段内容提问。</p>}
    </section>
    <Assistant
      scope={{ document_ids: [doc.document_id] }}
      title="// 与文档对话" subtitle="从问题出发，回到原文。"
      disabledNotice={ready ? undefined : doc.status === 'failed' ? '文档处理失败，请在图书馆中重试。' : '文档准备完成后即可提问。'}
      status={<span className="pill">{doc.status !== 'indexed' && ready ? '重新处理中 · 仍可提问' : statusNames[doc.status] || doc.status}</span>}
      placeholder="关于这份文档，你想了解什么？"
      suggestions={['文档讨论了哪些主要问题？', '文中提出的方法是什么？']}
      fallbackName={doc.filename}
      selection={selection} onClearSelection={() => setSelection(null)}
      onOpenSource={openSource} canOpen={isPdf ? !!pages : true}
    />
  </div>;
}
