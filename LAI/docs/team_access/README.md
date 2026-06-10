# Push access for the shared LAI workstation

Single doc covering setup for new team members, admin procedures for
the person inviting them, troubleshooting, and offboarding.

If you've just been added as a collaborator on LAI / LAI-UI, start at
[Step 1](#step-1--ssh-in-and-run-the-bootstrap-script) below.
~5 minutes start to finish.

Design context (why this setup, not a GitHub org) is in
[`rj/blueprint/2026-06-10-push-access-spof.md`](../../../rj/blueprint/2026-06-10-push-access-spof.md).

---

## For new team members — get set up (5 steps, ~5 min)

### What you're doing and why

You've been invited as a collaborator on the LAI repos
(`Ravijangid820/LAI` and `Ravijangid820/LAI-UI`). To actually push
commits, GitHub needs to know who you are when you connect from the
shared workstation. The setup: generate an SSH key on the
workstation, then paste the **public** part of that key into your
*personal* GitHub account so GitHub can identify you. From then on,
every `git push` is recognised as you and your collaborator access
kicks in.

The bootstrap script does the key-generation half. The pubkey
paste + the GitHub invite-accept are the two parts you do by hand.

### Before you start

- [ ] You should have received **two** invite emails from
      `@Ravijangid820` (one for `LAI`, one for `LAI-UI`). Accept
      both — they expire 7 days from send. Check spam if missing;
      ask rj to resend if past the window.
- [ ] You can ssh into the shared workstation as your own user
      (not `rj`, not a shared login).
- [ ] You have a **personal** GitHub account (one tied to your own
      email — *not* the shared `TAI-Agent` identity). If you don't,
      create one in 2 min at
      [github.com/signup](https://github.com/signup) and tell rj
      the username so he can re-send the invites.

### Step 1 — ssh in and run the bootstrap script

ssh into the shared workstation **as yourself**, then:

```bash
bash /data/projects/lai/LAI/scripts/ops/team_access_bootstrap.sh
```

The script generates a brand-new SSH keypair at
`~/.ssh/id_ed25519_lai` (your existing keys are untouched), updates
`~/.ssh/config` so SSH knows to use the new key when talking to
github.com, and prints the public part on screen. Safe to re-run
if anything goes wrong.

### Step 2 — copy the printed pubkey

The script prints a block like:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...long string...0Fk yourname@blockland.ae-LAI-20260610
```

It's a single line (wraps visually in the terminal). Easiest way to
copy: print it explicitly and triple-click to select:

```bash
cat ~/.ssh/id_ed25519_lai.pub
```

### Step 3 — paste it into YOUR personal GitHub

1. Open [github.com/settings/keys](https://github.com/settings/keys)
   in a browser. **Make sure you're logged into YOUR personal
   GitHub account** (the one you gave rj) — *not* the shared
   `TAI-Agent` identity.
2. Click **New SSH key**.
3. Fill in:
   - **Title:** `LAI shared workstation`
   - **Key type:** `Authentication Key` (default)
   - **Key:** paste the line from Step 2
4. Click **Add SSH key**. GitHub may prompt for your password.

If GitHub says "Key is already in use" — see
[Troubleshooting](#troubleshooting).

### Step 4 — verify SSH now recognises you

Back in the terminal:

```bash
ssh -T git@github.com
```

Expected:

```
Hi <your-personal-github-username>! You've successfully authenticated,
but GitHub does not provide shell access.
```

The greeting must show **your** username. If it says
`Hi Ravijangid820`, see Troubleshooting.

### Step 5 — test push

The final sanity check:

```bash
cd /data/projects/lai/LAI
git checkout -b push-test/$USER
git commit --allow-empty -m "push test from $USER"
git push origin push-test/$USER
```

Should succeed. Clean up:

```bash
git push origin --delete push-test/$USER
git checkout develop
git branch -D push-test/$USER
```

Done. Ping rj that the test push worked. From now on your normal
`git push` from the shared workstation will work as you.

---

## For rj — adding a new collaborator

### Slack template

> Hi <name> — sending push access for LAI/LAI-UI. Two things:
>
> 1. Send me your personal GitHub username (the one on your own
>    email, not the shared TAI-Agent identity). If you don't have
>    one, create at github.com/signup — takes 2 minutes.
> 2. Once I send you the two invite emails, ssh into the shared
>    workstation as yourself and follow
>    `/data/projects/lai/LAI/docs/team_access/README.md`. ~5 min.
>    Ping me when your test push (step 5) works.

### Invite procedure

For each personal GH username:

1. Open
   <https://github.com/Ravijangid820/LAI/settings/access> →
   **Add people** → enter their personal GH username → role
   **Write** (NOT Admin — only rj should hold admin for now) →
   confirm.
2. Same for
   <https://github.com/Ravijangid820/LAI-UI/settings/access>.

GitHub emails both invites. Expire in 7 days.

### Target list (curated from the `lai` group, minus rj)

| Box user | Need push? |
|---|---|
| sa, hc, vm, ss | **Yes** — recent committers |
| aj, dg, as | Confirm with rj |
| ks_admin, dn_admin | Likely service accounts — skip unless rj says otherwise |

---

## When someone leaves (offboarding)

A `team_access_remove.sh` companion script is planned, not yet
shipped. For now, rj removes the user manually from each repo's
`settings/access` page:

- <https://github.com/Ravijangid820/LAI/settings/access> → find
  their row → **Remove**.
- <https://github.com/Ravijangid820/LAI-UI/settings/access> → same.

Their bootstrap key (`~/.ssh/id_ed25519_lai`) on the shared box can
stay — it's harmless once the collaborator status is revoked
(GitHub refuses pushes from a key whose attached account has no
access). If you want to be tidy, have them (or an admin) run:

```bash
rm ~/.ssh/id_ed25519_lai*
```

---

## Troubleshooting

### `ssh -T git@github.com` says "Hi Ravijangid820"

SSH is offering rj's key first. Re-run the bootstrap script — it
re-checks your config. If that doesn't fix it, open
`~/.ssh/config` and confirm this line appears **first** inside any
`Host github.com` block (above any `id_ed25519` / `id_rsa` fallback
lines):

```
    IdentityFile ~/.ssh/id_ed25519_lai
```

Save and try `ssh -T git@github.com` again.

### Push fails with "Permission to Ravijangid820/LAI.git denied to ..."

Three things to check, in order:

1. Have you accepted **both** invite emails (LAI and LAI-UI)?
   Unaccepted invites grant no access.
2. Ask rj to confirm your role is **Write** (not "Read" or
   "Triage" — those can't push).
3. `ssh -T git@github.com` — does it greet you by your personal
   username? If not, see the issue above first.

### "Key is already in use" when pasting on GitHub

You probably pasted the wrong pubkey — likely your default one,
which is already attached to the `TAI-Agent` account (or another
GitHub account). GitHub forbids the same key on two accounts.

The bootstrap script generated a **new, separate** key
specifically for this. Print only that one and paste it:

```bash
cat ~/.ssh/id_ed25519_lai.pub
```

Note the `_lai` suffix — that's the new one. `id_ed25519.pub`
*without* the suffix is your old default key; do not paste that.

### TAI-Agent push stops working after the bootstrap

The script set `IdentitiesOnly yes` and didn't pick up your
TAI-Agent key under a standard name. Add it as a fallback in
`~/.ssh/config`:

```
Host github.com
    ...
    IdentityFile ~/.ssh/id_ed25519_lai
    IdentityFile ~/.ssh/<your-tai-agent-key>     # add this
```

### Other / not covered above

DM rj with the exact command you ran and the full output.

---

## Why option (a) per-collaborator, not a GitHub org transfer?

The structurally cleaner answer is option (b) — transfer both repos
to a shared GitHub org so team membership becomes the single source
of truth. We chose option (a) on 2026-06-10 because it closes the
immediate "I can't push" pain in 2-3 days without gating on a boss
conversation about org name + admin set.

When (b) eventually happens:

- GitHub auto-migrates collaborators on transfer — nobody loses
  access during the cutover.
- The bootstrap script's `~/.ssh/config` block keeps working: the
  SSH endpoint `git@github.com` is unchanged.
- Each member updates one URL on each clone:
  ```bash
  git remote set-url origin git@github.com:<new-org>/LAI.git
  ```

Full design + (a)-vs-(b) scorecard in
[`rj/blueprint/2026-06-10-push-access-spof.md`](../../../rj/blueprint/2026-06-10-push-access-spof.md).

---

## Related

- [`LAI/scripts/ops/team_access_bootstrap.sh`](../../scripts/ops/team_access_bootstrap.sh) — the script this doc drives
- [`rj/blueprint/2026-06-10-push-access-spof.md`](../../../rj/blueprint/2026-06-10-push-access-spof.md) — design log
- [`harsh/PROGRESS_V2.md`](../../../harsh/PROGRESS_V2.md) row 4.5.5 — tracking entry
