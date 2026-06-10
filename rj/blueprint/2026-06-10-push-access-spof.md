# Push-access SPOF on rj — decision brief (Phase 4.5.5)

**Date:** 2026-06-10 · **Owners:** rj (engineering audit) + boss
(decision) · **Status:** DECISION BRIEF — engineering side ready;
awaits boss conversation on org name + ownership

## TL;DR

Both LAI repos are under `Ravijangid820` — rj's *personal* GitHub
account. Every other team member's SSH key resolves to the shared
`TAI-Agent` identity, which has no push rights on rj-personal repos.
Result: every commit by Sahid / harsh / vm / anyone has to route
through rj's machine. Four incidents on 2026-06-02, another on
2026-06-04. Two fix paths exist — **option (b) is unambiguously
correct and likely much cheaper than the PROGRESS_V2 row estimated**,
because `TAI-Agent` is almost certainly already a GitHub *organization*
(it owns at least one repo — `DS_Platform` — under the org/repo
URL pattern). If so, every team member already has org access; the
transfer is trivial and the per-collaborator-add option (a) becomes
strictly worse on every dimension.

## Current state (verified 2026-06-10)

| Check | Result |
|---|---|
| `git remote -v` on `LAI/` | `git@github.com:Ravijangid820/LAI.git` |
| `git remote -v` on `LAI-UI/` | `git@github.com:Ravijangid820/LAI-UI.git` |
| `ssh -T git@github.com` from this box | `Hi Ravijangid820!` |
| rj's box SSH key | ED25519 `rj@blockland.ae` (fp `s5QRSM…OMI/0A`), attached to `Ravijangid820` |
| Other users' SSH keys | their `~/.ssh/` exists but mode-700 (not auditable from rj's session); per PROGRESS_V2 row, all resolve to shared `TAI-Agent` identity with no push rights |
| LAI CI workflows | **none** in `.github/workflows/` |
| LAI-UI CI workflows | `.github/workflows/ci.yml` — `npm run lint` + `npm run build`, no hardcoded owner |
| LAI-UI Vercel config | `vercel.json` has only build/output/rewrites — **no GitHub integration hooks**; Vercel relinks via dashboard, not config |
| Hardcoded `Ravijangid820/` references in repo | **7 active files** to update + 6 historical doc references that can stay |

## The structural problem

`Ravijangid820` is a single human's personal GitHub account. Even
if we add 8 collaborators tomorrow (option a), the account is still
a SPOF:

1. **rj is hit by a bus / loses laptop / leaves the company** → repos
   are orphaned; nobody can manage settings, rotate webhooks, or
   transfer ownership. GitHub support takes weeks for non-org account
   recovery.
2. **Personal accounts can't have org-level features** — team-level
   secrets, SAML SSO, audit log, fine-grained permission roles.
3. **The "shared TAI-Agent" identity is unused for the LAI work** —
   every team member already has an SSH key linked to it, but it can't
   push because it's not a collaborator on rj-personal repos. We're
   paying the org-style identity cost without getting the benefit.

## ⭐ Critical finding: `TAI-Agent` is almost certainly already an org

The PROGRESS_V2 description says "every personal SSH key on the
shared workstation resolves to `TAI-Agent/DS_Platform` on GitHub."
The `OWNER/REPO` URL pattern means `TAI-Agent` owns `DS_Platform`.
GitHub allows organisations to own repos; personal accounts also
do. **But:** if multiple team members' keys all resolve to a single
`TAI-Agent` identity AND that identity owns repos AND members can
push to those repos → that's the signature of a GitHub *organisation*
with members, not a shared personal account (GitHub doesn't allow
multiple SSH keys on one personal account from independent humans;
it does on an org).

**If `TAI-Agent` is an org, option (b) collapses to a 2-step click
path:** rj transfers the two repos to the `TAI-Agent` org; everyone
who's already a member gets push rights instantly. No
per-collaborator setup, no per-person SSH keypair generation, no
8×2=16 individual invites.

**Action item for boss conversation:** confirm whether `TAI-Agent` is
a GitHub user or an organisation. If user → we need a new org (or
acquire one). If org → we already have what we need.

How to check (anyone with a GitHub account can): visit
`https://github.com/TAI-Agent` — an organisation page shows
"Organization", a "People" tab, a "Teams" tab, and member avatars
in a grid. A personal account shows pinned repos, contribution
graph, and a "Followers" count.

## Path comparison

### Option (a) — per-collaborator additions on rj's repos

| Step | Who | Time |
|---|---|---|
| For each of 8 team members: confirm they have a personal GitHub account | each member | varies |
| For each member: rj invites their personal GitHub user as collaborator to `Ravijangid820/LAI` | rj (web UI) | 8 × 1 min |
| For each member: rj invites them as collaborator to `Ravijangid820/LAI-UI` | rj | 8 × 1 min |
| For each member: accept the email invite | each member | 8 × ~2 min |
| For each member: generate a keypair on the shared box, add the pubkey to their personal GitHub | each member | 8 × ~5 min |
| For each member: test push | each member | 8 × ~2 min |
| **Total** | rj ~16 min + 8 × ~9 min member-side | ~1.5 h calendar |

**What's still broken after (a):** the SPOF on `Ravijangid820`. If
rj's account is compromised or unreachable, everything orphans.
Off-boarding requires per-repo collaborator removal. New team
members need the same 9-min onboarding × N repos. The "shared
TAI-Agent" identity stays unused.

### Option (b) — transfer to a shared org

| Step | Who | Time |
|---|---|---|
| **Decide:** is `TAI-Agent` an org? If yes, use it. If no, create a new org (free for public repos, $4/user/month for private). | boss + rj | 10 min decision; 5 min org-create if needed |
| Transfer `Ravijangid820/LAI` → `<org>/LAI` (Settings → Danger Zone → Transfer ownership) | rj | 2 min |
| Transfer `Ravijangid820/LAI-UI` → `<org>/LAI-UI` (same path) | rj | 2 min |
| Update LAI-UI Vercel project: Settings → Git → relink to new repo path | whoever has Vercel login | 5 min |
| Update 7 active files (`sed -i 's,Ravijangid820/LAI,<org>/LAI,g'`) — README × 2, INFRASTRUCTURE.md, DEVELOPMENT.md, MVP_DELIVERY.md, CONTRIBUTING.md, start.sh, serve_rag.service, TODO.md | rj | 5 min |
| Update both repos' `git remote set-url origin git@github.com:<org>/<repo>.git` on every workstation that has clones | rj + each member | 1 min × N clones |
| Add any non-`TAI-Agent` collaborators to the org if needed | rj (org admin) | as needed |
| **Total** | ~30 min focused | ~1 h calendar including verifications |

**What's fixed by (b):**
- No more SPOF on a personal account; org has multiple admins.
- Onboarding a new team member is a single org-invite, not per-repo.
- Off-boarding is single org-removal.
- Existing TAI-Agent identity gets used.
- Team-level secrets, SAML, audit log become available.
- GitHub URLs across docs become organisation-branded
  (`<org>/LAI` reads as a team project, not a personal one).
- GitHub auto-redirects all old `Ravijangid820/LAI*` URLs for free
  (transfer-redirect feature) — so the URL flips in docs are
  nice-to-have, not break-the-build essential.

### Side-by-side scorecard

| Dimension | (a) collaborators | (b) org transfer |
|---|---|---|
| One-time setup | ~1.5 h | ~1 h |
| Per-new-member onboarding | ~9 min × N | ~1 click |
| Off-boarding | per-repo collaborator removal | single org removal |
| Survives rj losing account | ❌ | ✅ (multiple org admins) |
| Survives any single member losing account | ✅ (others still in) | ✅ |
| Team-level secrets / SAML / audit | ❌ | ✅ |
| URL branding | rj-personal | team |
| Cost if private repos | free | $4/user/month |
| Reuses existing TAI-Agent identity | ❌ | ✅ if TAI-Agent is org |
| Rollback complexity if wrong | revoke 16 collaborators | transfer repos back (uses same UI path) |

**Recommendation: option (b). Strong recommendation.** The only
real cost is the boss decision on org name + admin set.

## The exact GitHub UI path for option (b)

### Step 0 — Verify TAI-Agent type (5 min)

Open `https://github.com/TAI-Agent` in a browser. If it shows
**Organization** at the top + "People" and "Teams" tabs → use it.
If not (it's a user account or a non-existent handle), do step 0a:

#### Step 0a (only if needed) — Create a new org

GitHub → top-right `+` → "New organization" → choose plan (Free for
public repos, Team for private at $4/user/month) → name it. **Naming
recommendation:** `blockland-ai`, `blockland-legal`, or `tai-agent`
(reclaim the existing identity if available). Avoid product-specific
names like `lai-org` — we may add other repos later.

### Step 1 — Transfer rj's repos (5 min, 2 transfers)

For each repo (`Ravijangid820/LAI`, then `Ravijangid820/LAI-UI`):

1. Open repo on GitHub web → Settings → Danger Zone (bottom) →
   "Transfer ownership."
2. Type the org name as the new owner.
3. Confirm by typing the repo name.
4. Hit Transfer. (GitHub holds the transfer until the org admin
   accepts — if rj is also the org admin, accept it immediately.)

After both: `git@github.com:Ravijangid820/LAI.git` auto-redirects
to `git@github.com:<org>/LAI.git` for ~6 months. Old clones keep
working; new clones should use the new URL.

### Step 2 — Update remotes on every workstation (1 min × N)

Anyone with an existing clone runs (from the repo dir):

```bash
git remote set-url origin git@github.com:<org>/LAI.git
git remote set-url origin git@github.com:<org>/LAI-UI.git    # in LAI-UI dir
```

### Step 3 — Update 7 hardcoded references in-repo (5 min)

```bash
cd /data/projects/lai
NEW_OWNER=<org>     # e.g., blockland-ai

# Active files (rj should verify each diff after the sed):
for f in \
  LAI/README.md \
  LAI/TODO.md \
  LAI/docs/INFRASTRUCTURE.md \
  LAI/docs/DEVELOPMENT.md \
  LAI/docs/MVP_DELIVERY.md \
  LAI/scripts/ops/start.sh \
  LAI/scripts/ops/systemd/serve_rag.service \
  LAI-UI/CONTRIBUTING.md \
  LAI-UI/README.md ; do
    sed -i "s,Ravijangid820/LAI,${NEW_OWNER}/LAI,g" "$f"
done
git diff --stat
```

Historical references in `rj/blueprint/2026-05-30-laiui-architecture-rollout.md`,
`rj/blueprint/2026-06-02-bm25-retune-empirical.md`,
`harsh/RJ_AI_BRIEFING.md`, `harsh/PROGRESS_V2.md` reference past
state and are fine to leave (GitHub auto-redirect covers them).

### Step 4 — Relink Vercel (5 min)

Vercel dashboard → LAI-UI project → Settings → Git → "Connect Git
Repository" or "Change repository" → pick `<org>/LAI-UI` → save.
Trigger a redeploy to verify.

### Step 5 — Test push from a non-rj account (5 min)

Have one team member who isn't rj try:

```bash
echo "$(date)" >> /tmp/push-test.txt
cd /data/projects/lai/LAI
git checkout -b push-test/<their-username>
git add -A 2>/dev/null   # nothing actually committed
git push origin push-test/<their-username>  # expect: empty push, 0 objects
```

If it succeeds → org access is working. Delete the branch:
`git push origin --delete push-test/<their-username>`.

## What can be done unilaterally tonight

Nothing irreversible — but I CAN do:

1. ✅ This blueprint (decision-ready).
2. ✅ Append the decision brief to `LAI/scripts/ops/README.md` so
   anyone hitting "I can't push" gets the link.
3. ⏸ NOT: transfer the repos. That's a one-way operation pending the
   boss decision on org name + admin set.

## What needs the boss conversation

- Is `TAI-Agent` an org? (Most likely yes.) If so, are we OK reusing
  it for LAI? (Yes unless TAI-Agent is for a different product line
  that shouldn't see LAI source.)
- If creating new: org name. Recommended `blockland-ai` (branded,
  generic enough for future products). Acceptable: `tai-agent` (if
  reclaiming), `blockland-legal`, etc.
- Who is the org owner / billing contact? Recommended: a corporate
  account (not rj-personal), and at least 2 org admins so admin
  itself isn't a SPOF.
- Are the repos staying public or going private? (Affects billing.)

## Honest gaps not closed by this work

1. **The transfer breaks pull request URLs** — open PRs migrate, but
   if anyone was @-mentioned with the full URL `github.com/Ravijangid820/...`,
   those mentions don't auto-update in their notifications.
   Mitigation: tell the team to refresh the page when they see the
   404 redirect.
2. **Webhooks reset on transfer.** If LAI-UI has Vercel installed as
   a GitHub App (which it does if Vercel auto-deploys on push), that
   integration needs to be re-authorized on the new owner. Handled in
   step 4 above but worth flagging.
3. **GitHub Action runs use the org's runner quota.** Free for public
   repos. If we're paying for private-repo minutes today, we'll keep
   paying the same; if we're getting the free public allotment, no
   change.
4. **DDiQ-related secrets stored as repo secrets** (none today per
   the workflow file inspection, but worth checking) would also
   transfer cleanly.

## Rollback

GitHub repo transfer is reversible. If wrong org, transfer back:
Settings → Danger Zone → Transfer ownership → enter old owner
(`Ravijangid820`) → confirm. Sub-minute operation. Vercel relink
follows the same path. The hardcoded-URL flips revert via `git revert`.

## Related

- [`harsh/PROGRESS_V2.md`](../harsh/PROGRESS_V2.md) row 4.5.5 — work item this closes
- `LAI/scripts/ops/start.sh` — hardcoded `git clone` instruction (update in step 3)
- `LAI/scripts/ops/systemd/serve_rag.service` — `Documentation=` URL (update in step 3)
- `LAI-UI/vercel.json` — no GitHub hooks today; relink via Vercel dashboard
- `LAI-UI/.github/workflows/ci.yml` — no hardcoded owner; survives transfer unchanged
- `https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository` — GitHub's official transfer doc
