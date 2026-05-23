"""Phase B-revert (Path A, Step 1) — private-by-default visibility.

After firm-wide sharing was reversed, the data plane is back to
per-creator ownership. These tests pin that invariant:

  1. A user cannot see another user's session — even if both users
     belong to the same firm (org). Membership in an org doesn't grant
     data access; the explicit-share flow (Step 2 of Path A) is what
     widens visibility, not org-membership.
  2. ``org_id`` is still stamped on every new row so the future
     sharing flow + admin/billing queries have org context.

Pure SQLite — no FastAPI, no LLM, no Postgres. Runnable with stdlib only.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import lai.persistence as p

ORG_A = "11111111-1111-1111-1111-111111111111"
ALICE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"  # in Org A
BOB   = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"  # ALSO in Org A — same firm
CARL  = "cccccccc-cccc-cccc-cccc-cccccccccccc"  # different org


def _init():
    d = tempfile.mkdtemp()
    p._STATE["conn"] = None
    p.init(Path(d) / "t.db", Path(d) / "up")


def test_same_org_users_are_still_isolated():
    """Bob and Alice are in the SAME firm. After the Phase B revert, Bob
    cannot see Alice's session — firm-membership alone doesn't grant
    visibility. Explicit sharing (Step 2 of Path A) will widen this."""
    _init()
    p.save_session("sess-alpha", {"user_id": ALICE, "org_id": ORG_A,
                                  "filename": "vertrag.pdf"})
    # Alice (creator) sees it.
    assert p.load_session("sess-alpha", user_id=ALICE) is not None
    assert p.session_exists("sess-alpha", user_id=ALICE) is True
    # Bob (same firm, different user) does NOT — that's the revert.
    assert p.load_session("sess-alpha", user_id=BOB) is None
    assert p.session_exists("sess-alpha", user_id=BOB) is False
    # Carl (different firm) does NOT either.
    assert p.load_session("sess-alpha", user_id=CARL) is None


def test_list_sessions_is_per_user():
    _init()
    # Alice and Bob both in ORG_A.
    p.save_session("a1", {"user_id": ALICE, "org_id": ORG_A, "filename": "a.pdf"})
    p.save_session("a2", {"user_id": ALICE, "org_id": ORG_A, "filename": "b.pdf"})
    p.save_session("b1", {"user_id": BOB,   "org_id": ORG_A, "filename": "c.pdf"})

    # Each user sees only their own — even though they're in the same firm.
    assert {s["id"] for s in p.list_sessions(user_id=ALICE)} == {"a1", "a2"}
    assert {s["id"] for s in p.list_sessions(user_id=BOB)}   == {"b1"}


def test_messages_inherit_session_scope():
    _init()
    p.save_session("sess-alpha", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    # Alice posts — fine.
    assert p.add_message("sess-alpha", "user", "Hallo", user_id=ALICE) > 0
    # Bob (same firm) tries — blocked.
    assert p.add_message("sess-alpha", "user", "leak", user_id=BOB) == 0
    # Bob sees no messages on Alice's session.
    assert p.list_messages("sess-alpha", user_id=BOB) == []
    # Alice does.
    assert len(p.list_messages("sess-alpha", user_id=ALICE)) == 1


def test_matter_documents_per_user_with_org_stamp():
    """add_matter_document takes user_id (visibility check) AND org_id
    (stamp on the row — used by future sharing/admin). Verify both."""
    _init()
    p.save_session("sess-alpha", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    # Alice adds — fine. org_id stamped on the row.
    doc = p.add_matter_document(
        "sess-alpha", filename="anhang.pdf", doc_text="", n_pages=0,
        upload_ext=".pdf", user_id=ALICE, org_id=ORG_A, status="done",
    )
    assert doc is not None and doc["doc_index"] == 1
    # Bob (same firm) tries — blocked.
    assert p.add_matter_document(
        "sess-alpha", filename="evil.pdf", doc_text="", n_pages=0,
        upload_ext=".pdf", user_id=BOB, org_id=ORG_A, status="done",
    ) is None
    # Bob sees no matter docs.
    assert p.list_matter_documents("sess-alpha", user_id=BOB) == []
    # Alice sees hers.
    docs = p.list_matter_documents("sess-alpha", user_id=ALICE)
    assert len(docs) == 1
    # Confirm org_id was actually stamped (read back via direct SQL).
    row = p._conn().execute(
        "SELECT org_id FROM matter_documents WHERE doc_index = 1"
    ).fetchone()
    assert row["org_id"] == ORG_A, "org_id should be persisted for future sharing"


def test_delete_session_per_user():
    _init()
    p.save_session("a1", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    # Bob (same firm) cannot delete Alice's matter — that's the revert.
    assert p.delete_session("a1", user_id=BOB) is False
    assert p.session_exists("a1", user_id=ALICE) is True
    # Alice can.
    assert p.delete_session("a1", user_id=ALICE) is True
    assert p.session_exists("a1", user_id=ALICE) is False


def test_feedback_per_user():
    _init()
    p.save_session("sess-alpha", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    # Alice (owner) can rate her own session.
    rid = p.record_feedback(session_id="sess-alpha", user_id=ALICE, rating=1)
    assert rid is not None and rid > 0
    # Bob (same firm) cannot — silently dropped.
    assert p.record_feedback(session_id="sess-alpha", user_id=BOB, rating=-1) is None
    # Bob sees no feedback; Alice sees hers.
    assert p.list_feedback("sess-alpha", user_id=BOB) == []
    rows = p.list_feedback("sess-alpha", user_id=ALICE)
    assert len(rows) == 1 and rows[0]["user_id"] == ALICE


def test_save_session_round_trips_org_id():
    """The org_id column survives a load → save round-trip via
    _row_to_session, so future explicit-share queries can read it
    without an extra JOIN."""
    _init()
    p.save_session("a1", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    sess = p.load_session("a1", user_id=ALICE)
    assert sess is not None
    assert sess["org_id"] == ORG_A
    # Mutate something irrelevant and re-save; org_id must be preserved.
    sess["filename"] = "renamed.pdf"
    p.save_session("a1", sess)
    sess2 = p.load_session("a1", user_id=ALICE)
    assert sess2["org_id"] == ORG_A, "org_id lost on save→load→save round-trip"


# ───────────────────────────────────────────────────────────────────────────────
# Path A Step 2 — explicit per-session sharing (view-only in v1)
# ───────────────────────────────────────────────────────────────────────────────

def test_share_grants_read_but_not_write():
    """The whole point of Step 2: Alice can grant Bob view access to her
    session. Bob can READ but cannot WRITE — sharing is view-only in v1.
    Carl (no share) still sees nothing."""
    _init()
    p.save_session("sess-1", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})

    # Baseline: Bob and Carl have no access.
    assert p.load_session("sess-1", user_id=BOB) is None
    assert p.load_session("sess-1", user_id=CARL) is None

    # Alice shares with Bob.
    share_id = p.add_session_share("sess-1", BOB, granted_by=ALICE)
    assert share_id is not None and share_id > 0

    # READ widens for Bob.
    assert p.load_session("sess-1", user_id=BOB) is not None
    assert p.session_exists("sess-1", user_id=BOB) is True
    assert {s["id"] for s in p.list_sessions(user_id=BOB)} == {"sess-1"}

    # Carl (no share, different org) still blocked.
    assert p.load_session("sess-1", user_id=CARL) is None
    assert p.session_exists("sess-1", user_id=CARL) is False

    # WRITE stays owner-only — Bob has view, not edit.
    assert p.add_message("sess-1", "user", "from Bob", user_id=BOB) == 0
    assert p.delete_session("sess-1", user_id=BOB) is False
    assert p.update_session_title("sess-1", "stolen", user_id=BOB) is False
    assert p.record_feedback(session_id="sess-1", user_id=BOB, rating=1) is None
    assert p.add_matter_document(
        "sess-1", filename="evil.pdf", doc_text="", n_pages=0,
        upload_ext=".pdf", user_id=BOB, org_id=ORG_A, status="done",
    ) is None

    # Alice (owner) still writes normally.
    assert p.add_message("sess-1", "user", "from Alice", user_id=ALICE) > 0

    # Bob can READ the messages Alice wrote.
    msgs = p.list_messages("sess-1", user_id=BOB)
    assert len(msgs) == 1 and msgs[0]["content"] == "from Alice"


def test_share_is_idempotent_and_owner_only_to_grant():
    """Re-sharing with the same user is a no-op (returns existing row).
    Only the session owner can grant; even a shared collaborator can't
    re-share to someone else."""
    _init()
    p.save_session("sess-1", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})

    first = p.add_session_share("sess-1", BOB, granted_by=ALICE)
    again = p.add_session_share("sess-1", BOB, granted_by=ALICE)
    assert first is not None and again is not None
    # No duplicate row — UNIQUE(session_id, user_id).
    assert p.session_share_user_ids("sess-1") == {BOB}

    # Bob (shared collaborator) cannot re-share Alice's session to Carl.
    assert p.add_session_share("sess-1", CARL, granted_by=BOB) is None
    assert CARL not in p.session_share_user_ids("sess-1")


def test_revoke_removes_access_cleanly():
    """After revoke, Bob falls back to baseline (no access). Alice (owner)
    still sees the session; the revoke is fully reversible by re-sharing."""
    _init()
    p.save_session("sess-1", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    p.add_session_share("sess-1", BOB, granted_by=ALICE)
    assert p.load_session("sess-1", user_id=BOB) is not None

    assert p.revoke_session_share("sess-1", BOB, granted_by=ALICE) is True
    assert p.load_session("sess-1", user_id=BOB) is None
    assert p.session_exists("sess-1", user_id=BOB) is False
    # Alice still has full access — revoking Bob doesn't affect the owner.
    assert p.session_exists("sess-1", user_id=ALICE) is True

    # Non-owner cannot revoke.
    p.add_session_share("sess-1", BOB, granted_by=ALICE)
    assert p.revoke_session_share("sess-1", BOB, granted_by=CARL) is False
    assert p.load_session("sess-1", user_id=BOB) is not None  # Bob still has access


def test_self_share_is_synthesised_idempotent_no_op():
    """Sharing the session with the owner returns the sentinel id 0 — the
    owner already has full access. No row is inserted."""
    _init()
    p.save_session("sess-1", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    rv = p.add_session_share("sess-1", ALICE, granted_by=ALICE)
    assert rv == 0
    assert p.session_share_user_ids("sess-1") == set()


def test_session_owned_by_is_strict_for_writes():
    """The strict-owner gate distinguishes owner from shared — a shared
    user must NOT satisfy session_owned_by even though they satisfy
    session_exists (the read-widened gate)."""
    _init()
    p.save_session("sess-1", {"user_id": ALICE, "org_id": ORG_A, "filename": "x.pdf"})
    p.add_session_share("sess-1", BOB, granted_by=ALICE)
    # Read gate: Bob passes.
    assert p.session_exists("sess-1", user_id=BOB) is True
    # Write gate: Bob does NOT pass.
    assert p.session_owned_by("sess-1", BOB) is False
    # Owner passes both.
    assert p.session_exists("sess-1", user_id=ALICE) is True
    assert p.session_owned_by("sess-1", ALICE) is True


if __name__ == "__main__":
    import traceback
    funcs = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in funcs:
        try:
            fn(); print(f"PASS  {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}"); failed += 1
        except Exception:
            print(f"ERROR {fn.__name__}:"); traceback.print_exc(); failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(0 if failed == 0 else 1)
