from __future__ import annotations

from research_memory.config import CHUNK_OVERLAP, CHUNK_SIZE
from research_memory.schema import TextChunk


def refine_chunks(raw: list[TextChunk]) -> list[TextChunk]:
    """Split oversized extractor chunks into citeable windows."""
    out: list[TextChunk] = []
    idx = 0
    for piece in raw:
        text = piece.text.strip()
        if not text:
            continue
        if len(text) <= CHUNK_SIZE:
            out.append(
                TextChunk(
                    text=text,
                    location=piece.location,
                    chunk_index=idx,
                    page=piece.page,
                )
            )
            idx += 1
            continue
        start = 0
        part = 1
        while start < len(text):
            end = min(len(text), start + CHUNK_SIZE)
            window = text[start:end].strip()
            if window:
                loc = piece.location if part == 1 else f"{piece.location} · part {part}"
                out.append(
                    TextChunk(
                        text=window,
                        location=loc,
                        chunk_index=idx,
                        page=piece.page,
                    )
                )
                idx += 1
                part += 1
            if end >= len(text):
                break
            start = max(0, end - CHUNK_OVERLAP)
    return out
