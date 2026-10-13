from __future__ import annotations

from research_memory.kb.index import normalize_hangul_separators, tokenize


def test_normalize_hangul_separators_collapses_hyphen() -> None:
    assert normalize_hangul_separators("한-독 데이터스페이스") == "한독 데이터스페이스"
    assert normalize_hangul_separators("한·독") == "한독"
    # Spaces between Hangul words are kept (phrase tokenization).
    assert normalize_hangul_separators("한 독") == "한 독"


def test_normalize_keeps_latin_hyphens() -> None:
    assert "Manufacturing-X" in normalize_hangul_separators("Manufacturing-X 소개")


def test_tokenize_matches_hyphenated_and_glued() -> None:
    hyphen = set(tokenize("한-독 데이터스페이스 포럼"))
    glued = set(tokenize("한독 데이터스페이스 포럼"))
    assert "한독" in hyphen
    assert "한독" in glued
    assert hyphen == glued


def test_tokenize_strips_korean_particles() -> None:
    tokens = set(tokenize("한독 데이터스페이스 포럼이 뭐야?"))
    assert "한독" in tokens
    assert "포럼" in tokens
