"""Resumable uploads via the tus 1.0 protocol — self-contained impl.

Why self-contained: ``tuspyserver`` etc. exist but adding a dependency for
~150 lines of well-specified protocol isn't worth the audit cost. The
tus wire format is stable and tightly scoped — POST creates, HEAD reads
offset, PATCH appends, DELETE cancels. Implementing it ourselves keeps
auth, tenant isolation, and the completion hook entirely under our
control, and makes future swaps (e.g. to a ``tusd`` sidecar) a deploy
change rather than a code rewrite.

State storage: filesystem under ``LAI_TUS_STAGING_DIR`` (default
``data/processed/tus_staging``). Each upload is a directory keyed by a
random id; inside, ``data.bin`` holds the bytes appended so far and
``info.json`` holds the metadata (owner, target session, filename,
length, current offset). On a PATCH we verify the offset header matches
``info.json["offset"]`` exactly — out-of-order chunks are rejected with
409 (the client must HEAD to find the truth and resume from there).

Completion hook: when ``offset == length`` after a PATCH, we hand the
assembled bytes off to :func:`_finalize_tus_upload`, which mirrors the
synchronous half of the legacy ``POST /upload``: validate the session,
add a ``matter_documents`` row, persist the file blob, and enqueue
ingestion. The doc_index + session id are returned in the PATCH
response's ``Upload-Metadata`` header so the client gets them back in
the same round-trip (no extra fetch needed).

See: LAI/docs/UPLOAD_RESUMABLE_DESIGN.md for the full design + client
contract + rollout plan.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from lai.common.auth.models import CurrentUser

# ─── Configuration ──────────────────────────────────────────────────────────

TUS_VERSION = "1.0.0"
# Match the legacy /upload cap (100 MB). Surfaced via OPTIONS so a polite
# client refuses an oversized file client-side before any bytes go on the
# wire — saves a 100 MB upload that's going to 413 anyway.
TUS_MAX_SIZE = int(os.environ.get("LAI_TUS_MAX_SIZE", str(100 * 1024 * 1024)))
TUS_STAGING_DIR = Path(
    os.environ.get("LAI_TUS_STAGING_DIR", "data/processed/tus_staging"),
).resolve()
TUS_STAGING_DIR.mkdir(parents=True, exist_ok=True)

# ─── Metadata helpers ───────────────────────────────────────────────────────


def _decode_upload_metadata(header: str | None) -> dict[str, str]:
    """Parse a tus ``Upload-Metadata`` header into a dict.

    Format per spec: ``key1 base64-value1,key2 base64-value2`` (no '=').
    Keys without a value are allowed and carry the empty string.
    """
    out: dict[str, str] = {}
    if not header:
        return out
    for raw in header.split(","):
        parts = raw.strip().split(" ", 1)
        if not parts or not parts[0]:
            continue
        key = parts[0]
        if len(parts) == 1:
            out[key] = ""
            continue
        try:
            out[key] = base64.b64decode(parts[1].encode()).decode("utf-8")
        except Exception:
            out[key] = ""
    return out


def _encode_upload_metadata(kv: dict[str, str]) -> str:
    """Inverse of :func:`_decode_upload_metadata`. Used in the response header
    so the client can pick up the ingest result on the final PATCH."""
    parts: list[str] = []
    for k, v in kv.items():
        b = base64.b64encode((v or "").encode("utf-8")).decode("ascii")
        parts.append(f"{k} {b}")
    return ",".join(parts)


# ─── On-disk state ──────────────────────────────────────────────────────────


def _upload_dir(upload_id: str) -> Path:
    """Path to the upload's working directory. The id is opaque to the
    caller; we validate it as a uuid4 to keep it traversal-safe."""
    # uuid.UUID() raises ValueError on a non-hex/wrong-shape string, which
    # we surface as 404 in the route handlers — never an unhandled crash.
    uuid.UUID(upload_id)
    return TUS_STAGING_DIR / upload_id


def _read_info(upload_id: str) -> dict[str, Any] | None:
    p = _upload_dir(upload_id) / "info.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _write_info(upload_id: str, info: dict[str, Any]) -> None:
    p = _upload_dir(upload_id) / "info.json"
    # Atomic write — partial info.json after a crash would be ambiguous.
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(info))
    os.replace(tmp, p)


def _tenant_guard(info: dict[str, Any] | None, user_id: str) -> dict[str, Any]:
    """Confirm an upload exists AND belongs to the calling user. 404 (not
    403) on cross-user — never leak the existence of another user's upload."""
    if not info or info.get("user_id") != user_id:
        raise HTTPException(404, "Upload not found")
    return info


# ─── Router factory ─────────────────────────────────────────────────────────


def build_tus_router(
    get_current_user: Callable[..., CurrentUser],
    finalize: Callable[[CurrentUser, dict[str, Any], bytes], dict[str, Any]],
) -> APIRouter:
    """Build the tus router.

    :param get_current_user: the same FastAPI dependency every other route
        uses for auth. Each tus verb runs through it, so a long-running
        upload survives token refresh on the client side.
    :param finalize: completion hook. Called with the user, the info dict
        (filename, session_id, length, …), and the assembled bytes. Must
        return a dict with at least ``doc_index`` and ``session_id`` keys
        — those flow back to the client in the Upload-Metadata response
        header on the final PATCH.
    """
    r = APIRouter(tags=["Resumable Upload"])

    def _tus_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
        h: dict[str, str] = {
            "Tus-Resumable": TUS_VERSION,
            "Cache-Control": "no-store",
        }
        if extra:
            h.update(extra)
        return h

    @r.options("/files/")
    async def tus_options() -> Response:
        """Discovery — advertises protocol version + capabilities. The
        client (``tus-js-client``) probes this before the first upload."""
        return Response(
            status_code=204,
            headers=_tus_headers(
                {
                    "Tus-Version": TUS_VERSION,
                    "Tus-Max-Size": str(TUS_MAX_SIZE),
                    # Only the ``creation`` extension is needed for our use
                    # case. We don't implement concatenation, expiration
                    # endpoints, or termination-as-extension (DELETE works
                    # but is core, not announced as the extension).
                    "Tus-Extension": "creation,creation-with-upload",
                }
            ),
        )

    @r.post("/files/")
    async def tus_create(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> Response:
        """Create a new upload. Spec requires ``Upload-Length`` and accepts
        an ``Upload-Metadata`` blob. Optionally accepts the first chunk
        in the same request body (creation-with-upload extension) — we
        support that because it shaves one round-trip on small files."""
        if request.headers.get("Tus-Resumable") != TUS_VERSION:
            raise HTTPException(412, "Unsupported Tus-Resumable")

        length_h = request.headers.get("Upload-Length")
        if length_h is None:
            raise HTTPException(411, "Upload-Length header required")
        try:
            length = int(length_h)
        except ValueError:
            raise HTTPException(400, "Upload-Length must be an integer") from None
        if length < 0 or length > TUS_MAX_SIZE:
            raise HTTPException(413, f"File too large (max {TUS_MAX_SIZE} bytes)")

        meta = _decode_upload_metadata(request.headers.get("Upload-Metadata"))
        # filename + sessionId travel in Upload-Metadata. ``filename`` is
        # required because the ingest hook persists it as
        # ``matter_documents.filename`` and the chat manifest keys on it.
        filename = (meta.get("filename") or "").strip()
        if not filename:
            raise HTTPException(400, "filename metadata is required")
        filename = Path(filename).name or "uploaded.bin"  # strip any path
        session_id = (meta.get("sessionId") or "").strip() or None

        upload_id = str(uuid.uuid4())
        d = _upload_dir(upload_id)
        d.mkdir(parents=True, exist_ok=True)
        # Touch the data file so PATCH can ``append`` without worrying
        # about whether it exists yet.
        (d / "data.bin").touch()

        info: dict[str, Any] = {
            "user_id": str(user.id),
            "org_id": (str(user.org_id) if getattr(user, "org_id", None) else None),
            "session_id": session_id,
            "filename": filename,
            "length": length,
            "offset": 0,
            "created_at": time.time(),
        }
        _write_info(upload_id, info)

        # Return the Location as a path (no scheme+netloc) so it survives a
        # reverse proxy. The full URL would be the BACKEND's loopback
        # (127.0.0.1:18000), which a browser fetched through Vite's /rag
        # proxy can't reach directly — the next PATCH would bypass the
        # proxy and hit CORS. tus 1.0 explicitly allows relative URIs here;
        # tus-js-client resolves them against the request URL.
        location = f"{request.url.path.rstrip('/')}/{upload_id}"

        # creation-with-upload: if the request carries body bytes, treat
        # them as the first chunk and write them now. Saves a round-trip
        # for files smaller than one chunk.
        ct = (request.headers.get("Content-Type") or "").lower()
        body_offset_after = 0
        if ct.startswith("application/offset+octet-stream"):
            data = await request.body()
            if data:
                if len(data) > length:
                    raise HTTPException(
                        413,
                        "Initial chunk longer than declared Upload-Length",
                    )
                (d / "data.bin").write_bytes(data)
                info["offset"] = len(data)
                _write_info(upload_id, info)
                body_offset_after = len(data)

        # If the file was small enough to fit entirely in the create call,
        # finalize now so the client's onSuccess fires immediately.
        result_meta: dict[str, str] = {}
        if body_offset_after == length:
            try:
                finalize_result = finalize(
                    user,
                    info,
                    (d / "data.bin").read_bytes(),
                )
                result_meta = {k: str(v) for k, v in (finalize_result or {}).items()}
            finally:
                _cleanup(upload_id)

        headers = _tus_headers(
            {
                "Location": location,
                "Upload-Offset": str(body_offset_after),
            }
        )
        if result_meta:
            headers["Upload-Metadata"] = _encode_upload_metadata(result_meta)
        return Response(status_code=201, headers=headers)

    @r.head("/files/{upload_id}")
    async def tus_head(
        upload_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> Response:
        """Tell the client where we got to. The whole point of resume:
        on reconnect/reload, the client HEADs here to learn the
        server-known offset, then PATCHes from that point — no re-send
        of bytes already confirmed."""
        if request.headers.get("Tus-Resumable") != TUS_VERSION:
            raise HTTPException(412, "Unsupported Tus-Resumable")
        try:
            info = _tenant_guard(_read_info(upload_id), str(user.id))
        except ValueError:
            raise HTTPException(404, "Upload not found") from None
        return Response(
            status_code=200,
            headers=_tus_headers(
                {
                    "Upload-Offset": str(info["offset"]),
                    "Upload-Length": str(info["length"]),
                    "Upload-Metadata": _encode_upload_metadata(
                        {
                            "filename": info["filename"],
                            "sessionId": info.get("session_id") or "",
                        }
                    ),
                }
            ),
        )

    @r.patch("/files/{upload_id}")
    async def tus_patch(
        upload_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> Response:
        """Append bytes at the declared offset. The client must send the
        exact offset we returned on the last HEAD/PATCH — any mismatch
        is a 409 and forces the client to re-HEAD before retrying."""
        if request.headers.get("Tus-Resumable") != TUS_VERSION:
            raise HTTPException(412, "Unsupported Tus-Resumable")
        ct = (request.headers.get("Content-Type") or "").lower()
        if not ct.startswith("application/offset+octet-stream"):
            raise HTTPException(415, "Content-Type must be application/offset+octet-stream")

        try:
            info = _tenant_guard(_read_info(upload_id), str(user.id))
        except ValueError:
            raise HTTPException(404, "Upload not found") from None

        try:
            offset_in = int(request.headers.get("Upload-Offset", ""))
        except ValueError:
            raise HTTPException(400, "Upload-Offset header must be an integer") from None
        if offset_in != info["offset"]:
            # 409 is the spec's "conflict — re-HEAD and try again" code.
            return Response(
                status_code=409,
                headers=_tus_headers({"Upload-Offset": str(info["offset"])}),
            )

        body = await request.body()
        if not body:
            # Empty PATCH is technically legal but useless — let it through
            # as a no-op so a client retry doesn't double-count.
            return Response(
                status_code=204,
                headers=_tus_headers({"Upload-Offset": str(info["offset"])}),
            )

        new_offset = info["offset"] + len(body)
        if new_offset > info["length"]:
            raise HTTPException(413, "Chunk would exceed declared Upload-Length")

        # Append. ``ab`` is atomic for a single write up to PIPE_BUF on
        # POSIX, and our chunks (≤5 MB) are far above that — but we're
        # the only writer for this upload_id, so concurrent appends from
        # a misbehaving client are still well-defined: the offset header
        # check above gates them.
        with (_upload_dir(upload_id) / "data.bin").open("ab") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())  # survive a serve_rag crash mid-upload
        info["offset"] = new_offset
        _write_info(upload_id, info)

        result_meta: dict[str, str] = {}
        if new_offset == info["length"]:
            try:
                finalize_result = finalize(
                    user,
                    info,
                    (_upload_dir(upload_id) / "data.bin").read_bytes(),
                )
                result_meta = {k: str(v) for k, v in (finalize_result or {}).items()}
            finally:
                _cleanup(upload_id)

        headers = _tus_headers({"Upload-Offset": str(new_offset)})
        if result_meta:
            headers["Upload-Metadata"] = _encode_upload_metadata(result_meta)
        return Response(status_code=204, headers=headers)

    @r.delete("/files/{upload_id}")
    async def tus_delete(
        upload_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> Response:
        """Cancel an in-flight upload. Idempotent — missing/foreign uploads
        return 404 (we never leak existence)."""
        if request.headers.get("Tus-Resumable") != TUS_VERSION:
            raise HTTPException(412, "Unsupported Tus-Resumable")
        try:
            info = _tenant_guard(_read_info(upload_id), str(user.id))
        except ValueError:
            raise HTTPException(404, "Upload not found") from None
        del info  # only validated for the tenant guard side-effect
        _cleanup(upload_id)
        return Response(status_code=204, headers=_tus_headers())

    return r


def _cleanup(upload_id: str) -> None:
    """Remove an upload's working directory. Best-effort; if it errors
    (file locked on Windows etc.) the orphan can be GC'd by a periodic
    cron — left as a deploy concern, see UPLOAD_RESUMABLE_DESIGN.md."""
    try:
        d = _upload_dir(upload_id)
    except ValueError:
        return
    if not d.exists():
        return
    for p in d.iterdir():
        with contextlib.suppress(OSError):
            p.unlink()
    with contextlib.suppress(OSError):
        d.rmdir()


def gc_stale_uploads(max_age_seconds: int = 24 * 60 * 60) -> int:
    """Delete upload directories whose ``info.json.created_at`` is older
    than ``max_age_seconds`` AND whose upload isn't complete. Returns
    the number purged. Safe to call from a cron / startup task."""
    now = time.time()
    n = 0
    for d in TUS_STAGING_DIR.iterdir():
        if not d.is_dir():
            continue
        info_path = d / "info.json"
        if not info_path.exists():
            # Bare data dir with no info — definitely garbage.
            try:
                for p in d.iterdir():
                    p.unlink()
                d.rmdir()
                n += 1
            except OSError:
                pass
            continue
        try:
            info = json.loads(info_path.read_text())
        except Exception:
            continue
        age = now - float(info.get("created_at", now))
        if age > max_age_seconds:
            _cleanup(d.name)
            n += 1
    return n
