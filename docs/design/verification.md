# Verification — Implementation

Implements System Design §11 for two methods from §11.3: citation existence and citation support checking. FR-VER-02, -04, -05, -09, -10. Status: M7. Not implemented: cross-validation (FR-VER-06), contradiction search across sources (FR-VER-07), computational checks (FR-VER-08), verification in notes (FR-VER-11).

Modules: `verification/claims.py` (claims, local checks), `verification/support.py` (prompt, parsing, verdict rules), `services/verification.py`, `storage/verification_repository.py`.

## Claims

One claim per sentence of a `library` or `unlabeled` segment, with its citation numbers; Markdown list and heading markers removed; under 8 characters skipped; at most 20 per answer. `model` segments are not checked against documents: their source type already marks them as not from the documents (§11.5); reports count them as `unchecked_segments`.

## Local checks (every answer, no model call)

Stored with each answer as `checks` (conversation messages and `/api/v1/ask`):

| Hint | Meaning |
|---|---|
| `uncited` | document-sourced sentence without a citation |
| `unknown_citation:n` | cites a source number that was not returned |
| `number_not_in_source:x` | a number (2+ digits, decimals, percentages) absent from the cited text |

Hints are not verdicts: a number may be computed or written differently.

## Citation support check (on request)

`POST /api/v1/conversations/{id}/messages/{message_id}/verification`. One model call with every claim and the answer's stored excerpts; the model returns JSON verdicts with verbatim quotes.

| Model verdict | Status (PRD §7.2) | Condition |
|---|---|---|
| supported | `supported` | at least one quote found in a cited excerpt |
| contradicted | `conflicting` | same |
| partial, not_supported | `insufficient_evidence` | — |
| supported / contradicted without a found quote | `insufficient_evidence` | note `quote_not_found` / `no_verified_quote` |
| quote only from an uncited excerpt | `insufficient_evidence` | `suggested_citations` lists it |
| claim not returned by the model | `unverified` | `not_judged` |
| all cited sources deleted | `unverified` | `source_removed`; no model call if no source is left |

Uncited claims are judged against all excerpts; a supported one keeps note `uncited`.

The quote check keeps a model's judgement of a model's answer from counting as proof on its own: quotes are matched case- and whitespace-insensitively; `...` separates parts, each of which must occur. In a live run the judge quoted a sentence from a different chunk than the cited one; the claim was correctly reported `insufficient_evidence`.

A failed call or unreadable output is an HTTP error (502/503) and stores nothing: a failure is never shown as a result (§11.5).

Reports are stored per run in `verifications(id, message_id FK messages ON DELETE CASCADE, created_at, payload)`; the conversation returns the newest per message. Each report lists `limitations`: `model_judgement`, `cited_text_only`, `no_cross_validation`, and `no_sources` when applicable.

Privacy: the request sends the claims and the answer's excerpts — the same data as answering.
