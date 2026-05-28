"""Structured logging for the LAI platform.

Provides consistent, JSON-friendly logging with per-component context,
request tracing, and per-operation timing via the trace_operation context manager.
"""

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class StructuredFormatter(logging.Formatter):
    """Log formatter with consistent structure for parsing and monitoring."""

    def format(self, record: logging.LogRecord) -> str:
        # Add default extras if missing
        component = getattr(record, "component", "")
        request_id = getattr(record, "request_id", "")

        prefix = f"{self.formatTime(record)} {record.levelname:<8}"
        if request_id:
            prefix += f" [{request_id[:8]}]"
        if component:
            prefix += f" {component}"
        else:
            prefix += f" {record.name}"

        return f"{prefix} {record.getMessage()}"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging(
    level: int = logging.INFO,
    *,
    json_output: bool = False,
    log_name: str | None = None,
    log_dir: str | None = None,
) -> str | None:
    """Configure logging for the LAI platform.

    Args:
        level: Log level (default INFO).
        json_output: If True, use JSON-structured output (for production).
        log_name: Name for the log file (e.g., "step1_dd_reports").
                  If provided, logs are saved to a file automatically.
        log_dir: Directory for log files. Defaults to LAI/logs/pipeline/.

    Returns:
        Path to the log file if file logging is enabled, None otherwise.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    formatter = StructuredFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    if json_output:
        formatter = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (if log_name provided)
    log_file_path = None
    if log_name:
        if log_dir is None:
            # Default: LAI/logs/pipeline/<step>/ relative to project root
            project_root = Path(__file__).resolve().parents[3]  # src/lai/core/ -> LAI/
            # Extract step name (first part before _) for subdirectory
            step_dir = log_name.split("_")[0] if "_" in log_name else log_name
            log_dir = str(project_root / "logs" / "pipeline" / step_dir)

        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        log_file_path = os.path.join(log_dir, f"{log_name}_{timestamp}.log")

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(StructuredFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
        root.addHandler(file_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return log_file_path


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific component.

    Usage:
        logger = get_logger("lai.retrieval.hybrid_search")
        logger.info("Search completed", extra={"request_id": req_id})
    """
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------


@dataclass
class TokenUsage:
    """Token usage from an LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


@dataclass
class OperationMetrics:
    """Metrics from a single pipeline operation."""

    operation_name: str
    duration_ms: float = 0.0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "operation": self.operation_name,
            "duration_ms": round(self.duration_ms, 1),
            "tokens": self.token_usage.total_tokens,
            "success": self.success,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Tracing context manager
# ---------------------------------------------------------------------------


class _OperationContext:
    """Internal context for a traced operation."""

    __slots__ = (
        "_end_time",
        "_error",
        "_start_time",
        "_success",
        "_token_usage",
        "_warnings",
        "operation_name",
        "request_id",
    )

    def __init__(self, operation_name: str, request_id: str) -> None:
        self.operation_name = operation_name
        self.request_id = request_id
        self._start_time = time.perf_counter()
        self._end_time: float | None = None
        self._token_usage = TokenUsage()
        self._success = True
        self._error: str | None = None
        self._warnings: list[str] = []

    @property
    def elapsed_ms(self) -> float:
        end = self._end_time or time.perf_counter()
        return (end - self._start_time) * 1000

    def record_tokens(self, usage: TokenUsage) -> None:
        self._token_usage = self._token_usage + usage

    def add_warning(self, msg: str) -> None:
        self._warnings.append(msg)
        _logger.warning(
            "[%s] %s WARNING: %s",
            self.request_id[:8],
            self.operation_name,
            msg,
        )

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    @property
    def metrics(self) -> OperationMetrics:
        return OperationMetrics(
            operation_name=self.operation_name,
            duration_ms=self.elapsed_ms,
            token_usage=self._token_usage,
            success=self._success,
            error=self._error,
        )


_logger = get_logger("lai.tracing")


@asynccontextmanager
async def trace_operation(
    operation_name: str,
    request_id: str,
) -> AsyncIterator[_OperationContext]:
    """Context manager for tracing a pipeline operation with timing and token tracking.

    Usage:
        async with trace_operation("retrieve", request_id) as ctx:
            # do work
            ctx.record_tokens(usage)
        metrics = ctx.metrics
    """
    ctx = _OperationContext(operation_name, request_id)
    _logger.info("[%s] %s START", request_id[:8], operation_name)
    try:
        yield ctx
        ctx._success = True
    except Exception as e:
        ctx._success = False
        ctx._error = str(e)
        _logger.error("[%s] %s ERROR: %s", request_id[:8], operation_name, e)
        raise
    finally:
        ctx._end_time = time.perf_counter()
        _logger.info(
            "[%s] %s END (%.1fms, %d tokens, %s)",
            request_id[:8],
            operation_name,
            ctx.elapsed_ms,
            ctx._token_usage.total_tokens,
            "OK" if ctx._success else "FAIL",
        )
