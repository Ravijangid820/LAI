"""Tests for the report-persistence helpers in ``ddiq_report``.

* ``_compute_fingerprint`` — the dedup cache key. Pure (hashlib); the
  tenant-scoping + order-independence properties are the security-
  relevant contract (a fingerprint that ignored user_id would let one
  tenant's cache lookup return another's report).

* ``_find_existing_report`` — the dedup lookup. SQL is captured to
  verify the user_id filter is present (defense in depth) and the
  staged row is returned.

* ``_update_report_progress`` — builds a dynamic SET clause. The
  status-transition side effects (started_at on 'running',
  finished_at on 'done'/'failed') and the user scoping are verified
  by inspecting the captured SQL.

* ``_persist_report_jsonb`` — checkpoint UPSERT. Verifies it writes
  report_data and swallows DB errors (a failed checkpoint must not
  kill the pipeline).
"""

from __future__ import annotations

import ddiq_report
from ddiq.models import DDiQReportData


# ── _compute_fingerprint ─────────────────────────────────────────────


class TestComputeFingerprint:
    def test_deterministic(self) -> None:
        a = ddiq_report._compute_fingerprint(["d1", "d2"], "full", "Proj", "user-1")
        b = ddiq_report._compute_fingerprint(["d1", "d2"], "full", "Proj", "user-1")
        assert a == b
        assert len(a) == 64  # sha256 hexdigest

    def test_doc_order_independent(self) -> None:
        """doc_ids are sorted before hashing — the same document set in
        a different order is the same request."""
        a = ddiq_report._compute_fingerprint(["d1", "d2"], "full", "P", "u1")
        b = ddiq_report._compute_fingerprint(["d2", "d1"], "full", "P", "u1")
        assert a == b

    def test_user_scoping(self) -> None:
        """The security-critical property: two tenants with identical
        documents + preset + project get DIFFERENT fingerprints, so a
        cache lookup can never cross tenants."""
        a = ddiq_report._compute_fingerprint(["d1"], "full", "P", "user-1")
        b = ddiq_report._compute_fingerprint(["d1"], "full", "P", "user-2")
        assert a != b

    def test_preset_case_and_whitespace_normalized(self) -> None:
        a = ddiq_report._compute_fingerprint(["d1"], "Full", "P", "u1")
        b = ddiq_report._compute_fingerprint(["d1"], "  full ", "P", "u1")
        assert a == b

    def test_project_name_case_normalized(self) -> None:
        a = ddiq_report._compute_fingerprint(["d1"], "full", "Windpark", "u1")
        b = ddiq_report._compute_fingerprint(["d1"], "full", "windpark", "u1")
        assert a == b

    def test_distinct_inputs_distinct_fingerprints(self) -> None:
        base = ddiq_report._compute_fingerprint(["d1"], "full", "P", "u1")
        assert base != ddiq_report._compute_fingerprint(["d1", "d2"], "full", "P", "u1")
        assert base != ddiq_report._compute_fingerprint(["d1"], "wea_full", "P", "u1")
        assert base != ddiq_report._compute_fingerprint(["d1"], "full", "Q", "u1")

    def test_handles_none_and_empty(self) -> None:
        # None doc_ids / preset / project_name must not raise.
        fp = ddiq_report._compute_fingerprint(None, None, None, "u1")
        assert len(fp) == 64


# ── _find_existing_report ────────────────────────────────────────────


class TestFindExistingReport:
    def test_returns_staged_row(self, fake_db) -> None:
        row = {"id": "r-1", "status": "done", "created_at": None, "started_at": None}
        _, cur = fake_db(fetchone=row)
        out = ddiq_report._find_existing_report("fp-abc", "user-1")
        assert out == row

    def test_query_filters_by_user(self, fake_db) -> None:
        """Defense in depth: even though the fingerprint already
        embeds user_id, the WHERE clause must filter by user_id too."""
        _, cur = fake_db(fetchone=None)
        ddiq_report._find_existing_report("fp", "user-1")
        sql, params = cur.executed[0]
        assert "user_id = %s" in sql
        assert params == ("fp", "user-1")

    def test_none_when_no_row(self, fake_db) -> None:
        fake_db(fetchone=None)
        assert ddiq_report._find_existing_report("fp", "u1") is None


# ── _update_report_progress ──────────────────────────────────────────


class TestUpdateReportProgress:
    def test_no_fields_is_noop(self, fake_db) -> None:
        _, cur = fake_db()
        ddiq_report._update_report_progress("r-1")
        # Nothing to set → must not execute any SQL.
        assert cur.executed == []

    def test_sets_step_and_percent(self, fake_db) -> None:
        _, cur = fake_db()
        ddiq_report._update_report_progress("r-1", step="classifying", percent=0.5)
        sql, params = cur.executed[0]
        assert "progress_step = %s" in sql
        assert "progress_percent = %s" in sql
        assert "classifying" in params
        assert 0.5 in params
        assert params[-1] == "r-1"  # the WHERE id

    def test_running_sets_started_at(self, fake_db) -> None:
        """Transition to 'running' stamps started_at (COALESCE so a
        re-entry doesn't reset it)."""
        _, cur = fake_db()
        ddiq_report._update_report_progress("r-1", status="running")
        sql, _ = cur.executed[0]
        assert "started_at = COALESCE(started_at, NOW())" in sql

    def test_done_sets_finished_at(self, fake_db) -> None:
        _, cur = fake_db()
        ddiq_report._update_report_progress("r-1", status="done", percent=1.0)
        sql, _ = cur.executed[0]
        assert "finished_at = NOW()" in sql

    def test_failed_sets_finished_at(self, fake_db) -> None:
        _, cur = fake_db()
        ddiq_report._update_report_progress("r-1", status="failed", error="boom")
        sql, params = cur.executed[0]
        assert "finished_at = NOW()" in sql
        assert "boom" in params

    def test_user_scoping_in_where(self, fake_db) -> None:
        _, cur = fake_db()
        ddiq_report._update_report_progress("r-1", step="x", user_id="user-9")
        sql, params = cur.executed[0]
        assert "AND user_id = %s" in sql
        assert params[-1] == "user-9"

    def test_db_error_swallowed(self, monkeypatch) -> None:
        """A best-effort progress write must never raise — the
        pipeline keeps going even if the row update fails."""
        def boom():
            raise RuntimeError("pool exhausted")
        monkeypatch.setattr(ddiq_report, "get_conn", boom)
        # Should not raise.
        ddiq_report._update_report_progress("r-1", step="x")


# ── _persist_report_jsonb ────────────────────────────────────────────


class TestPersistReportJsonb:
    def _report(self) -> DDiQReportData:
        return DDiQReportData(
            projectName="P", preparedBy="b", preparedFor="f",
            date="2026-05-19", projectCenter={"lat": 53.0, "lng": 8.0},
        )

    def test_upsert_writes_report_data(self, fake_db) -> None:
        conn, cur = fake_db()
        ddiq_report._persist_report_jsonb(
            "r-1", "Proj", ["d1"], "full", self._report(), "user-1",
        )
        sql, params = cur.executed[0]
        assert "INSERT INTO ddiq_reports" in sql
        assert "ON CONFLICT (id) DO UPDATE" in sql
        # user_id is the 2nd bound param (INSERT column order).
        assert "user-1" in params

    def test_db_error_swallowed(self, monkeypatch) -> None:
        """A checkpoint failure must not kill the pipeline — the next
        checkpoint or the final UPSERT catches up."""
        def boom():
            raise RuntimeError("disk full")
        monkeypatch.setattr(ddiq_report, "get_conn", boom)
        ddiq_report._persist_report_jsonb(
            "r-1", "P", ["d1"], "full", self._report(), "u1",
        )  # no raise
