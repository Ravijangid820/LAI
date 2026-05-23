"""Tests for the feedback helpers in :mod:`lai.persistence`.

Covers the three public functions added for POST /feedback:

* :func:`record_feedback` — upsert keyed on ``(user_id, session_id, message_id)``
* :func:`list_feedback` — newest-first, scoped to ``user_id``
* :func:`message_belongs_to_session` — referential-integrity guard the
  /feedback route runs before calling ``record_feedback``

Each test uses a temporary SQLite via :func:`persistence.init` so the
production DB is never touched. The module-level ``_STATE`` dict is
reset between tests to keep them order-independent.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lai import persistence


SID = "sess-feedback-1"
UID = "11111111-1111-1111-1111-111111111111"
OTHER_UID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def fresh_db(tmp_path: Path):
    """A clean SQLite + uploads dir per test. Resets the module state so
    successive tests don't carry rows over."""
    persistence._STATE["conn"] = None
    persistence._STATE["uploads_dir"] = None
    persistence.init(tmp_path / "test.db", tmp_path / "uploads")
    yield
    # Tear down: close the connection so the temp dir is cleanable.
    conn = persistence._STATE.get("conn")
    if conn is not None:
        conn.close()
    persistence._STATE["conn"] = None
    persistence._STATE["uploads_dir"] = None


@pytest.fixture
def session_with_message(fresh_db):
    """Create one session owned by ``UID`` with one assistant message."""
    persistence.save_session(SID, {
        "user_id": UID, "filename": None, "contract_text": None,
        "n_pages": 0, "tables": [], "uploaded_at": time.time(),
        "clauses": None, "analysis": None,
    })
    mid = persistence.add_message(SID, "assistant", "hello", mode="rag", user_id=UID)
    assert mid > 0
    return mid


@pytest.mark.unit
def test_record_feedback_inserts_row(session_with_message: int) -> None:
    mid = session_with_message
    fid = persistence.record_feedback(
        session_id=SID, user_id=UID, rating=1, message_id=mid,
        reason="wrong-citation", comment="bad source",
    )
    assert isinstance(fid, int) and fid > 0
    rows = persistence.list_feedback(SID, user_id=UID)
    assert len(rows) == 1
    assert rows[0]["rating"] == 1
    assert rows[0]["reason"] == "wrong-citation"
    assert rows[0]["comment"] == "bad source"
    assert rows[0]["message_id"] == mid


@pytest.mark.unit
def test_record_feedback_upsert_preserves_id(session_with_message: int) -> None:
    """Re-submitting on the same (user, session, message) edits in place."""
    mid = session_with_message
    fid_first = persistence.record_feedback(
        session_id=SID, user_id=UID, rating=1, message_id=mid,
    )
    fid_second = persistence.record_feedback(
        session_id=SID, user_id=UID, rating=-1, message_id=mid,
        reason="hallucination",
    )
    assert fid_first == fid_second, "ON CONFLICT DO UPDATE must keep the id stable"
    rows = persistence.list_feedback(SID, user_id=UID)
    assert len(rows) == 1
    assert rows[0]["rating"] == -1
    assert rows[0]["reason"] == "hallucination"


@pytest.mark.unit
def test_record_feedback_null_message_id_is_session_level(
    session_with_message: int,
) -> None:
    """A NULL message_id is treated as session-level — distinct from a
    rated bubble. Both must coexist (no spurious unique-violation)."""
    mid = session_with_message
    fid_bubble = persistence.record_feedback(
        session_id=SID, user_id=UID, rating=1, message_id=mid,
    )
    fid_session = persistence.record_feedback(
        session_id=SID, user_id=UID, rating=-1, message_id=None,
        comment="overall not great",
    )
    assert fid_bubble != fid_session
    rows = persistence.list_feedback(SID, user_id=UID)
    assert {r["message_id"] for r in rows} == {mid, None}


@pytest.mark.unit
def test_record_feedback_cross_tenant_dropped(session_with_message: int) -> None:
    mid = session_with_message
    fid = persistence.record_feedback(
        session_id=SID, user_id=OTHER_UID, rating=1, message_id=mid,
    )
    assert fid is None
    # The owner of the session sees nothing — the cross-tenant call
    # must not have leaked a row in.
    rows = persistence.list_feedback(SID, user_id=UID)
    assert rows == []


@pytest.mark.unit
def test_list_feedback_filters_by_user(session_with_message: int) -> None:
    """list_feedback scoped to a non-owner user returns empty (matches
    the no-leak semantics of list_messages)."""
    mid = session_with_message
    persistence.record_feedback(
        session_id=SID, user_id=UID, rating=1, message_id=mid,
    )
    assert persistence.list_feedback(SID, user_id=OTHER_UID) == []
    assert len(persistence.list_feedback(SID, user_id=UID)) == 1
    # Unscoped read sees the row (admin / cross-user audit path).
    assert len(persistence.list_feedback(SID)) == 1


@pytest.mark.unit
def test_message_belongs_to_session_referential_check(
    session_with_message: int,
) -> None:
    mid = session_with_message
    assert persistence.message_belongs_to_session(mid, SID)
    assert not persistence.message_belongs_to_session(mid, "other-sid")
    assert not persistence.message_belongs_to_session(999_999, SID)


@pytest.mark.unit
def test_session_cascade_deletes_feedback(session_with_message: int) -> None:
    """The feedback table has ON DELETE CASCADE on session_id —
    deleting the parent session must clear its feedback."""
    mid = session_with_message
    persistence.record_feedback(
        session_id=SID, user_id=UID, rating=1, message_id=mid,
    )
    assert persistence.delete_session(SID, user_id=UID)
    assert persistence.list_feedback(SID) == []


@pytest.mark.unit
def test_init_is_idempotent(fresh_db) -> None:
    """Re-running init() against the same DB must not error.

    Old DBs may predate the feedback unique index — re-init re-creates
    it with IF NOT EXISTS, so the second call should be a no-op."""
    conn = persistence._STATE["conn"]
    db_path = Path([r[0] for r in conn.execute("PRAGMA database_list")][0][1]) if False else None  # noqa
    # Just call init() with the same paths the fixture used.
    uploads_dir = persistence._STATE["uploads_dir"]
    # Discover the db path from sqlite_master indirectly — we kept it
    # in the fixture, but reading it via PRAGMA database_list is robust.
    row = conn.execute("PRAGMA database_list").fetchone()
    db_path = Path(row["file"])
    conn.close()
    persistence._STATE["conn"] = None
    persistence._STATE["uploads_dir"] = None
    persistence.init(db_path, uploads_dir)
    # If we got here, re-init didn't raise; the unique index still exists.
    idx = persistence._STATE["conn"].execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_feedback_user_session_msg'"
    ).fetchone()
    assert idx is not None
