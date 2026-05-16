"""Smoke tests for the ``lai.common`` package.

These tests do not exercise behaviour — they exist so that the CI gate has a
real assertion to evaluate while the package is being built out, and so that
the package's importability and version surface never regress silently.
"""

from __future__ import annotations

import re

import pytest

import lai.common


@pytest.mark.unit
def test_package_imports() -> None:
    """The package imports without side effects and exposes ``__version__``."""
    assert hasattr(lai.common, "__version__")


@pytest.mark.unit
def test_version_is_semver() -> None:
    """``__version__`` follows the semver MAJOR.MINOR.PATCH shape."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", lai.common.__version__) is not None


@pytest.mark.unit
def test_dunder_all_is_a_list() -> None:
    """``__all__`` is a ``list[str]`` — Pylance/mypy rely on this exact shape."""
    assert isinstance(lai.common.__all__, list)
    assert all(isinstance(name, str) for name in lai.common.__all__)
