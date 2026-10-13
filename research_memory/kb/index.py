from __future__ import annotations

import math
import pickle
import re
from collections import Counter
from pathlib import Path
from typing import Any


_TOKEN_RE = re.compile(r"[A-Za-z가-힣][A-Za-z가-힣0-9_\-]{1,}")
# 한-독 / 한·독 → 한독 (spaces kept so phrases still tokenize)
_HANGUL_SEP_RE = re.compile(r"(?<=[가-힣])[\-‐‑‒–—―·ㆍ./]+(?=[가-힣])")
# Longest-first josa/ending strip so 포럼이 → 포럼 (query vs doc match).
_KO_PARTICLES = (
    "에서는",
    "으로는",
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "이랑",
    "랑",
    "이가",
    "을",
    "를",
    "은",
    "는",
    "이",
    "가",
    "에",
    "의",
    "로",
    "와",
    "과",
    "도",
    "만",
)


def normalize_hangul_separators(text: str) -> str:
    """Collapse separators between Hangul so hyphenated names match glued queries."""
    if not text:
        return ""
    return _HANGUL_SEP_RE.sub("", text)


def _strip_korean_particle(token: str) -> str:
    if len(token) <= 2:
        return token
    for particle in _KO_PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def tokenize(text: str) -> list[str]:
    normalized = normalize_hangul_separators(text or "")
    out: list[str] = []
    for raw in _TOKEN_RE.findall(normalized):
        token = raw.lower()
        stripped = _strip_korean_particle(token)
        out.append(stripped)
        # Keep original too when stripping changed it (index + query both benefit).
        if stripped != token:
            out.append(token)
    return out


class TfidfIndex:
    """Minimal TF-IDF index (no sklearn) for Phase 1 retrieval."""

    def __init__(self) -> None:
        self.chunks: list[dict[str, Any]] = []
        self.doc_freqs: Counter[str] = Counter()
        self.tf_list: list[Counter[str]] = []
        self.idf: dict[str, float] = {}
        self.norms: list[float] = []

    def fit(self, chunks: list[dict[str, Any]]) -> "TfidfIndex":
        self.chunks = chunks
        self.tf_list = []
        self.doc_freqs = Counter()
        for ch in chunks:
            tf = Counter(tokenize(ch.get("text", "")))
            self.tf_list.append(tf)
            for term in tf:
                self.doc_freqs[term] += 1
        n = max(len(chunks), 1)
        self.idf = {
            term: math.log((1 + n) / (1 + df)) + 1.0
            for term, df in self.doc_freqs.items()
        }
        self.norms = []
        for tf in self.tf_list:
            acc = 0.0
            for term, freq in tf.items():
                w = (1 + math.log(freq)) * self.idf.get(term, 0.0)
                acc += w * w
            self.norms.append(math.sqrt(acc) or 1.0)
        return self

    def search(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        q_tf = Counter(tokenize(query))
        if not q_tf or not self.chunks:
            return []
        q_weights: dict[str, float] = {}
        q_norm_acc = 0.0
        for term, freq in q_tf.items():
            if term not in self.idf:
                continue
            w = (1 + math.log(freq)) * self.idf[term]
            q_weights[term] = w
            q_norm_acc += w * w
        q_norm = math.sqrt(q_norm_acc) or 1.0

        scored: list[tuple[float, int]] = []
        for i, tf in enumerate(self.tf_list):
            dot = 0.0
            for term, qw in q_weights.items():
                freq = tf.get(term)
                if not freq:
                    continue
                dw = (1 + math.log(freq)) * self.idf[term]
                dot += qw * dw
            score = dot / (q_norm * self.norms[i])
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for score, idx in scored[:top_k]:
            item = dict(self.chunks[idx])
            item["score"] = float(score)
            results.append(item)
        return results

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(
                {
                    "chunks": self.chunks,
                    "doc_freqs": self.doc_freqs,
                    "tf_list": self.tf_list,
                    "idf": self.idf,
                    "norms": self.norms,
                },
                f,
            )

    @classmethod
    def load(cls, path: Path) -> "TfidfIndex":
        with path.open("rb") as f:
            payload = pickle.load(f)
        obj = cls()
        obj.chunks = payload["chunks"]
        obj.doc_freqs = payload["doc_freqs"]
        obj.tf_list = payload["tf_list"]
        obj.idf = payload["idf"]
        obj.norms = payload["norms"]
        return obj
