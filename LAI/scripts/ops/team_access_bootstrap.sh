#!/usr/bin/env bash
# team_access_bootstrap.sh — Phase 4.5.5, option (a) path.
#
# Run this AS YOURSELF (not as rj) on the shared LAI workstation. It:
#   1. Generates a separate ED25519 keypair at ~/.ssh/id_ed25519_lai
#      (does NOT touch any existing key — your TAI-Agent access keeps working).
#   2. Updates ~/.ssh/config so SSH prefers the new key when talking to
#      github.com, with your existing default keys as fallback.
#   3. Prints the new public key + step-by-step instructions for adding
#      it to YOUR personal GitHub account at github.com/settings/keys.
#   4. Prints test-push commands.
#
# Idempotent — safe to re-run if you re-attached the key or your config
# got reverted. If ~/.ssh/config already has a `Host github.com` block,
# the script bails and prints what line to add manually instead.
#
# After running:
#   - Add the printed pubkey to your PERSONAL GitHub account.
#   - Tell rj your personal GitHub username so he can invite you to
#     Ravijangid820/LAI and Ravijangid820/LAI-UI as a collaborator.
#   - Accept the two GitHub email invites.
#   - Re-run `ssh -T git@github.com` — it should now greet you by your
#     personal GH username.
#   - Run the test-push block printed at the end.

set -euo pipefail

# -- safety guard: don't run as rj (his key is already configured) --
if [ "${USER:-$(whoami)}" = "rj" ]; then
    cat <<'EOF'
This script is for team members other than rj. rj's box key is already
attached to the Ravijangid820 account and pushes already work for him.
Exiting without changes.
EOF
    exit 0
fi

SSH_DIR="${HOME}/.ssh"
NEW_KEY="${SSH_DIR}/id_ed25519_lai"
PUBKEY="${NEW_KEY}.pub"
SSH_CONFIG="${SSH_DIR}/config"
BLOCK_MARKER="# === LAI personal-GitHub identity (Phase 4.5.5, option a) ==="

mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

# -- 1. Generate the keypair (idempotent) --
if [ -f "${NEW_KEY}" ]; then
    echo "[i] key already exists at ${NEW_KEY} — keeping it."
else
    echo "[i] generating new ED25519 key at ${NEW_KEY}..."
    ssh-keygen -t ed25519 -f "${NEW_KEY}" -N "" -C "${USER}@blockland.ae-LAI-$(date +%Y%m%d)"
fi
chmod 600 "${NEW_KEY}"
chmod 644 "${PUBKEY}"

# -- 2. Update ~/.ssh/config (idempotent + safe) --
existing_github_block=""
if [ -f "${SSH_CONFIG}" ] && grep -qE '^[[:space:]]*Host[[:space:]].*github\.com' "${SSH_CONFIG}" 2>/dev/null; then
    existing_github_block="yes"
fi

if [ -n "${existing_github_block}" ] && ! grep -q "${BLOCK_MARKER}" "${SSH_CONFIG}" 2>/dev/null; then
    cat <<EOF

[!] ${SSH_CONFIG} already contains a 'Host github.com' block that this
    script didn't write. Refusing to clobber.

    To finish setup MANUALLY, add this single line inside your existing
    'Host github.com' block (right after 'HostName github.com' if it
    exists, or as the first IdentityFile entry):

        IdentityFile ${NEW_KEY}

    Then save and continue with step 3 below (the pubkey paste).

EOF
elif ! grep -q "${BLOCK_MARKER}" "${SSH_CONFIG}" 2>/dev/null; then
    # Build the block. Chain the user's existing default keys as fallbacks
    # so any other github-using flow (TAI-Agent etc.) keeps working.
    {
        echo ""
        echo "${BLOCK_MARKER}"
        echo "# Prefers id_ed25519_lai when talking to github.com (this key must"
        echo "# be attached to YOUR personal GitHub account, which is a"
        echo "# collaborator on Ravijangid820/LAI{,-UI}). Falls back to any"
        echo "# existing default key so TAI-Agent push continues to work."
        echo "Host github.com"
        echo "    HostName github.com"
        echo "    User git"
        echo "    IdentitiesOnly yes"
        echo "    IdentityFile ${NEW_KEY}"
        for fallback in "${SSH_DIR}/id_ed25519" "${SSH_DIR}/id_rsa" "${SSH_DIR}/id_ecdsa"; do
            if [ -f "${fallback}" ] && [ "${fallback}" != "${NEW_KEY}" ]; then
                echo "    IdentityFile ${fallback}"
            fi
        done
    } >> "${SSH_CONFIG}"
    chmod 600 "${SSH_CONFIG}"
    echo "[i] appended LAI block to ${SSH_CONFIG}"
else
    echo "[i] ${SSH_CONFIG} already has the LAI block — skipping."
fi

# -- 3. Print pubkey + instructions --
cat <<EOF

====================================================================
  Step 1 of 3 — add this public key to your PERSONAL GitHub account
====================================================================
EOF
cat "${PUBKEY}"
cat <<EOF

  How:
  1. Log into github.com as YOUR personal account (not TAI-Agent).
  2. Open https://github.com/settings/keys
  3. Click "New SSH key"
       - Title:    LAI on shared workstation (${USER})
       - Key type: Authentication Key
       - Key:      paste the block above (one line, starts with 'ssh-ed25519')
     Click "Add SSH key".
  4. Tell rj your personal GitHub username so he can invite you as a
     collaborator on Ravijangid820/LAI and Ravijangid820/LAI-UI.
  5. Accept the two invite emails.

====================================================================
  Step 2 of 3 — verify the SSH identity flips to YOUR personal account
====================================================================
  ssh -T git@github.com
  # Expected: "Hi <your-personal-github-username>! You've successfully ..."
  # If it still says "Hi Ravijangid820", re-run this script or check
  # that the IdentityFile line above appears at the TOP of your
  # 'Host github.com' block in ${SSH_CONFIG}.

====================================================================
  Step 3 of 3 — test push from the LAI repo
====================================================================
  cd /data/projects/lai/LAI
  git checkout -b push-test/${USER}
  git commit --allow-empty -m "push test from ${USER}"
  git push origin push-test/${USER}      # expected: succeeds
  # cleanup:
  git push origin --delete push-test/${USER}
  git checkout develop && git branch -D push-test/${USER}

Done. Re-run anytime if your key or config gets clobbered.
EOF
