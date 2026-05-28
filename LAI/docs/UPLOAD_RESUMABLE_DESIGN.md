# Resumable Uploads — Phase 2 design (tus protocol)

> Status: **code-complete, awaiting `restart_serve_rag.sh`** by rj.
> Backend tus router + completion hook + client adapter + npm dep are
> all landed. The protocol is gated behind ``VITE_RESUMABLE_UPLOAD`` —
> off by default, so the legacy XHR path keeps running until the env
> var is flipped after the smoke test.
>
> Companion shipped earlier: **Phase 1** (IndexedDB blob persistence,
> reload-resume, beforeunload guard, stronger retry, visibility re-poll).
> Phase 1 alone covers reload/crash and short network blips. Phase 2 adds
> **byte-level resume**: a 90 MB upload interrupted at byte 47 MB resumes
> from 47 MB, not from 0.

---

## 1. Why tus

The current `/upload` and `/ddiq/documents/upload` endpoints take a single
`multipart/form-data` POST. If the TCP connection dies at byte N, the
server-side buffer is discarded and the client has to re-POST the whole
file. For Kristian's 50–100 MB VDR PDFs over flaky train wifi that's a
showstopper.

[**tus 1.0**](https://tus.io/protocols/resumable-upload.html) is the
industry-standard resumable-upload HTTP protocol. Used by Vimeo,
Cloudflare, GitLab, Transloadit. Mature reference server (`tusd`) and
client (`tus-js-client`). Chosen over a custom chunked protocol because:

- Battle-tested edge cases (chunk reordering, partial chunks, server
  crash mid-upload, expired uploads, content-length mismatch).
- Existing tooling — `tus-js-client` slots in with byte-progress callbacks
  that mirror our current `onProgress(percent)` shape.
- One spec to point at when teaching the next engineer.

Rejected: rolling our own chunked POST. Saved maybe 200 lines of code,
costs us a quarter of debugging every weird corner case. Not worth it.

---

## 2. Wire-level summary (so we agree on what we're building)

The protocol is dead simple — three HTTP verbs.

**Step 1 — client creates the upload.**

```http
POST /tus/files/ HTTP/1.1
Tus-Resumable: 1.0.0
Upload-Length: 104857600                    ← total bytes
Upload-Metadata: filename d2luZHBhcms=,sessionId YWJj...    ← base64 kv pairs
Authorization: Bearer eyJ…

→ 201 Created
Location: /tus/files/abc123
Tus-Resumable: 1.0.0
```

**Step 2 — client PATCHes chunks until done.**

```http
PATCH /tus/files/abc123 HTTP/1.1
Tus-Resumable: 1.0.0
Upload-Offset: 0
Content-Type: application/offset+octet-stream
Content-Length: 5242880                     ← 5 MB chunk
Authorization: Bearer eyJ…
[binary]

→ 204 No Content
Tus-Resumable: 1.0.0
Upload-Offset: 5242880
```

Repeat with `Upload-Offset: 5242880`, `10485760`, … On network drop, the
client does:

```http
HEAD /tus/files/abc123 HTTP/1.1
→ 200 OK
Upload-Offset: 47185920                     ← server tells us where we got to
```

…then resumes PATCHing from byte 47185920. No re-send of confirmed bytes.

**Step 3 — server-side completion hook.** When `Upload-Offset == Upload-Length`,
the tus handler invokes our existing ingest pipeline (Docling → chunk →
embed) on the assembled file. The client's `onSuccess` callback fires and
the chip flips to "processing".

---

## 3. Backend — what's already landed

**No third-party dep.** The tus protocol is ~200 lines of well-specified
HTTP. `tuspyserver` and `tusd` both add an audit + ops surface for
something we can implement directly. Self-contained route handlers in
[upload_tus.py](../src/lai/api/upload_tus.py) avoid all of that and
keep auth + tenant isolation under our control.

### 3.1 What was added

| File | Change |
|---|---|
| **`LAI/src/lai/api/upload_tus.py`** (new) | Full tus 1.0 core impl: `OPTIONS`, `POST /files/`, `HEAD /files/{id}`, `PATCH /files/{id}`, `DELETE /files/{id}`. State on disk under `data/processed/tus_staging/`. Tenant guard on every verb. `creation-with-upload` extension supported so small files finish in one round-trip. |
| **`LAI/src/lai/api/serve_rag.py`** | (a) `_finalize_tus_upload()` completion hook — mirrors `POST /upload`'s synchronous body (validate session, add matter_documents row, persist blob, enqueue ingest). (b) Mounts the tus router at `/tus/` inside the lifespan startup. (c) CORS `expose_headers` extended with `Location, Upload-*, Tus-*`. (d) Startup GC of abandoned upload dirs older than 24h. |

### 3.2 What rj needs to verify post-restart

After the restart sequence in §3.3, smoke-test the four invariants:

- [ ] **Auth on every verb.** Each tus endpoint takes
      `user: CurrentUser = Depends(get_current_user)`. Tested by
      `curl /tus/files/ -X HEAD` without a Bearer token → expect 401.
- [ ] **Tenant isolation.** User A creates an upload; user B HEADs
      that upload id with their own valid token → expect 404 (never 200,
      never 403 — 404 doesn't leak existence).
- [ ] **Restart survival.** Mid-upload, run `./scripts/ops/restart_serve_rag.sh`.
      The staging dir survives. After restart, client HEAD returns the
      pre-restart `Upload-Offset` and PATCH resumes cleanly.
- [ ] **Disk pressure.** `data/processed/tus_staging` is the new hot
      directory. The startup GC purges abandoned >24h dirs; a longer-term
      cron is optional but not yet wired.
- [ ] **Final-PATCH response shape.** The completion hook returns
      `{"doc_index": "...", "session_id": "..."}`; the client decodes it
      out of the `Upload-Metadata` response header.
- [ ] **413 on oversize.** A `POST /tus/files/` with
      `Upload-Length: 200_000_000` → expect 413 (LAI_TUS_MAX_SIZE = 100 MB).

### 3.3 Step-by-step for rj (the commands)

```bash
# 1. Pull whatever sa just pushed. The branch is v2-restructure.
cd /data/projects/lai/LAI
git pull

# 2. Restart serve_rag. THIS IS THE ONLY THING ONLY YOU CAN DO.
./scripts/ops/restart_serve_rag.sh

# 3. (rj or sa) Smoke test from the same box. Replace $TOKEN with a
#    valid Bearer token (curl one out of /auth/login if needed):
curl -i -X OPTIONS http://127.0.0.1:18000/tus/files/ \
     -H "Tus-Resumable: 1.0.0"
# → expect: 204 with Tus-Version, Tus-Extension, Tus-Max-Size headers

curl -i -X POST http://127.0.0.1:18000/tus/files/ \
     -H "Authorization: Bearer $TOKEN" \
     -H "Tus-Resumable: 1.0.0" \
     -H "Upload-Length: 5" \
     -H "Upload-Metadata: filename dGVzdC50eHQ="
# → expect: 201 with Location: …/tus/files/<uuid>
```

That's it. If the smoke test passes, sa flips `VITE_RESUMABLE_UPLOAD=1`
and rebuilds the SPA — no further backend work needed.

### 3.4 If something goes wrong

The protocol is gated behind `VITE_RESUMABLE_UPLOAD`. If anything
misbehaves, the rollback is one env var:

```bash
# In .env (or wherever Vite reads from):
VITE_RESUMABLE_UPLOAD=0
# Rebuild the SPA. The tus router stays mounted but no client touches
# it — every upload flows through the legacy /upload endpoint.
```

### 3.5 Roll-out path

1. ✅ Backend code landed (this commit).
2. rj: restart serve_rag.
3. rj/sa: curl smoke test (§3.3).
4. sa: `VITE_RESUMABLE_UPLOAD=1` on dev, rebuild, drag a 50 MB file in,
   yank wifi mid-upload, confirm resume.
5. Flip on staging.
6. Flip on prod once we've watched it for a day.

---

## 4. Client — what's already landed

| File | Change |
|---|---|
| **`LAI-UI/src/react-app/lib/uploadResumable.ts`** (new) | `uploadDocumentResumable()` — drop-in replacement for `uploadDocumentWithProgress`, same signature, same `AbortSignal` + `UploadAbortError` semantics. Dynamically imports `tus-js-client` so it's tree-shaken when the flag is off. |
| **`LAI-UI/src/react-app/lib/ragApi.ts`** | `uploadDocumentWithProgress()` is now a dispatcher: tus when `VITE_RESUMABLE_UPLOAD=1`, legacy XHR (still complete with retry/backoff) otherwise. |
| **`LAI-UI/src/react-app/lib/ddiqApi.ts`** | Same dispatcher pattern on the DDiQ library upload. |
| **`LAI-UI/package.json`** | `tus-js-client@^4.3.1` added. `npm install` already run by sa; rj only needs to redeploy the SPA after flipping the flag. |

Resume after reload (Phase 1) and resume after network drop (Phase 2)
compose: IDB has the File blob; tus has the server-known offset. On
boot the composer re-uploads via the dispatcher; tus's `Upload.start()`
HEADs the resource first and resumes from the offset — no re-send of
confirmed bytes even across a full browser restart.

---

## 5. Testing checklist

To be run end-to-end after rj lands the backend.

| # | Scenario | Pass criteria |
|---|---|---|
| 1 | 5 MB file, happy path | uploads ≤ 2 s, chip green |
| 2 | 100 MB file, happy path | uploads in <1 min on LAN |
| 3 | 100 MB file, kill wifi at 30% | client retries; on reconnect resumes from ~30%, finishes |
| 4 | 100 MB file, reload tab at 50% | banner appears; resumes from ~50% on next page open |
| 5 | 100 MB file, kill serve_rag at 50%, restart | client HEAD returns correct offset, resume succeeds |
| 6 | 5 files × 50 MB in parallel | all complete, no cross-contamination of session ids |
| 7 | Two users upload simultaneously | tenant isolation: user B's HEAD on user A's upload id → 404 |
| 8 | Access token expires mid-upload | client refreshes via cookie, resumes |
| 9 | File over 100 MB | server returns 413, client surfaces error (no infinite retry) |
| 10 | Same file dropped twice | dedup logic still catches it (composer layer is unchanged) |

---

## 6. Risks + rollback

- **`tuspyserver` library maturity** — small project. If it breaks, we
  switch to `tusd` sidecar. Wire protocol is identical, client code is
  unchanged. Migration is rj-only.
- **Disk leak** — abandoned uploads pile up. Mitigated by tus's
  `expiration_seconds`, but verify the cron actually runs.
- **Auth context drift** — between create (POST) and final PATCH, a
  long-running upload may span 30+ minutes. The user_id stamped at
  create-time should be the source of truth; do NOT re-derive from the
  current token on each PATCH (that would let user B steal user A's
  upload by passing their own valid token to a guessed upload id).

**Rollback:** flip `VITE_RESUMABLE_UPLOAD=0` and redeploy the SPA. Legacy
endpoint and old code path still run alongside, so the rollback is one
env-var change.

---

## 7. Cross-references

- Client code that will hook into this: [LAI-UI/src/react-app/lib/ragApi.ts](../../LAI-UI/src/react-app/lib/ragApi.ts), [LAI-UI/src/react-app/lib/ddiqApi.ts](../../LAI-UI/src/react-app/lib/ddiqApi.ts), [LAI-UI/src/react-app/lib/uploadStore.ts](../../LAI-UI/src/react-app/lib/uploadStore.ts).
- Phase 1 (already shipped): IDB persistence + reload-resume + beforeunload + stronger retry + visibility re-poll. Files: [uploadStore.ts](../../LAI-UI/src/react-app/lib/uploadStore.ts), [UploadResumeIndicator.tsx](../../LAI-UI/src/react-app/components/UploadResumeIndicator.tsx), [useComposerAttachments.ts](../../LAI-UI/src/react-app/hooks/useComposerAttachments.ts), [UploadQueueProvider.tsx](../../LAI-UI/src/react-app/hooks/UploadQueueProvider.tsx).
- Stress-test plan that this work-stream supports: [STRESS_VOLUME_PLAN.md](./STRESS_VOLUME_PLAN.md).
