# LAI team access — push permissions for the shared workstation

**Owner:** rj · **Status:** active · **Last updated:** 2026-06-10

Single operational doc for "I need (or someone else needs) to push to
LAI / LAI-UI from the shared workstation." If you're hitting
"Permission denied" you're in the right place. For the design
context behind the current setup, see
[`rj/blueprint/2026-06-10-push-access-spof.md`](../../rj/blueprint/2026-06-10-push-access-spof.md).

## Why this is needed

Both repos live at `Ravijangid820/LAI` and `Ravijangid820/LAI-UI` —
rj's *personal* GitHub account. For anyone else to push, two things
must be true:

1. Their **personal** GitHub user is a collaborator on both repos
   (rj's web-UI invite).
2. Their SSH key on the shared workstation is attached to that
   personal GH account (which means a key separate from any
   existing `TAI-Agent`-attached key, because GitHub forbids the
   same key on two accounts).

The bootstrap script automates step 2 cleanly.

---

## When you join (team-member-side, ~5 min)

### 1. Tell rj your personal GitHub username

The one tied to *your* email — NOT the shared `TAI-Agent` identity.
If you don't have a personal GH account yet, create one at
[github.com/signup](https://github.com/signup) (2 min; stays your
account).

### 2. Wait for two invite emails. Accept both.

Subject lines look like:
> @Ravijangid820 has invited you to collaborate on Ravijangid820/LAI

Invites expire in 7 days. If you miss the window, ask rj to re-send.

### 3. ssh into the shared workstation **as yourself**, then run:

```bash
bash /data/projects/lai/LAI/scripts/ops/team_access_bootstrap.sh
```

The script is idempotent (safe to re-run) and bails safely if you
already have a `Host github.com` block in `~/.ssh/config`. It will:

- Generate a separate ED25519 keypair at `~/.ssh/id_ed25519_lai`.
  Your existing TAI-Agent key is untouched.
- Update `~/.ssh/config` so SSH prefers the new key when talking
  to github.com, with your existing keys as fallback.
- Print the new public key + 3 numbered steps to finish.

### 4. Follow the 3 printed steps

1. Paste the printed pubkey into
   [github.com/settings/keys](https://github.com/settings/keys)
   logged into **your personal** GH account.
2. Verify `ssh -T git@github.com` greets you by your personal
   username (not `Hi Ravijangid820`).
3. Test push from the LAI repo (block printed by the script).

Done. Welcome aboard.

---

## When you're adding someone (rj-side, ~5 min per person)

### 1. Ask for their personal GH username

Slack/email template:

> Hi <name> — setting up push access for LAI/LAI-UI. Two things:
>
> 1. Send me your personal GitHub username (the one on your own
>    email, not the shared TAI-Agent identity). If you don't have
>    one, create at github.com/signup — takes 2 minutes.
> 2. Once I send you the two invite emails, ssh into the shared
>    workstation as yourself and run:
>    ```
>    bash /data/projects/lai/LAI/scripts/ops/team_access_bootstrap.sh
>    ```
>    Follow the printed steps. ~5 min start to finish. Then tell me
>    you're done so we can verify with a test push.

### 2. Invite their personal GH user on both repos

For each user:

- `https://github.com/Ravijangid820/LAI/settings/access` → **Add
  people** → enter their personal GH username → role **Write**
  (NOT Admin — only rj should hold admin for now) → confirm.
- `https://github.com/Ravijangid820/LAI-UI/settings/access` → same.

GitHub emails them both invites. Expire in 7 days.

### 3. They run steps 3–4 of the team-member flow above.

---

## Verification (run at least once after the first member finishes)

From their ssh session as themselves:

```bash
ssh -T git@github.com
# expected: "Hi <their-personal-username>! You've successfully authenticated"

cd /data/projects/lai/LAI
git checkout -b push-test/$USER
git commit --allow-empty -m "push test from $USER"
git push origin push-test/$USER       # should succeed
git push origin --delete push-test/$USER
git checkout develop && git branch -D push-test/$USER
```

If push works for the first person, every other member who runs the
bootstrap script will get the same result — the pattern is proven.

---

## When someone leaves (offboarding)

A `team_access_remove.sh` companion is planned but not yet shipped.
For now, rj manually removes the user:

- `https://github.com/Ravijangid820/LAI/settings/access` →
  find their row → "Remove."
- `https://github.com/Ravijangid820/LAI-UI/settings/access` → same.

Their bootstrap key (`~/.ssh/id_ed25519_lai`) on the shared box can
stay — it's harmless once their collaborator status is revoked
(GitHub will refuse pushes from a key whose attached account has no
access). If you want to be tidy, ssh in as them (or have an admin
do it) and `rm ~/.ssh/id_ed25519_lai*`.

---

## Troubleshooting

**Symptom:** `ssh -T git@github.com` says `Hi Ravijangid820` instead
of your personal username.

> **Cause:** SSH is offering rj's key first.
> **Fix:** re-run the bootstrap script. Confirm
> `~/.ssh/config` has `IdentityFile ~/.ssh/id_ed25519_lai` at the
> **top** of the `Host github.com` block (above any
> `id_ed25519` / `id_rsa` fallback lines).

**Symptom:** push fails with `Permission to Ravijangid820/LAI.git
denied to <your-personal-username>`.

> **Cause:** invite not accepted yet, OR collaborator role is
> Read/Triage instead of Write.
> **Fix:** check your GitHub email for the unaccepted invite; ask
> rj to confirm role = Write (not Read or Triage).

**Symptom:** "Key is already in use" when adding pubkey on GitHub.

> **Cause:** that pubkey is already attached to the TAI-Agent
> account (or another GH account). GitHub forbids one key on two
> accounts.
> **Fix:** the bootstrap script generates a *separate* key
> (`id_ed25519_lai`); paste THAT pubkey, not your default one.
> If you accidentally pasted the wrong one, just generate a fresh
> bootstrap key by `rm ~/.ssh/id_ed25519_lai*` and re-run the
> script.

**Symptom:** TAI-Agent push (to some other repo) stops working
after the bootstrap script ran.

> **Cause:** the script set `IdentitiesOnly yes` and didn't pick
> up your TAI-Agent key under a standard name. Add it as a
> fallback:
> ```
> Host github.com
>     ...
>     IdentityFile ~/.ssh/id_ed25519_lai
>     IdentityFile ~/.ssh/<your-tai-agent-key>     # add this
> ```

---

## Why is this not just a GitHub org?

The structurally cleaner answer is option (b) — transfer both repos
to a shared org so team membership becomes the single source of
truth. We chose option (a) per-collaborator additions on
2026-06-10 because it closes the immediate "I can't push" pain
without gating on a boss conversation about org name + admin set.
When (b) eventually happens:

- GitHub auto-migrates collaborators on transfer — nobody loses
  access during the cutover.
- The bootstrap script's `~/.ssh/config` block keeps working: the
  SSH endpoint `git@github.com` is unchanged.
- Each member updates one URL on each clone:
  `git remote set-url origin git@github.com:<new-org>/LAI.git`.

Full design trade-off + (a)-vs-(b) scorecard in
[`rj/blueprint/2026-06-10-push-access-spof.md`](../../rj/blueprint/2026-06-10-push-access-spof.md).

---

## Related

- `LAI/scripts/ops/team_access_bootstrap.sh` — the script this doc
  drives.
- `rj/blueprint/2026-06-10-push-access-spof.md` — design context
  (why option (a), what (b) looks like).
- [`harsh/PROGRESS_V2.md`](../../harsh/PROGRESS_V2.md) row 4.5.5 —
  tracking entry.
