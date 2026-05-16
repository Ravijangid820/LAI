"""Tests for :class:`lai.common.embedding.client.EmbeddingClient` and
:class:`SyncEmbeddingClient`.

HTTP transport mocked via :class:`httpx.MockTransport`; metrics observed
on an isolated :class:`CollectorRegistry`; retry backoff configured tiny
so the suite stays fast.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from prometheus_client import CollectorRegistry

from lai.common.embedding import (
    EmbeddingClient,
    EmbeddingResult,
    SyncEmbeddingClient,
)
from lai.common.embedding.client import (
    _auth_headers,
    _build_request_body,
    _chunk_inputs,
    _classify_http_error,
    _merge_batches,
    _parse_embedding_response,
    _validate_inputs,
)
from lai.common.embedding.config import EmbeddingConfig
from lai.common.embedding.metrics import EmbeddingMetrics
from lai.common.exceptions import (
    EmbeddingCallError,
    EmbeddingDimensionMismatchError,
    EmbeddingInvalidResponseError,
    EmbeddingRetryExhaustedError,
)

DIM = 4  # tiny dimension keeps test payloads readable


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def metrics(registry: CollectorRegistry) -> EmbeddingMetrics:
    return EmbeddingMetrics(registry=registry)


@pytest.fixture
def config() -> EmbeddingConfig:
    """Tiny-backoff config so retry tests don't hang the suite."""
    return EmbeddingConfig(
        base_url="http://test-embed:8000/v1",
        model="test-model",
        dimension=DIM,
        max_retries=2,
        retry_initial_wait_seconds=0.001,
        retry_max_wait_seconds=0.001,
        max_batch_size=4,
        max_input_chars=1024,
    )


def _embedding_response(vectors: list[list[float]]) -> dict[str, object]:
    """Build an OpenAI-shaped embeddings response."""
    return {
        "object": "list",
        "data": [{"object": "embedding", "embedding": vec, "index": i} for i, vec in enumerate(vectors)],
        "model": "test-model",
        "usage": {"prompt_tokens": sum(len(v) for v in vectors), "total_tokens": 0},
    }


def _ones(n: int = DIM, scale: float = 1.0) -> list[float]:
    return [scale] * n


def _mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateInputs:
    @pytest.mark.unit
    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_inputs([], max_input_chars=100)

    @pytest.mark.unit
    def test_non_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            _validate_inputs(["ok", 42], max_input_chars=100)  # type: ignore[list-item]

    @pytest.mark.unit
    def test_oversized_input_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_input_chars"):
            _validate_inputs(["x" * 101], max_input_chars=100)

    @pytest.mark.unit
    def test_within_limit_passes(self) -> None:
        _validate_inputs(["short"], max_input_chars=100)


class TestBuildRequestBody:
    @pytest.mark.unit
    def test_shape(self) -> None:
        body = _build_request_body(model="m", inputs=["a", "b"])
        assert body == {"model": "m", "input": ["a", "b"]}

    @pytest.mark.unit
    def test_input_is_coerced_to_list(self) -> None:
        body = _build_request_body(model="m", inputs=("a", "b"))
        assert isinstance(body["input"], list)


class TestChunkInputs:
    @pytest.mark.unit
    def test_no_chunking_below_batch(self) -> None:
        chunks = _chunk_inputs(["a", "b"], max_batch_size=4)
        assert chunks == [(0, ["a", "b"])]

    @pytest.mark.unit
    def test_exact_boundary(self) -> None:
        chunks = _chunk_inputs(["a", "b"], max_batch_size=2)
        assert chunks == [(0, ["a", "b"])]

    @pytest.mark.unit
    def test_partial_final(self) -> None:
        chunks = _chunk_inputs(["a", "b", "c"], max_batch_size=2)
        assert chunks == [(0, ["a", "b"]), (2, ["c"])]


class TestParseResponse:
    @pytest.mark.unit
    def test_valid(self) -> None:
        raw = _embedding_response([_ones(scale=0.1), _ones(scale=0.2)])
        results = _parse_embedding_response(raw, batch_size=2, expected_dimension=DIM)
        assert results == [
            EmbeddingResult(index=0, embedding=[0.1] * DIM),
            EmbeddingResult(index=1, embedding=[0.2] * DIM),
        ]

    @pytest.mark.unit
    def test_non_dict_rejected(self) -> None:
        with pytest.raises(EmbeddingInvalidResponseError, match="expected a JSON object"):
            _parse_embedding_response(["oops"], batch_size=0, expected_dimension=DIM)

    @pytest.mark.unit
    def test_missing_data_rejected(self) -> None:
        with pytest.raises(EmbeddingInvalidResponseError, match="missing 'data' array"):
            _parse_embedding_response({"object": "list"}, batch_size=1, expected_dimension=DIM)

    @pytest.mark.unit
    def test_wrong_count_rejected(self) -> None:
        raw = _embedding_response([_ones()])
        with pytest.raises(EmbeddingInvalidResponseError, match="expected 2 embeddings"):
            _parse_embedding_response(raw, batch_size=2, expected_dimension=DIM)

    @pytest.mark.unit
    def test_non_dict_entry_rejected(self) -> None:
        raw = {"data": [42]}
        with pytest.raises(EmbeddingInvalidResponseError, match="not an object"):
            _parse_embedding_response(raw, batch_size=1, expected_dimension=DIM)

    @pytest.mark.unit
    def test_missing_index_rejected(self) -> None:
        raw = {"data": [{"embedding": _ones()}]}
        with pytest.raises(EmbeddingInvalidResponseError, match="missing int 'index'"):
            _parse_embedding_response(raw, batch_size=1, expected_dimension=DIM)

    @pytest.mark.unit
    def test_bool_index_rejected(self) -> None:
        raw = {"data": [{"index": True, "embedding": _ones()}]}
        with pytest.raises(EmbeddingInvalidResponseError, match="missing int 'index'"):
            _parse_embedding_response(raw, batch_size=1, expected_dimension=DIM)

    @pytest.mark.unit
    def test_missing_embedding_rejected(self) -> None:
        raw = {"data": [{"index": 0}]}
        with pytest.raises(EmbeddingInvalidResponseError, match="missing list 'embedding'"):
            _parse_embedding_response(raw, batch_size=1, expected_dimension=DIM)

    @pytest.mark.unit
    def test_out_of_range_index_rejected(self) -> None:
        raw = {"data": [{"index": 5, "embedding": _ones()}]}
        with pytest.raises(EmbeddingInvalidResponseError, match="out of range"):
            _parse_embedding_response(raw, batch_size=1, expected_dimension=DIM)

    @pytest.mark.unit
    def test_duplicate_index_rejected(self) -> None:
        raw = {
            "data": [
                {"index": 0, "embedding": _ones()},
                {"index": 0, "embedding": _ones()},
            ]
        }
        with pytest.raises(EmbeddingInvalidResponseError, match="duplicate index"):
            _parse_embedding_response(raw, batch_size=2, expected_dimension=DIM)

    @pytest.mark.unit
    def test_dimension_mismatch_rejected(self) -> None:
        raw = {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}
        with pytest.raises(EmbeddingDimensionMismatchError) as info:
            _parse_embedding_response(raw, batch_size=1, expected_dimension=DIM)
        assert info.value.expected_dimension == DIM
        assert info.value.actual_dimension == 2

    @pytest.mark.unit
    def test_int_values_coerced_to_float(self) -> None:
        raw = {"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]}
        results = _parse_embedding_response(raw, batch_size=1, expected_dimension=DIM)
        assert results[0].embedding == [1.0, 0.0, 0.0, 0.0]
        assert all(isinstance(v, float) for v in results[0].embedding)

    @pytest.mark.unit
    def test_results_ordered_by_index(self) -> None:
        """Even if the server returns out-of-order, output is ordered."""
        raw = {
            "data": [
                {"index": 1, "embedding": _ones(scale=0.2)},
                {"index": 0, "embedding": _ones(scale=0.1)},
            ]
        }
        results = _parse_embedding_response(raw, batch_size=2, expected_dimension=DIM)
        assert results[0].index == 0
        assert results[0].embedding == [0.1] * DIM
        assert results[1].index == 1


class TestMergeBatches:
    @pytest.mark.unit
    def test_offsets_remapped(self) -> None:
        batch_a = [
            EmbeddingResult(index=0, embedding=[0.1] * DIM),
            EmbeddingResult(index=1, embedding=[0.2] * DIM),
        ]
        batch_b = [
            EmbeddingResult(index=0, embedding=[0.3] * DIM),
            EmbeddingResult(index=1, embedding=[0.4] * DIM),
        ]
        merged = _merge_batches([batch_a, batch_b], offsets=[0, 2])
        assert [r.index for r in merged] == [0, 1, 2, 3]
        assert merged[2].embedding == [0.3] * DIM


class TestClassifyHttpError:
    @pytest.mark.unit
    def test_status_and_url_propagate(self) -> None:
        request = httpx.Request("POST", "http://x/embeddings")
        response = httpx.Response(500, text="boom", request=request)
        err = _classify_http_error(
            httpx.HTTPStatusError("boom", request=request, response=response), "http://x/embeddings"
        )
        assert err.status_code == 500
        assert err.url == "http://x/embeddings"
        assert "boom" in str(err)


class TestAuthHeaders:
    @pytest.mark.unit
    def test_no_api_key_returns_empty(self) -> None:
        cfg = EmbeddingConfig()
        assert _auth_headers(cfg) == {}

    @pytest.mark.unit
    def test_api_key_yields_bearer(self) -> None:
        cfg = EmbeddingConfig(api_key="abc")  # type: ignore[arg-type]
        assert _auth_headers(cfg) == {"Authorization": "Bearer abc"}


# ─────────────────────────────────────────────────────────────────────────────
# Async client (transport-mocked)
# ─────────────────────────────────────────────────────────────────────────────


def _ok_handler(vectors_for_size: dict[int, list[list[float]]]) -> Callable[[httpx.Request], httpx.Response]:
    """Return a handler that responds with a canned vector list keyed by batch size."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        n = len(body["input"])
        if n not in vectors_for_size:
            pytest.fail(f"unexpected batch size {n}")
        return httpx.Response(200, json=_embedding_response(vectors_for_size[n]))

    return handler


class TestEmbeddingClientAsync:
    @pytest.mark.unit
    async def test_basic_embed(self, config: EmbeddingConfig, metrics: EmbeddingMetrics) -> None:
        handler = _ok_handler({2: [_ones(scale=0.1), _ones(scale=0.2)]})
        async with EmbeddingClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            results = await client.embed(["a", "b"])
        assert [r.index for r in results] == [0, 1]
        assert results[0].embedding == [0.1] * DIM

    @pytest.mark.unit
    async def test_empty_inputs_rejected(self, config: EmbeddingConfig) -> None:
        async with EmbeddingClient(config, transport=_mock_transport(lambda r: httpx.Response(500))) as client:
            with pytest.raises(ValueError, match="must not be empty"):
                await client.embed([])

    @pytest.mark.unit
    async def test_embed_one_returns_vector(self, config: EmbeddingConfig) -> None:
        handler = _ok_handler({1: [_ones(scale=0.5)]})
        async with EmbeddingClient(config, transport=_mock_transport(handler)) as client:
            vec = await client.embed_one("query")
        assert vec == [0.5] * DIM

    @pytest.mark.unit
    async def test_embed_one_rejects_empty(self, config: EmbeddingConfig) -> None:
        async with EmbeddingClient(config, transport=_mock_transport(lambda r: httpx.Response(500))) as client:
            with pytest.raises(ValueError, match="must not be empty"):
                await client.embed_one("")

    @pytest.mark.unit
    async def test_batching_splits_and_merges(self, config: EmbeddingConfig) -> None:
        # max_batch_size=4 from the fixture; 5 inputs forces 2 calls.
        handler = _ok_handler(
            {
                4: [_ones(scale=i / 10) for i in range(1, 5)],
                1: [_ones(scale=0.5)],
            }
        )
        async with EmbeddingClient(config, transport=_mock_transport(handler)) as client:
            results = await client.embed(["a", "b", "c", "d", "e"])
        assert [r.index for r in results] == [0, 1, 2, 3, 4]
        # The 5th input came from the second batch (size 1) with scale 0.5.
        assert results[4].embedding == [0.5] * DIM

    @pytest.mark.unit
    async def test_retries_on_transient_error(
        self, config: EmbeddingConfig, metrics: EmbeddingMetrics, registry: CollectorRegistry
    ) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, text="cold")
            return httpx.Response(200, json=_embedding_response([_ones()]))

        async with EmbeddingClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            results = await client.embed(["q"])
        assert len(results) == 1
        # One retry metric should have been emitted (we incremented at attempt 2).
        retry_value = registry.get_sample_value(
            "lai_embedding_retries_total",
            labels={"model": "test-model"},
        )
        assert retry_value == 1.0

    @pytest.mark.unit
    async def test_retry_exhaustion(self, config: EmbeddingConfig) -> None:
        def always_500(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        async with EmbeddingClient(config, transport=_mock_transport(always_500)) as client:
            with pytest.raises(EmbeddingRetryExhaustedError) as info:
                await client.embed(["q"])
        assert info.value.attempts == config.max_retries + 1
        assert isinstance(info.value.__cause__, EmbeddingCallError)

    @pytest.mark.unit
    async def test_non_json_response_classified_as_call_error(self, config: EmbeddingConfig) -> None:
        def garbage(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not-json")

        async with EmbeddingClient(config, transport=_mock_transport(garbage)) as client:
            with pytest.raises(EmbeddingRetryExhaustedError):
                await client.embed(["q"])

    @pytest.mark.unit
    async def test_invalid_response_shape_propagates(self, config: EmbeddingConfig) -> None:
        def bad_shape(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": "oops"})

        async with EmbeddingClient(config, transport=_mock_transport(bad_shape)) as client:
            with pytest.raises(EmbeddingInvalidResponseError):
                await client.embed(["q"])

    @pytest.mark.unit
    async def test_dimension_mismatch_increments_counter_and_propagates(
        self,
        config: EmbeddingConfig,
        metrics: EmbeddingMetrics,
        registry: CollectorRegistry,
    ) -> None:
        def wrong_dim(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]},
            )

        async with EmbeddingClient(config, metrics=metrics, transport=_mock_transport(wrong_dim)) as client:
            with pytest.raises(EmbeddingDimensionMismatchError):
                await client.embed(["q"])
        assert (
            registry.get_sample_value(
                "lai_embedding_dimension_mismatch_total",
                labels={"model": "test-model"},
            )
            == 1.0
        )

    @pytest.mark.unit
    async def test_oversized_input_caught_before_call(self, config: EmbeddingConfig) -> None:
        async with EmbeddingClient(config, transport=_mock_transport(lambda r: httpx.Response(200))) as client:
            with pytest.raises(ValueError, match="max_input_chars"):
                await client.embed(["x" * (config.max_input_chars + 1)])

    @pytest.mark.unit
    async def test_auth_header_sent_when_api_key_set(self) -> None:
        seen_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.update(request.headers)
            return httpx.Response(200, json=_embedding_response([_ones()]))

        cfg = EmbeddingConfig(
            base_url="http://x/v1",
            model="test-model",
            dimension=DIM,
            api_key="topsecret",  # type: ignore[arg-type]
            max_input_chars=1024,
        )
        async with EmbeddingClient(cfg, transport=_mock_transport(handler)) as client:
            await client.embed(["q"])
        assert seen_headers.get("authorization") == "Bearer topsecret"


# ─────────────────────────────────────────────────────────────────────────────
# Sync client (parallel surface)
# ─────────────────────────────────────────────────────────────────────────────


class TestSyncEmbeddingClient:
    @pytest.mark.unit
    def test_basic_embed(self, config: EmbeddingConfig) -> None:
        handler = _ok_handler({2: [_ones(scale=0.1), _ones(scale=0.2)]})
        with SyncEmbeddingClient(config, transport=_mock_transport(handler)) as client:
            results = client.embed(["a", "b"])
        assert results[1].embedding == [0.2] * DIM

    @pytest.mark.unit
    def test_embed_one(self, config: EmbeddingConfig) -> None:
        handler = _ok_handler({1: [_ones(scale=0.7)]})
        with SyncEmbeddingClient(config, transport=_mock_transport(handler)) as client:
            vec = client.embed_one("q")
        assert vec == [0.7] * DIM

    @pytest.mark.unit
    def test_empty_inputs_rejected(self, config: EmbeddingConfig) -> None:
        with (
            SyncEmbeddingClient(config, transport=_mock_transport(lambda r: httpx.Response(500))) as client,
            pytest.raises(ValueError, match="must not be empty"),
        ):
            client.embed([])

    @pytest.mark.unit
    def test_embed_one_rejects_empty(self, config: EmbeddingConfig) -> None:
        with (
            SyncEmbeddingClient(config, transport=_mock_transport(lambda r: httpx.Response(500))) as client,
            pytest.raises(ValueError, match="must not be empty"),
        ):
            client.embed_one("")

    @pytest.mark.unit
    def test_retry_exhaustion(self, config: EmbeddingConfig) -> None:
        def always_500(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with (
            SyncEmbeddingClient(config, transport=_mock_transport(always_500)) as client,
            pytest.raises(EmbeddingRetryExhaustedError),
        ):
            client.embed(["q"])

    @pytest.mark.unit
    def test_dimension_mismatch_propagates(
        self,
        config: EmbeddingConfig,
        metrics: EmbeddingMetrics,
        registry: CollectorRegistry,
    ) -> None:
        def wrong_dim(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [1.0]}]},
            )

        with (
            SyncEmbeddingClient(config, metrics=metrics, transport=_mock_transport(wrong_dim)) as client,
            pytest.raises(EmbeddingDimensionMismatchError),
        ):
            client.embed(["q"])
        assert (
            registry.get_sample_value(
                "lai_embedding_dimension_mismatch_total",
                labels={"model": "test-model"},
            )
            == 1.0
        )

    @pytest.mark.unit
    def test_non_json_response_classified(self, config: EmbeddingConfig) -> None:
        with (
            SyncEmbeddingClient(
                config, transport=_mock_transport(lambda r: httpx.Response(200, text="nope"))
            ) as client,
            pytest.raises(EmbeddingRetryExhaustedError),
        ):
            client.embed(["q"])

    @pytest.mark.unit
    def test_invalid_shape_propagates(self, config: EmbeddingConfig) -> None:
        with (
            SyncEmbeddingClient(
                config,
                transport=_mock_transport(lambda r: httpx.Response(200, json={"data": "oops"})),
            ) as client,
            pytest.raises(EmbeddingInvalidResponseError),
        ):
            client.embed(["q"])

    @pytest.mark.unit
    def test_batching(self, config: EmbeddingConfig) -> None:
        handler = _ok_handler(
            {
                4: [_ones(scale=0.1)] * 4,
                1: [_ones(scale=0.9)],
            }
        )
        with SyncEmbeddingClient(config, transport=_mock_transport(handler)) as client:
            results = client.embed(["a", "b", "c", "d", "e"])
        assert results[4].embedding == [0.9] * DIM
