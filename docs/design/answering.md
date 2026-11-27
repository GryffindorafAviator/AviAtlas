# Answering — Implementation

Implements System Design §10 for single questions. Status: M1. Multi-turn answering: [conversations.md](conversations.md); it reuses the modes, segments, and selection rules below.

Entry: `POST /api/v1/ask` → `QuestionAnsweringService.answer` (`services/qa.py`). Retrieval is hybrid (see [retrieval.md](retrieval.md)).

## Modes

| `mode` | Instructions | No evidence |
|---|---|---|
| `grounded` (default) | Answer only from evidence; cite `[n]` | Model not called; fixed reply |
| `open` | May add own knowledge; mark sections `[LIBRARY]` / `[MODEL]` | Model still called |

Fixed replies: `NO_EVIDENCE` (scope had documents, nothing retrieved), `NO_DOCUMENTS` (empty scope). Grounded mode skips the model here because, given no evidence, it would answer from its own knowledge and break the mode.

## Segments

`answering/segments.py` splits output on line-leading `[LIBRARY]` / `[MODEL]`:

- `grounded`, no markers → one `library` segment.
- `open`, no markers → one `unlabeled` segment. Defaulting it to `library` would present possible model knowledge as document content.
- Text before the first marker → `unlabeled`. Adjacent segments of the same type are merged.

Labels are the model's own claims, not verification. Real-model adherence to markers is untested.

`invalid_citations`: cited numbers outside `1..len(sources)`.

## Selected text

`selection {text ≤ 2000, document_id?, page_number?}` is appended to the retrieval query, since questions like "explain this" carry no searchable terms on their own, and sent to the model as `selected_passage`. The frontend captures it from the PDF text layer.

## Response

`SourceAwareAnswer`: `answer`, `segments`, `sources` (retrieval results), `mode`, `invalid_citations`, `document_ids`, `skipped_document_ids`.
