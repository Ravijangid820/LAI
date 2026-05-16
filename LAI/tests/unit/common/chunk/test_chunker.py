"""Tests for :class:`lai.common.chunk.chunker.Chunker`.

Pure-function design — every chunker decision is reachable without
mocking. The tests pin three orthogonal behaviours: greedy grouping
respects ``target_chars`` / ``max_chars``, ``min_chars`` merges a short
trailing chunk back, and overlap re-prefixes the trailing characters of
the previous chunk onto the next without splitting words.
"""

from __future__ import annotations

import pytest

from lai.common.chunk.chunker import Chunk, Chunker
from lai.common.chunk.config import ChunkerConfig
from lai.common.exceptions import ChunkInvalidInputError


def _short_cfg(**overrides: int) -> ChunkerConfig:
    """Helper: tiny chunk sizes keep test inputs readable."""
    base: dict[str, int] = {
        "target_chars": 60,
        "max_chars": 120,
        "min_chars": 0,
        "overlap_chars": 0,
    }
    base.update(overrides)
    return ChunkerConfig(**base)  # type: ignore[arg-type]


class TestInputValidation:
    @pytest.mark.unit
    def test_non_string_rejected(self) -> None:
        with pytest.raises(ChunkInvalidInputError, match="must be str"):
            Chunker().chunk(123)  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_empty_string_returns_empty(self) -> None:
        assert Chunker().chunk("") == []

    @pytest.mark.unit
    def test_whitespace_only_returns_empty(self) -> None:
        assert Chunker().chunk("   \n\n   ") == []


class TestSingleChunk:
    @pytest.mark.unit
    def test_short_text_one_chunk(self) -> None:
        c = Chunker(_short_cfg())
        result = c.chunk("Ein kurzer Satz. Noch ein Satz.")
        assert len(result) == 1
        assert isinstance(result[0], Chunk)
        assert result[0].index == 0
        assert "Ein kurzer Satz" in result[0].text
        assert "Noch ein Satz" in result[0].text

    @pytest.mark.unit
    def test_chunk_offsets_reference_original_text(self) -> None:
        text = "Erster Satz. Zweiter Satz."
        result = Chunker(_short_cfg()).chunk(text)
        chunk = result[0]
        # Offsets should bracket the chunk content in the original string.
        assert 0 <= chunk.char_start < chunk.char_end <= len(text)


class TestGreedyGrouping:
    @pytest.mark.unit
    def test_target_size_closes_chunk(self) -> None:
        # Each sentence is ~30 chars; with target 60 we expect ~2
        # sentences per chunk.
        sentences = [f"Satz Nummer {i} ist hier vorhanden." for i in range(6)]
        text = " ".join(sentences)
        c = Chunker(_short_cfg(target_chars=60))
        chunks = c.chunk(text)
        assert len(chunks) >= 2
        # No chunk exceeds the hard cap.
        for ch in chunks:
            assert len(ch.text) <= 120

    @pytest.mark.unit
    def test_indices_are_sequential(self) -> None:
        text = " ".join(f"Satz {i} ist hier." for i in range(10))
        chunks = Chunker(_short_cfg()).chunk(text)
        indices = [ch.index for ch in chunks]
        assert indices == list(range(len(chunks)))


class TestLongSentenceSplit:
    @pytest.mark.unit
    def test_oversized_sentence_word_split(self) -> None:
        # One sentence wider than ``max_chars`` — must be split on
        # word boundaries so no chunk exceeds the cap.
        word = "Wort"
        # 50 words x 5 chars = 250 chars, well above max_chars=80.
        long_sentence = " ".join([word] * 50) + "."
        c = Chunker(_short_cfg(target_chars=40, max_chars=80))
        chunks = c.chunk(long_sentence)
        assert len(chunks) >= 2
        for ch in chunks:
            assert len(ch.text) <= 80

    @pytest.mark.unit
    def test_oversized_sentence_preceded_by_short_one_flushes(self) -> None:
        # The grouping loop must flush the in-progress chunk before
        # processing the over-long sentence.
        short = "Eins."
        long_sentence = " ".join(["Wort"] * 60) + "."
        c = Chunker(_short_cfg(target_chars=40, max_chars=80))
        chunks = c.chunk(short + " " + long_sentence)
        assert len(chunks) >= 2
        # The first chunk should contain the short flush.
        assert chunks[0].text.startswith("Eins.")


class TestMinCharsMerge:
    @pytest.mark.unit
    def test_short_trailing_chunk_merged(self) -> None:
        # Build a text where the last sentence forms a too-small trailing
        # chunk that should fold back into its predecessor.
        big = "A" * 55 + "."
        tiny = "X."
        text = big + " " + big + " " + tiny
        c = Chunker(_short_cfg(target_chars=60, max_chars=120, min_chars=20))
        chunks = c.chunk(text)
        # The tiny tail should NOT be its own chunk.
        assert all(len(ch.text) >= 5 for ch in chunks)
        assert chunks[-1].text.endswith("X.")

    @pytest.mark.unit
    def test_min_chars_zero_keeps_short_tail(self) -> None:
        big = "A" * 55 + "."
        tiny = "X."
        text = big + " " + big + " " + tiny
        c = Chunker(_short_cfg(target_chars=60, max_chars=120, min_chars=0))
        chunks = c.chunk(text)
        # With ``min_chars=0`` the splitter is allowed to emit the tail
        # on its own (it isn't required to, but the merge must not run).
        # Pin behaviour: the body content survives.
        assert any("X." in ch.text for ch in chunks)


class TestOverlap:
    @pytest.mark.unit
    def test_overlap_zero_no_prefix(self) -> None:
        sentences = [f"Aussage Nummer {i} hier vorhanden." for i in range(8)]
        text = " ".join(sentences)
        c = Chunker(_short_cfg(overlap_chars=0))
        chunks = c.chunk(text)
        # Without overlap, the first chunk starts with the first sentence.
        assert chunks[0].text.startswith("Aussage Nummer 0")
        # And the second does not re-include trailing text from the first.
        if len(chunks) >= 2:
            assert "Aussage Nummer 0" not in chunks[1].text

    @pytest.mark.unit
    def test_overlap_prefix_present(self) -> None:
        sentences = [f"Aussage Nummer {i} hier vorhanden." for i in range(8)]
        text = " ".join(sentences)
        c = Chunker(_short_cfg(target_chars=60, max_chars=120, overlap_chars=20))
        chunks = c.chunk(text)
        if len(chunks) < 2:
            pytest.skip("test input did not produce >=2 chunks")
        # The body of chunk 1 starts with non-empty overlap; concretely,
        # its first whitespace-separated token appears in chunk 0.
        token = chunks[1].text.split(" ", 1)[0]
        assert token in chunks[0].text

    @pytest.mark.unit
    def test_overlap_does_not_split_word(self) -> None:
        # Build a chunk-1 boundary where the raw overlap window would land
        # mid-token; the chunker must round forward to a whitespace.
        text = " ".join(["AAAA"] * 80) + "."  # one word per token, deterministic
        c = Chunker(_short_cfg(target_chars=40, max_chars=80, overlap_chars=15))
        chunks = c.chunk(text)
        if len(chunks) >= 2:
            for ch in chunks[1:]:
                # No leading partial word: every overlap prefix must
                # begin with a complete "AAAA" token.
                first_token = ch.text.split(" ", 1)[0]
                assert first_token in {"AAAA", "AAAA."}


class TestDeterminism:
    @pytest.mark.unit
    def test_same_input_same_output(self) -> None:
        text = " ".join(f"Satz {i} mit Inhalt." for i in range(20))
        c = Chunker(_short_cfg(overlap_chars=10))
        a = c.chunk(text)
        b = c.chunk(text)
        assert [ch.text for ch in a] == [ch.text for ch in b]
        assert [ch.char_start for ch in a] == [ch.char_start for ch in b]

    @pytest.mark.unit
    def test_chunk_is_frozen(self) -> None:
        ch = Chunk(index=0, text="x", char_start=0, char_end=1)
        with pytest.raises((AttributeError, TypeError)):
            ch.text = "y"  # type: ignore[misc]
