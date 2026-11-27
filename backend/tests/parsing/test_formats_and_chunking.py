"""Upload formats, Markdown/TXT parsing, PDF OCR routing, and structure-aware chunking."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aviatlas.chunking.structured_chunker import StructuredChunker, split_sentences
from aviatlas.parsing import docling_parser
from aviatlas.parsing.formats import UnsupportedFormat, check_content, decode_text, source_format
from aviatlas.parsing.models import Block, BlockType as T
from aviatlas.parsing.text_parser import parse_markdown, parse_text


def test_formats_are_checked_by_extension_and_content(tmp_path):
    assert [source_format(n) for n in ['a.PDF', 'b.docx', 'c.md', 'd.markdown', 'e.txt']] == ['pdf', 'docx', 'markdown', 'markdown', 'text']
    for name in ['a.doc', 'b.exe', 'noext', None]:
        with pytest.raises(UnsupportedFormat):
            source_format(name)
    fake = tmp_path / 'fake.pdf'
    fake.write_bytes(b'plain text')
    with pytest.raises(UnsupportedFormat, match='PDF'):
        check_content(fake, 'pdf')
    with pytest.raises(UnsupportedFormat, match='DOCX'):
        check_content(fake, 'docx')
    binary = tmp_path / 'b.txt'
    binary.write_bytes(b'abc\x00def')
    with pytest.raises(UnsupportedFormat):
        check_content(binary, 'text')
    assert decode_text('角速度'.encode('gbk')) == '角速度'
    assert decode_text('﻿角速度'.encode('utf-8')) == '角速度'


def test_markdown_keeps_formulas_code_and_tables_verbatim():
    doc = parse_markdown('---\ntitle: x\n---\n# Rotation\n\nIntro line one\nline two.\n\n## Angular velocity\n\n'
                         '$$\n\\omega = \\frac{d\\theta}{dt} \\, dt\n$$\n\n$$E=mc^2$$\n\n'
                         '| q | unit |\n|---|---|\n| ω | rad/s |\n\n- first\n  continued\n- second\n\n'
                         '```python\nprint(1)\n\n# not a heading\n```\n\n---\n\nEnd.')
    kinds = [(b.type, b.content) for b in doc.blocks]
    assert doc.metadata.title == 'Rotation' and doc.metadata.source_format == 'markdown'
    assert kinds == [
        (T.HEADING, 'Rotation'), (T.PARAGRAPH, 'Intro line one\nline two.'), (T.HEADING, 'Angular velocity'),
        (T.EQUATION, '$$\n\\omega = \\frac{d\\theta}{dt} \\, dt\n$$'), (T.EQUATION, '$$E=mc^2$$'),
        (T.TABLE, '| q | unit |\n|---|---|\n| ω | rad/s |'), (T.LIST_ITEM, '- first\n  continued'),
        (T.LIST_ITEM, '- second'), (T.CODE, 'print(1)\n\n# not a heading'), (T.PARAGRAPH, 'End.')]
    assert [b.order for b in doc.blocks] == list(range(len(doc.blocks)))


def test_plain_text_paragraphs():
    assert [b.content for b in parse_text('First para\nstill first.\n\n\nSecond.\r\n').blocks] == ['First para\nstill first.', 'Second.']
    # No blank lines: one line per paragraph.
    assert [b.content for b in parse_text('一行。\n二行。\n').blocks] == ['一行。', '二行。']


def test_pdf_is_routed_by_text_layer(monkeypatch, tmp_path):
    assert docling_parser.text_layer(10, []) == 'native'
    assert docling_parser.text_layer(10, [3]) == 'mixed'
    assert docling_parser.text_layer(10, list(range(1, 6))) == 'scanned'
    formula = SimpleNamespace(label=docling_parser.DocItemLabel.FORMULA, text='', orig='FFN(x) =  max(0, xW1)')
    document = SimpleNamespace(pages={1: object()}, iterate_items=lambda: iter([(formula, 0)]))
    used = []
    parser = docling_parser.DoclingParser()
    parser._converter = lambda layer: used.append(layer) or SimpleNamespace(convert=lambda p: SimpleNamespace(document=document))
    monkeypatch.setattr(docling_parser, 'pages_without_text', lambda path: (4, [2, 3]))
    parsed = parser.parse(tmp_path / 'scan.pdf')
    assert used == ['scanned'] and parsed.metadata.text_layer == 'scanned' and parsed.metadata.ocr_pages == [2, 3]
    # Formula text comes from the text layer when Docling leaves it empty.
    assert parsed.blocks[0].type == T.EQUATION and parsed.blocks[0].content == 'FFN(x) = max(0, xW1)'
    monkeypatch.setattr(docling_parser, 'pages_without_text', lambda path: (4, []))
    assert parser.parse(tmp_path / 'text.pdf').metadata.ocr_pages == [] and used[-1] == 'native'


def test_converter_options_follow_the_route():
    parser = docling_parser.DoclingParser()
    from docling.datamodel.base_models import InputFormat
    options = {layer: parser._converter(layer).format_to_options[InputFormat.PDF].pipeline_options
               for layer in ['native', 'mixed', 'scanned']}
    assert [(o.do_ocr, o.ocr_options.force_full_page_ocr) for o in options.values()] == [(False, False), (True, False), (True, True)]


def test_docx_is_parsed_with_headings_lists_and_tables(tmp_path):
    import docx
    document = docx.Document()
    document.add_heading('旋转动力学', 0)
    document.add_heading('角速度', 1)
    document.add_paragraph('角速度是矢量。')
    table = document.add_table(rows=2, cols=2)
    for cell, text in zip(table._cells, ['量', '单位', '角速度', 'rad/s']):
        cell.text = text
    path = tmp_path / 'notes.docx'
    document.save(path)
    parsed = docling_parser.DoclingParser().parse(path)
    assert parsed.metadata.title == '旋转动力学' and parsed.metadata.source_format == 'docx'
    assert [b.type for b in parsed.blocks] == [T.HEADING, T.HEADING, T.PARAGRAPH, T.TABLE]
    assert 'rad/s' in parsed.blocks[-1].content and not parsed.blocks[0].provenance


def block(kind, content):
    return Block(id=uuid4(), type=kind, content=content, order=0)


def test_tables_get_their_own_chunk_with_caption_and_heading():
    heading, intro = block(T.HEADING, 'Results'), block(T.PARAGRAPH, 'We report scores.')
    table, caption = block(T.TABLE, '| m | bleu |\n|---|---|\n| big | 28.4 |'), block(T.CAPTION, 'Table 2: BLEU.')
    after = block(T.PARAGRAPH, 'The big model wins.')
    chunks = StructuredChunker(max_chars=400).chunk([heading, intro, table, caption, after])
    assert [c.content for c in chunks] == [
        'Results\nWe report scores.', 'Table 2: BLEU.\n| m | bleu |\n|---|---|\n| big | 28.4 |', 'The big model wins.']
    assert [c.metadata['kind'] for c in chunks] == ['text', 'table', 'text']
    assert chunks[1].block_ids == [caption.id, table.id] and {c.section_title for c in chunks} == {'Results'}


def test_long_tables_split_by_rows_repeating_caption_and_header():
    rows = '\n'.join(f'| {i} | {"x" * 30} |' for i in range(40))
    table, caption = block(T.TABLE, f'| id | value |\n|---|---|\n{rows}'), block(T.CAPTION, 'Table 9')
    chunks = StructuredChunker(max_chars=300).chunk([block(T.HEADING, 'Data'), table, caption])
    assert len(chunks) > 1 and all(c.metadata['parts'] == len(chunks) for c in chunks)
    assert chunks[0].content.startswith('Data\nTable 9\n| id | value |\n|---|---|\n| 0 |')
    assert all(c.content.split('\n')[c.content.split('\n').index('Table 9') + 1] == '| id | value |' for c in chunks)
    assert all(len(c.content) <= 300 + len('Data\n') for c in chunks)
    body = [line for c in chunks for line in c.content.split('\n') if line.startswith('| ') and 'id' not in line]
    assert body == rows.split('\n')  # every row exactly once, in order


def test_formulas_stay_with_their_text_and_headings_never_end_a_chunk():
    lead = block(T.PARAGRAPH, 'p' * 280 + ' defined as:')
    formula = block(T.EQUATION, 'PE(pos, 2i) = sin(pos / 10000^(2i/d))')
    chunks = StructuredChunker(max_chars=300).chunk([lead, formula, block(T.HEADING, 'Next'), block(T.PARAGRAPH, 'q' * 100)])
    assert chunks[0].content.endswith('defined as:\nPE(pos, 2i) = sin(pos / 10000^(2i/d))')
    assert chunks[1].content.startswith('Next\n') and chunks[1].section_title == 'Next'
    # Short sections merge rather than producing tiny chunks.
    small = StructuredChunker(max_chars=300).chunk([block(T.HEADING, 'A'), block(T.PARAGRAPH, 'a'), block(T.HEADING, 'B'), block(T.PARAGRAPH, 'b')])
    assert [c.content for c in small] == ['A\na\nB\nb']


def test_long_paragraphs_split_at_sentences():
    assert split_sentences('第一句。第二句很长。第三句！', 8) == ['第一句。', '第二句很长。', '第三句！']
    assert split_sentences('One two. Three. Four five six.', 16) == ['One two. Three.', 'Four five six.']
    long = block(T.PARAGRAPH, ' '.join(f'Sentence number {i}.' for i in range(40)))
    chunks = StructuredChunker(max_chars=200).chunk([block(T.HEADING, 'H'), long])
    assert len(chunks) > 1 and all(len(c.content) <= 202 for c in chunks)
    assert chunks[0].content.startswith('H\nSentence number 0.') and all(c.block_ids[-1] == long.id for c in chunks)
    assert ' '.join(c.content.removeprefix('H\n') for c in chunks) == long.content


def test_serialized_tables_lose_padding_and_duplicate_captions():
    table = SimpleNamespace(label=docling_parser.DocItemLabel.TABLE, text='',
                            export_to_markdown=lambda doc: 'Table 2: BLEU  scores.\n\n| Model     |   BLEU |\n|:----------|-------:|\n| big       |   28.4 |',
                            caption_text=lambda doc: 'Table 2: BLEU scores.')
    block = docling_parser.DoclingParser()._create_block(table, SimpleNamespace(pages={}), 0)
    assert block.content == '| Model | BLEU |\n|---|---|\n| big | 28.4 |'


def test_concurrent_first_use_creates_one_docling_parser(tmp_path):
    import threading
    import time
    from aviatlas.parsing.router import FormatRouter
    created = []

    def factory():
        created.append(1)
        time.sleep(0.05)  # widen the window two threads would race through
        return SimpleNamespace(parse=lambda path: path)

    router = FormatRouter(factory)
    threads = [threading.Thread(target=router.parse, args=(tmp_path / f'{i}.pdf',)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(created) == 1


def test_section_title_is_the_section_with_most_text():
    chunks = StructuredChunker(max_chars=1000, min_chars=200).chunk([
        block(T.HEADING, '5.4 Regularization'), block(T.PARAGRAPH, 'Short end of a section.'),
        block(T.HEADING, '6.1 Machine Translation'), block(T.PARAGRAPH, 'r' * 300)])
    assert len(chunks) == 1 and chunks[0].section_title == '6.1 Machine Translation'
