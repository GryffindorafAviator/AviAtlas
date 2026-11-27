"""Split generated answers into source-typed segments and check citation ids.

In open mode the model marks sections with `[LIBRARY]` (supported by the
supplied evidence) and `[MODEL]` (its own background knowledge). Labels are the
model's own account of where content came from, not a verification result.
"""
import re

from aviatlas.services.models import AnswerMode, AnswerSegment

LIBRARY, MODEL = "[LIBRARY]", "[MODEL]"
_MARKER = re.compile(r"^[ \t]*\[(LIBRARY|MODEL)\][ \t]*:?[ \t]*", re.MULTILINE)
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def split_segments(raw: str, mode: AnswerMode) -> list[AnswerSegment]:
    parts = _MARKER.split(raw)
    preamble, tagged = parts[0].strip(), parts[1:]
    if not tagged:
        if not preamble:
            return []
        # Grounded mode permits only the supplied evidence, so the whole answer
        # is library-grounded by policy; in open mode an unmarked answer cannot
        # be attributed and is reported as such.
        return [AnswerSegment("library" if mode == "grounded" else "unlabeled", preamble)]
    segments = [AnswerSegment("unlabeled", preamble)] if preamble else []
    for label, text in zip(tagged[::2], tagged[1::2]):
        text = text.strip()
        if not text:
            continue
        source_type = "library" if label == "LIBRARY" else "model"
        if segments and segments[-1].source_type == source_type:
            segments[-1] = AnswerSegment(source_type, f"{segments[-1].text}\n\n{text}")
        else:
            segments.append(AnswerSegment(source_type, text))
    return segments


def join_segments(segments: list[AnswerSegment]) -> str:
    return "\n\n".join(segment.text for segment in segments)


def invalid_citations(text: str, source_count: int) -> list[int]:
    cited = {int(n) for group in _CITATION.findall(text) for n in group.split(",")}
    return sorted(n for n in cited if not 1 <= n <= source_count)
