"""Tests for :mod:`ddiq.llm`.

These tests exercise :func:`ddiq.llm.llm_call` and
:func:`ddiq.llm.llm_json` via the ``_FakeLlmClient`` from
:mod:`conftest`. The salvage path inside ``llm_json`` (strip ``json``
fences → ``json.loads`` → ``salvage_json`` → strengthened-prompt
retry → ``{}``) is the core production-readiness contract: the
pipeline must not raise on malformed model output mid-report.

The :func:`ddiq.llm.embed_texts` / :func:`ddiq.llm.embed_single`
tests verify the wrapper behaviour (empty input short-circuits;
batch_size argument is accepted but ignored).
"""

from __future__ import annotations

import ddiq.llm as ddiq_llm


# ── llm_call ─────────────────────────────────────────────────────────


class TestLlmCall:
    def test_returns_client_output(self, patch_llm_singletons) -> None:
        patch_llm_singletons.responses = ["clean response"]
        result = ddiq_llm.llm_call("sys", "user")
        assert result == "clean response"
        # The fake records (system, user, temperature, max_tokens).
        sys_, user, temp, mx = patch_llm_singletons.calls[0]
        assert sys_ == "sys"
        assert user == "user"
        assert temp == 0.1
        assert mx == 2048

    def test_forwards_temperature_and_max_tokens(self, patch_llm_singletons) -> None:
        patch_llm_singletons.responses = ["x"]
        ddiq_llm.llm_call("s", "u", temperature=0.7, max_tokens=512)
        _, _, temp, mx = patch_llm_singletons.calls[0]
        assert temp == 0.7
        assert mx == 512

    def test_returns_empty_string_on_llm_error(self, monkeypatch, mock_llm_client) -> None:
        """The :class:`LlmError` catch is load-bearing: a transport
        failure mid-pipeline must NOT crash the orchestrator. The
        function returns ``""`` and downstream JSON parse fails
        gracefully on empty."""
        mock_llm_client.raise_on_call = True
        monkeypatch.setattr(ddiq_llm, "_LLM_CLIENT", mock_llm_client)
        result = ddiq_llm.llm_call("s", "u")
        assert result == ""

    def test_returns_empty_string_when_responses_exhausted(self, patch_llm_singletons) -> None:
        # No staged responses → fake returns "" → llm_call returns "".
        result = ddiq_llm.llm_call("s", "u")
        assert result == ""


# ── llm_json ─────────────────────────────────────────────────────────


class TestLlmJson:
    def test_parses_clean_json(self, patch_llm_singletons) -> None:
        patch_llm_singletons.responses = ['{"key": "value", "n": 3}']
        out = ddiq_llm.llm_json("s", "u")
        assert out == {"key": "value", "n": 3}

    def test_strips_json_code_fence(self, patch_llm_singletons) -> None:
        """The legacy model output frequently wrapped JSON in ``json``
        fenced blocks even when told not to. The fast path strips the
        opening fence before parsing."""
        patch_llm_singletons.responses = ['```json\n{"a": 1}\n```']
        out = ddiq_llm.llm_json("s", "u")
        assert out == {"a": 1}

    def test_falls_through_to_salvage(self, patch_llm_singletons) -> None:
        """Trailing chatter after the JSON body — :func:`salvage_json`
        extracts the first balanced JSON substring."""
        patch_llm_singletons.responses = [
            'Here is the result: {"ok": true} (let me know if you need more)'
        ]
        out = ddiq_llm.llm_json("s", "u")
        assert out == {"ok": True}

    def test_second_attempt_with_strengthened_prompt(self, patch_llm_singletons) -> None:
        """First response unparseable + unsalvageable → retry with
        the strengthened system prompt. The second response is clean
        and gets returned."""
        patch_llm_singletons.responses = [
            "garbage with no json at all",
            '{"recovered": true}',
        ]
        out = ddiq_llm.llm_json("s", "u")
        assert out == {"recovered": True}
        # Second call's system prompt must include the strengthening
        # suffix — that's the contract the docstring promises.
        second_system, _, _, _ = patch_llm_singletons.calls[1]
        assert "CRITICAL: Return ONLY valid JSON" in second_system

    def test_returns_empty_dict_on_total_failure(self, patch_llm_singletons) -> None:
        """Two parse failures → ``{}`` (never a raise). The orchestrator
        treats ``{}`` as "no data extracted" and continues — losing
        the entire pipeline to a JSONDecodeError mid-report is the
        thing this contract specifically prevents."""
        patch_llm_singletons.responses = ["junk 1", "junk 2"]
        out = ddiq_llm.llm_json("s", "u")
        assert out == {}

    def test_returns_empty_dict_when_llm_returns_empty(self, monkeypatch, mock_llm_client) -> None:
        """Empty string from the LLM client (the path :func:`llm_call`
        takes on :class:`LlmError`) → both attempts return ``None``
        from ``_attempt`` → final fallthrough returns ``{}``."""
        mock_llm_client.raise_on_call = True
        monkeypatch.setattr(ddiq_llm, "_LLM_CLIENT", mock_llm_client)
        assert ddiq_llm.llm_json("s", "u") == {}


# ── Embedding helpers ────────────────────────────────────────────────


class TestEmbeddings:
    def test_embed_texts_returns_vectors(self, patch_llm_singletons, mock_embedding_client) -> None:
        out = ddiq_llm.embed_texts(["alpha", "beta", "gamma"])
        assert len(out) == 3
        # The fake makes each vector ``[index/100] * dim``.
        assert out[0][0] == 0.0
        assert out[1][0] == 0.01
        assert out[2][0] == 0.02

    def test_embed_texts_empty_short_circuits(self, patch_llm_singletons, mock_embedding_client) -> None:
        """Empty input bypasses the client entirely — the real client
        would ``ValueError`` on empty ``inputs``."""
        assert ddiq_llm.embed_texts([]) == []
        assert mock_embedding_client.calls == []

    def test_embed_texts_logs_when_batch_size_overridden(
        self,
        patch_llm_singletons,
        caplog,
    ) -> None:
        """The legacy ``batch_size`` arg is accepted but ignored. A
        non-default value emits a warning so the caller isn't
        silently surprised."""
        import logging
        caplog.set_level(logging.WARNING, logger="ddiq")
        ddiq_llm.embed_texts(["x"], batch_size=16)
        assert any("batch_size=16" in r.message for r in caplog.records)

    def test_embed_single_returns_one_vector(self, patch_llm_singletons, mock_embedding_client) -> None:
        v = ddiq_llm.embed_single("query")
        assert isinstance(v, list)
        assert len(v) == mock_embedding_client.dimension


# ── Singleton accessors ──────────────────────────────────────────────


def test_get_llm_client_returns_singleton(patch_llm_singletons) -> None:
    """``get_llm_client()`` returns the module-level fake the fixture
    just installed. This is the indirection point tests use to verify
    monkeypatching works."""
    assert ddiq_llm.get_llm_client() is patch_llm_singletons


def test_get_embedding_client_returns_singleton(patch_llm_singletons, mock_embedding_client) -> None:
    assert ddiq_llm.get_embedding_client() is mock_embedding_client


def test_extraction_system_includes_german_statutes() -> None:
    """The shared system prompt is load-bearing for every extractor.
    The German DD-lawyer framing AND the BImSchG / BauGB / BNatSchG /
    EEG citation hints must be present — without them the model
    drifts into generic-English Q&A."""
    text = ddiq_llm.EXTRACTION_SYSTEM
    assert "Berufsanwalt" in text
    assert "BImSchG" in text
    assert "BauGB" in text
    assert "BNatSchG" in text
    assert "EEG" in text
    assert "VwGO" in text


def test_env_defaults_are_strings() -> None:
    """The four env-derived URLs/models are strings even when no env
    var is set — the LLM/embedding clients are constructed at import
    time using them. A None here would crash the module load."""
    assert isinstance(ddiq_llm.LLM_URL, str) and ddiq_llm.LLM_URL
    assert isinstance(ddiq_llm.LLM_MODEL, str) and ddiq_llm.LLM_MODEL
    assert isinstance(ddiq_llm.EMBEDDING_URL, str) and ddiq_llm.EMBEDDING_URL
    assert isinstance(ddiq_llm.RERANKER_URL, str) and ddiq_llm.RERANKER_URL
