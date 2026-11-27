# Parsing — Implementation

Implements System Design §5.3. Status: M5.

## Formats

| Extension | `source_format` | Parser | Upload check |
|---|---|---|---|
| `.pdf` | `pdf` | Docling, routed by text layer | `%PDF-` in the first 1 KB |
| `.docx` | `docx` | Docling | ZIP containing `word/document.xml` |
| `.md`, `.markdown` | `markdown` | `parsing/text_parser.py` | decodes as text |
| `.txt` | `text` | `parsing/text_parser.py` | decodes as text |

Text encodings: UTF-8 (with or without BOM), then GB18030 (covers GBK/GB2312). Files containing NUL bytes are rejected. A failed check deletes the stored file and record (400). Legacy `.doc` is rejected.

Modules: `parsing/formats.py` (formats, checks), `parsing/router.py` (`FormatRouter`, one shared instance; Docling is created once, on first PDF/DOCX), `parsing/docling_parser.py`, `parsing/text_parser.py`.

## PDF OCR routing

`pages_without_text` counts non-whitespace characters per page with pypdfium2; under 32 means no usable text layer.

| `text_layer` | Condition | Docling |
|---|---|---|
| `native` | every page has text | `do_ocr=False` |
| `mixed` | some pages lack text | OCR of bitmap regions |
| `scanned` | ≥ 50% of pages lack text | full-page OCR |

Engine: Docling's `OcrAutoOptions` (RapidOCR, models bundled with the package, Chinese + English). Stored per document: `text_layer`, `ocr_pages`. Native PDFs skip OCR because OCR adds time and turns text in figures into noise blocks.

Measured: native 11-page paper 16 s; scanned 2 pages 16.5 s.

## Docling output fixes

- Formulas: without formula enrichment Docling leaves `text` empty; the parser uses `orig` (the PDF text layer), e.g. `FFN(x) = max(0, xW1 + b1)W2 + b2`. Before M5 every equation block was empty. LaTeX enrichment is not enabled (extra model, slow on CPU).
- Tables: the Markdown serializer includes the caption, which Docling also yields as a caption item; the copy inside the table is removed. Column padding is collapsed (`compact_table`); it was most of a table's length.

## Markdown and text

Docling's Markdown backend is not used: it splits `$$` blocks into several items and drops LaTeX escapes such as `\,`. The own parser keeps block text verbatim:

| Markdown | Block |
|---|---|
| `#`…`######` | heading (first `#` is the title) |
| `$$ … $$` | equation |
| fenced code | code (fences removed) |
| pipe table with separator row | table |
| `-`, `*`, `+`, `1.` items with indented continuation | list item |
| YAML front matter, horizontal rules | dropped |
| other | paragraph (blank-line separated) |

Plain text: paragraphs split on blank lines; a file without blank lines is one paragraph per line.

Non-PDF blocks have no page provenance. Results locate them by `block_ids`; the reader scrolls to the block.

## Concurrency

Docling conversions run one at a time (`DoclingParser` lock; the router creates a single parser even when documents start together). Docling's layout and table models use the Apple GPU; see [retrieval.md](retrieval.md#embedding-device) for why embeddings run on CPU.
