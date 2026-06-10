# LAI / LAI-UI push access — setup guide

**For new team members joining the LAI repos.** Total time: ~5
minutes once you're at a terminal on the shared workstation.

---

## What you're about to do

You've been invited as a collaborator on the LAI and LAI-UI
repositories. To actually push commits, GitHub needs to know who
you are when you connect from the shared workstation. The way that
works: you generate an SSH key on the workstation, then paste the
**public** part of that key into your *personal* GitHub account.
After that, every `git push` you make from the workstation is
recognised as you, and your collaborator access kicks in.

The script does the key-generation half automatically. The pubkey
paste + the GitHub invite-accept are the two parts you have to do
by hand.

---

## Before you start

- [ ] You should have received **two** GitHub invitation emails
      from `@Ravijangid820`:
      - "Ravijangid820 has invited you to collaborate on
        Ravijangid820/LAI"
      - same for `Ravijangid820/LAI-UI`

      **Accept both.** The invites expire in 7 days. If you can't
      find them, check spam, then ask rj to resend.

- [ ] You can ssh into the shared workstation as your own user
      (not as `rj`, not via a shared account — your own login).

- [ ] You have a **personal** GitHub account (one tied to your own
      email — *not* the shared `TAI-Agent` identity). If you
      don't, create one in 2 min at
      [github.com/signup](https://github.com/signup) and tell rj
      the username so he can re-send the invites.

---

## Step 1 — ssh in and run the bootstrap script

ssh into the shared workstation **as yourself**, then:

```bash
bash /data/projects/lai/LAI/scripts/ops/team_access_bootstrap.sh
```

The script:

- Generates a brand-new SSH keypair at `~/.ssh/id_ed25519_lai`
  (your existing keys are untouched).
- Updates `~/.ssh/config` so SSH knows to use this new key when
  talking to github.com.
- Prints the **public** part of the new key on screen.

It's safe to re-run if anything goes wrong.

---

## Step 2 — copy the printed public key

After the script runs, you'll see a block that looks like:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...long string...0Fk yourname@blockland.ae-LAI-20260610
```

**Copy that entire line.** It looks wrapped in the terminal but
it's a single line — make sure you select from `ssh-ed25519`
through the trailing comment after the last space.

Quickest way: print it explicitly and triple-click to select:

```bash
cat ~/.ssh/id_ed25519_lai.pub
```

---

## Step 3 — paste it into YOUR personal GitHub account

1. Open [`https://github.com/settings/keys`](https://github.com/settings/keys)
   in a browser. **Make sure you're logged into YOUR personal
   GitHub account** (the one you gave rj) — *not* the shared
   `TAI-Agent` identity.

2. Click **"New SSH key"** (top-right of the page).

3. Fill in:
   - **Title:** `LAI shared workstation` (or any descriptive name —
     just for your own reference)
   - **Key type:** `Authentication Key` (the default)
   - **Key:** paste the line you copied from Step 2

4. Click **"Add SSH key"**. GitHub may prompt for your password to
   confirm the change.

If GitHub says **"Key is already in use"** — see [Troubleshooting](#troubleshooting)
at the end.

---

## Step 4 — verify SSH now recognises you

Back in the terminal on the shared workstation:

```bash
ssh -T git@github.com
```

Expected output:

```
Hi <your-personal-github-username>! You've successfully authenticated,
but GitHub does not provide shell access.
```

That's success. The greeting must show **your** username — if it
says `Hi Ravijangid820` instead, see Troubleshooting.

---

## Step 5 — test push

The final sanity check is an actual push:

```bash
cd /data/projects/lai/LAI
git checkout -b push-test/$USER
git commit --allow-empty -m "push test from $USER"
git push origin push-test/$USER
```

This should succeed. Then clean up the test branch:

```bash
git push origin --delete push-test/$USER
git checkout develop
git branch -D push-test/$USER
```

---

## You're done

Ping rj to confirm the test push worked. That's it — from now on
your normal `git push` commands from the shared workstation will
work as you, with your collaborator access on both repos.

---

## Troubleshooting

### `ssh -T` says "Hi Ravijangid820" instead of my username

SSH is offering rj's key first. Re-run the bootstrap script —
it'll re-check your config. If that doesn't fix it, open
`~/.ssh/config` and confirm this line appears *first* inside any
`Host github.com` block:

```
    IdentityFile ~/.ssh/id_ed25519_lai
```

Save and try `ssh -T git@github.com` again.

### Push fails with "Permission to Ravijangid820/LAI.git denied to ..."

Three things to check, in order:

1. Have you accepted **both** invite emails (LAI and LAI-UI)?
   Unaccepted invites grant no access.
2. Ask rj to confirm your role on the repo is **Write** (not
   "Read" or "Triage" — those can't push).
3. `ssh -T git@github.com` — does it greet you by your personal
   username? If not, see the issue above first.

### "Key is already in use" when pasting in GitHub

You probably pasted the wrong pubkey — an existing one that's
already attached to the `TAI-Agent` account (or another GitHub
account). GitHub forbids the same key on two accounts.

The bootstrap script generated a **new, separate** key
specifically for this. Print only that one and paste it:

```bash
cat ~/.ssh/id_ed25519_lai.pub
```

(Note the `_lai` suffix — that's the new one. `id_ed25519.pub`
without the suffix is your old default key; do not paste that.)

### Other / not covered above

DM rj with the exact command you ran and the full output. Full
troubleshooting + design context is in
[`LAI/docs/TEAM_ACCESS.md`](TEAM_ACCESS.md).
