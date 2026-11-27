"""Verify the citations of a saved answer on request (FR-VER-04, -05, -09, -10)."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from aviatlas.answering.generator import AnswerConfigurationError, AnswerGenerationError
from aviatlas.services.errors import (
    ConversationNotFound, GenerationFailed, GenerationUnavailable, InvalidInput,
)
from aviatlas.services.qa import model_label
from aviatlas.verification.claims import extract_claims, local_checks
from aviatlas.verification.support import (
    INSTRUCTIONS, LIMITATIONS, METHOD, VerificationFailed, judge, parse, payload,
)

STATUSES = ("supported", "conflicting", "insufficient_evidence", "unverified")


class VerificationService:
    def __init__(self, conversations, repository, answerer):
        self.conversations, self.repository, self.answerer = conversations, repository, answerer

    def _message(self, conversation_id: UUID, message_id: UUID):
        message = next((m for m in self.conversations.messages(conversation_id) if m.message_id == message_id), None)
        if message is None or message.role != "assistant":
            raise ConversationNotFound("Answer not found.")
        if message.status not in ("complete", "stopped") or not message.content.strip():
            raise InvalidInput("Only finished answers can be verified.")
        return message

    def verify(self, conversation_id: UUID, message_id: UUID) -> dict:
        message = self._message(conversation_id, message_id)
        segments = [SimpleNamespace(**s) for s in message.payload.get("segments") or []] \
            or [SimpleNamespace(source_type="library", text=message.content)]
        excerpts = {n: {"document": s.document_name, "pages": list(s.page_range) if s.page_range else None,
                        "text": s.content} for n, s in message.sources}
        claims = local_checks(extract_claims(segments), {n: e["text"] for n, e in excerpts.items()})
        started = datetime.now(timezone.utc)
        if claims and excerpts:
            try:
                raw = self.answerer.judge(INSTRUCTIONS, payload(claims, excerpts))
            except AnswerConfigurationError as exc:
                raise GenerationUnavailable(str(exc)) from exc
            except AnswerGenerationError as exc:
                raise GenerationFailed(str(exc)) from exc
            try:
                results = judge(claims, excerpts, parse(raw))
            except VerificationFailed as exc:
                raise GenerationFailed(str(exc)) from exc
        else:
            # Nothing to send: no document claims, or every source was deleted.
            results = judge(claims, excerpts, {})
        claim_rows = []
        for r in results:
            source = dict(message.sources)
            claim_rows.append({
                "index": r.claim.index, "text": r.claim.text, "source_type": r.claim.source_type,
                "citations": r.claim.citations, "hints": r.claim.hints, "status": r.status,
                "model_verdict": r.model_verdict, "reason": r.reason, "notes": r.notes,
                "suggested_citations": r.suggested_citations,
                "evidence": [{**asdict(e), "document_name": source[e.citation].document_name,
                              "page_range": list(source[e.citation].page_range) if source[e.citation].page_range else None}
                             for e in r.evidence]})
        verification_id = uuid4()
        report = {
            "verification_id": str(verification_id), "message_id": str(message_id), "method": METHOD,
            "model": model_label(self.answerer) if claims and excerpts else None,
            "created_at": started.isoformat(), "claims": claim_rows,
            "summary": {s: sum(c["status"] == s for c in claim_rows) for s in STATUSES},
            "unchecked_segments": sum(s.source_type == "model" for s in segments),
            "limitations": LIMITATIONS + (["no_sources"] if not excerpts else []),
        }
        self.repository.add(verification_id, message_id, started, report)
        return report

    def latest(self, message_ids: list[UUID]) -> dict[UUID, dict]:
        return self.repository.latest(message_ids)
