# Frontend bug — DDiQ report progress bar stalls at 5% on a completed report

**Reported:** 2026-05-22 · **Severity:** high (demo-blocking perception —
a *finished* report looks *stuck*) · **Lane:** LAI-UI (frontend)

## Symptom

Sahid generated a DDiQ report from the UI ("Sönke-Nissen-Koog 58", 5
documents). The progress bar sat at **5% for ~30 min** and looked hung.

## What actually happened (backend is correct)

The report **completed successfully** server-side:

- `report_id = 867d2a52-6b0f-4fc0-a120-16bcc2ed5ae3`
- ran **14.3 min** (05:37:43 → 05:52:00), `status=done`, 4 sections, 28 findings, 8 WEA
- the status endpoint returns the correct terminal state:

```
GET /ddiq/report/867d2a52…/status →
{"status":"done","step":"done","percent":1.0,
 "started_at":"…05:37:43Z","finished_at":"…05:52:00Z","error":null}
```

So the bug is **entirely in the frontend progress poll** — it stopped
updating after an early poll and froze at 5% even though the backend
reached 100%/done.

## Likely root cause

The report-status poll most plausibly **died on a 401 and never
recovered**. The DDiQ report ran ~14 min; the access-token TTL is short,
so the token almost certainly expired mid-run. If the status poll:

- doesn't refresh the token on 401 (the way `apiFetch` does for other
  calls), and/or
- stops polling after the first error instead of continuing until a
  terminal `done`/`failed`,

…then it freezes at whatever percent it last saw (the ~5% "gathering /
metadata" early step) and never observes completion.

(Secondary possibility: the poll has a fixed max-attempts / fixed timeout
shorter than a real multi-document report, which now legitimately takes
10–25 min for 5 PDFs.)

## Fix (frontend)

In the DDiQ report-generation progress component (the one polling
`/ddiq/report/{id}/status`):

1. **Route the poll through `apiFetch`** (or replicate its single-flight
   401→refresh→retry) so an expired access token is transparently
   refreshed and polling continues — same pattern the chat already uses.
2. **Poll until a terminal state** (`status === "done" || "failed"`), not
   a fixed number of attempts or a short overall timeout. A 5-document
   report can legitimately run 10–25 min.
3. On terminal `done`, advance the bar to 100% and surface the report;
   on `failed`, show `error`. Don't leave the bar mid-progress.
4. Optional polish: show the real `step` label (gathering → sections →
   wea_extraction → … → done) so the user sees forward motion even
   between percent jumps — the endpoint already returns `step`,
   `started_at`, `finished_at`.

## How to verify

Generate a report from the UI on a multi-PDF matter, let the token age
past its TTL (or shorten TTL in a dev build), and confirm the bar
advances to 100% and the report opens — no manual page refresh needed.

## Workaround until fixed

The report is fine — **refresh the page / reopen it from the reports
list** and it shows complete. Nothing is lost; the progress bar is
cosmetic.
