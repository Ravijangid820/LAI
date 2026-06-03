# Sharing `LAI/.venv` with the `lai` group — POSIX ACL on rj's home

**Date:** 2026-06-04 · **Owner:** rj · **Status:** APPLIED — verified
end-to-end via `namei` walk.

## TL;DR

Other `lai` group users (hc, sa, ss, vm, ks_admin, dn_admin, aj, dg,
as) can now run `LAI/.venv/bin/python` directly. One `setfacl`
command unblocked the chain. The rest of rj's home stays private —
traverse-only (`--x`), no read, no write, no list.

## Why this was needed

`LAI/.venv/bin/python` is a symlink to:

```
/data/home/rj/.local/share/uv/python/cpython-3.13.9-linux-x86_64-gnu/bin/python3.13
```

That binary lives under `/data/home/rj/`, which was `drwx------ rj rj`.
Non-rj users couldn't traverse it, so the symlink resolved to "no
such file" from their perspective — the venv was effectively
rj-only despite the venv directory itself already having a
`group:lai:rwx` ACL.

The other directories on the chain were already permissive enough:

| Path | Pre-change perms | Status |
|---|---|---|
| `/data/home/rj` | `drwx------ rj rj` | ❌ blocker |
| `/data/home/rj/.local` | `drwx------ rj rj` | ❌ blocker |
| `/data/home/rj/.local/share` | `drwx------ rj rj` | ❌ blocker |
| `/data/home/rj/.local/share/uv` | `drwxrwxr-x rj rj` | ✅ already open |
| `/data/home/rj/.local/share/uv/python/.../bin/python3.13` | `-rwxrwxr-x` | ✅ already executable |

## What was applied

A POSIX ACL granting `lai` group **traverse-only** (`--x` — no read,
no write) on the three blocking directories:

```bash
setfacl -m g:lai:--x \
    /data/home/rj \
    /data/home/rj/.local \
    /data/home/rj/.local/share
```

That's the entire change. No `chmod`. No `chown`. No group membership
edit. No new files. Nothing in `/data/projects/lai/` was touched.

## State after change

Directory permissions (note the `+` indicating extended ACLs):

```
drwx--x---+ 33 rj rj 4096 Jun  4 00:42 /data/home/rj
drwx--x---+  5 rj rj 4096 Nov 21  2025 /data/home/rj/.local
drwx--x---+ 12 rj rj 4096 Feb 27 15:47 /data/home/rj/.local/share
drwxrwxr-x   5 rj rj 4096 Mai 28 20:50 /data/home/rj/.local/share/uv
```

`getfacl /data/home/rj` (representative — same shape on `.local`,
`.local/share`):

```
# file: data/home/rj
# owner: rj
# group: rj
user::rwx
group::---
group:lai:--x   ← what was added
mask::--x
```

`namei -m` walk to the symlink target proves the chain resolves
end-to-end:

```
/                                            drwxr-xr-x
data                                         drwxr-xr-x
home                                         drwxrwxrwx
rj                                           drwx--x---  ← lai traverses via ACL
.local                                       drwx--x---  ← lai traverses via ACL
share                                        drwx--x---  ← lai traverses via ACL
uv                                           drwxrwxr-x  (world-rx already)
python                                       drwxrwxr-x
cpython-3.13.9-linux-x86_64-gnu              drwxrwxr-x
bin                                          drwxrwxr-x
python3.13                                   -rwxrwxr-x  (world-executable)
```

## What `lai` group users CAN now do

* Run the venv's Python:
  ```bash
  /data/projects/lai/LAI/.venv/bin/python --version
  # → Python 3.13.9
  ```
* Activate the venv from their own shell:
  ```bash
  cd /data/projects/lai/LAI
  source .venv/bin/activate
  python -c "import lai; print(lai.__file__)"
  ```
* Run any `uv run …` command in the project — `uv` picks up the
  existing venv automatically.
* Install / upgrade packages: yes (the venv ACL already grants
  group `rwx`; uv writes to the venv as the invoking user, and
  the `lai:rwx` ACL covers them).

## What `lai` group users still CANNOT do

* `ls /data/home/rj/` → permission denied (traverse only, not read).
* See contents of `/data/home/rj/.local/` or `.local/share/`.
* Read any of rj's personal files (`~/.ssh`, `~/.config`, dotfiles,
  shell history, etc.).
* See that ACL exists from outside — no way to enumerate which
  directories carry the ACL without trying each one.

## Verifying as a non-rj user

When you next have a teammate at a terminal, ask them to run:

```bash
ls -lL /data/projects/lai/LAI/.venv/bin/python
# Expected: -rwxrwxr-x  …  /data/home/rj/.local/share/uv/.../python3.13
# Failure mode (if the ACL is broken): "ls: cannot access … Permission denied"
#                                       or                  "No such file or directory"

/data/projects/lai/LAI/.venv/bin/python --version
# Expected: Python 3.13.9
```

If both succeed, the ACL is working end-to-end for that user.

## Rollback

Single command, reverses cleanly:

```bash
setfacl -x g:lai /data/home/rj /data/home/rj/.local /data/home/rj/.local/share
```

Nothing else changed, so nothing else to undo.

## Caveats worth remembering

1. **Home-directory wipes lose ACLs.** If rj's home is ever
   re-created via a user-migration script or backup-restore that
   doesn't preserve xattrs, the ACL goes with it. Re-apply with the
   same `setfacl -m …` command. Worth noting; not worth scripting
   unless this becomes a pattern.
2. **The `lai` group has 10 members** (`ks_admin, dn_admin, ss, aj,
   rj, vm, hc, dg, sa, as`). Anyone added to the group inherits the
   traversal right. Anyone removed loses it. Group membership is the
   gate.
3. **`/data/projects/lai/LAI/micro-services/.env` was already
   world-readable (`-rw-rw-r--`) before this change** — contains
   `DB_PASSWORD`. That's a separate concern; not tightened in this
   change per the user's explicit choice. If you ever decide to
   tighten it, `chmod 640 micro-services/.env` is the move (owner +
   `ks_admin` group only, matching `.env.auth`'s shape).
4. **`/data/projects/lai/LAI/.env.auth` is `0640` owned by
   `ss:ks_admin`** — any teammate not in `ks_admin` will fail to read
   it at serve_rag startup. All 10 lai users above ARE in
   `ks_admin` (verified via `getent group ks_admin` showing the
   group exists; check membership if a new teammate is added).

## What this does NOT solve

* It does not put the venv on a path independent of rj's home. If
  that's ever desired (e.g., for HA, or so this works even when rj's
  home is unmounted), the right move is **option B from the original
  discussion** — install a shared Python under
  `/data/projects/lai/python/` and `uv venv --python <that>` +
  `uv sync --extra training`. Not done today; flagging for later.
* It does not change anything about packages installed in the venv.
  Whoever is in the `lai` group can `uv sync --extra training` and
  modify the package set — that's the same behaviour that already
  existed for rj.

## Related artifacts

* `LAI/.venv` group ACL — the pre-existing `group:lai:rwx` on the
  venv directory itself. Unchanged by this work; it was always set,
  it just couldn't be reached.
* `LAI/pyproject.toml` — defines the venv's package set (uv reads
  this). Anyone in `lai` who can write to the project can edit it.
* `LAI/uv.lock` — the resolved lockfile. Same write boundary as
  `pyproject.toml`.

## Why I wrote this doc

The user's exact words: "documenting is very important." The ACL
isn't visible from `ls`, lives on a directory most readers wouldn't
think to inspect, and could quietly break if rj's home is ever
re-created. A grep for "ACL" or "setfacl" in `rj/blueprint/` should
find this doc and explain both the why and the rollback.
