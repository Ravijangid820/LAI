# Draft — follow-up to `as@blockland.ae` re: 6 uploads, 0 queries

**Status:** DRAFT — for rj / boss to send · **Date:** 2026-06-02

Closes the ⬜ item from PROGRESS_V2 §"Live production sample — ks + as
chat audit (2026-06-01)" → "Follow-up for as" — a 6-document upload
session on 2026-05-26 with zero subsequent queries. Possible causes
in order of plausibility (per the audit):

1. Testing the upload flow only.
2. Hit the *"still processing"* gate that vm-2's dedup fixed (now
   shipped to `origin/develop` as `cf9adfe` post-recovery, awaiting
   the next LAI-UI Vercel rollout).
3. Dropped files for someone else (sa, ks) to query.
4. FE / auth issue specific to that account.

## Suggested message (short, low-pressure)

> Hi *as* — we noticed you uploaded six documents on 26 May but
> didn't end up asking the system anything against them. Two reasons
> we want to ask:
>
> 1. If you tried and got stuck — were the documents stuck on *"still
>    processing"* even after they finished? We landed a fix this week
>    (`vm-2 dedup`) that should help; happy to walk you through
>    re-trying.
> 2. If you uploaded them for someone else to query, that's also fine
>    — no action needed.
>
> Either way, a short reply (even "yes, all good") closes the loop on
> our end. Thanks!

## Why this matters

* Closes an audit ⬜ item with a 2-minute message.
* Tells us if the dedup gate hurt a real user (not just a repro on a
  test box).
* Stops as's upload session from looking like a silent failure in the
  production health view.

## Don't include

* The technical detail of which commit shipped the fix.
* Any reference to the audit (treat the data as internal).
* Pressure to use the product — this is a check-in, not a sales nudge.
