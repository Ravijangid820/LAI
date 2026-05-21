"""Tests for the multi-document Matter helpers in :mod:`lai.persistence`.

Covers ``add_matter_document`` / ``list_matter_documents`` /
``get_matter_document`` / ``matter_document_path`` / ``save_matter_upload``
— the storage layer behind a session ("Matter") holding many uploaded
documents, each addressable as a stable ``[M-n]`` handle.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lai import persistence

SID = "matter-1"
UID = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def db(tmp_path: Path):
    persistence._STATE["conn"] = None
    persistence._STATE["uploads_dir"] = None
    persistence.init(tmp_path / "t.db", tmp_path / "uploads")
    persistence.save_session(SID, {
        "user_id": UID, "filename": "lease.pdf", "contract_text": "lease",
        "n_pages": 3, "tables": [], "uploaded_at": time.time(),
        "clauses": None, "analysis": None, "upload_ext": ".pdf",
    })
    yield
    conn = persistence._STATE.get("conn")
    if conn is not None:
        conn.close()
    persistence._STATE["conn"] = None
    persistence._STATE["uploads_dir"] = None


@pytest.mark.unit
def test_doc_index_is_stable_and_sequential(db) -> None:
    d1 = persistence.add_matter_document(SID, filename="a.pdf", doc_text="A", n_pages=1, upload_ext=".pdf", user_id=UID)
    d2 = persistence.add_matter_document(SID, filename="b.pdf", doc_text="B", n_pages=2, upload_ext=".pdf", user_id=UID)
    d3 = persistence.add_matter_document(SID, filename="c.pdf", doc_text="C", n_pages=3, upload_ext=".pdf", user_id=UID)
    assert (d1["doc_index"], d2["doc_index"], d3["doc_index"]) == (1, 2, 3)


@pytest.mark.unit
def test_list_omits_text_by_default_includes_on_request(db) -> None:
    persistence.add_matter_document(SID, filename="a.pdf", doc_text="secret text", n_pages=1, upload_ext=".pdf", user_id=UID)
    light = persistence.list_matter_documents(SID, user_id=UID)
    assert "doc_text" not in light[0]
    heavy = persistence.list_matter_documents(SID, user_id=UID, include_text=True)
    assert heavy[0]["doc_text"] == "secret text"


@pytest.mark.unit
def test_list_ordered_by_doc_index(db) -> None:
    for i, n in enumerate(["x.pdf", "y.pdf", "z.pdf"]):
        persistence.add_matter_document(SID, filename=n, doc_text=str(i), n_pages=1, upload_ext=".pdf", user_id=UID)
    docs = persistence.list_matter_documents(SID, user_id=UID)
    assert [d["doc_index"] for d in docs] == [1, 2, 3]
    assert [d["filename"] for d in docs] == ["x.pdf", "y.pdf", "z.pdf"]


@pytest.mark.unit
def test_cross_tenant_add_and_list_blocked(db) -> None:
    assert persistence.add_matter_document(SID, filename="a.pdf", doc_text="A", n_pages=1, upload_ext=".pdf", user_id=OTHER) is None
    persistence.add_matter_document(SID, filename="a.pdf", doc_text="A", n_pages=1, upload_ext=".pdf", user_id=UID)
    assert persistence.list_matter_documents(SID, user_id=OTHER) == []
    assert len(persistence.list_matter_documents(SID, user_id=UID)) == 1


@pytest.mark.unit
def test_get_matter_document_by_index(db) -> None:
    persistence.add_matter_document(SID, filename="a.pdf", doc_text="A", n_pages=1, upload_ext=".pdf", user_id=UID)
    persistence.add_matter_document(SID, filename="b.pdf", doc_text="B", n_pages=9, upload_ext=".pdf", user_id=UID)
    g = persistence.get_matter_document(SID, 2, user_id=UID)
    assert g and g["filename"] == "b.pdf" and g["n_pages"] == 9
    assert persistence.get_matter_document(SID, 99, user_id=UID) is None


@pytest.mark.unit
def test_file_path_roundtrip(db) -> None:
    d = persistence.add_matter_document(SID, filename="permit.pdf", doc_text="x", n_pages=1, upload_ext=".pdf", user_id=UID)
    persistence.save_matter_upload(SID, d["id"], b"%PDF-1.4 fake", "permit.pdf")
    p = persistence.matter_document_path(SID, d["id"], ".pdf")
    assert p is not None and p.exists()
    # ext-agnostic lookup also finds it
    assert persistence.matter_document_path(SID, d["id"]) is not None


@pytest.mark.unit
def test_cascade_delete_clears_documents(db) -> None:
    persistence.add_matter_document(SID, filename="a.pdf", doc_text="A", n_pages=1, upload_ext=".pdf", user_id=UID)
    assert persistence.delete_session(SID, user_id=UID)
    assert persistence.list_matter_documents(SID) == []
