# Security Posture

> Status: VPN-trusted mode is the practical default. The code is secure-by-default (loopback-only) but each deployment chooses its bind address via env vars based on its trust model. No HTTPS, no auth yet — those are the next steps when going beyond a single trusted operator on a private VPN.

## Three trust models, one codebase

The same code supports three deployment shapes via env vars; pick the one that matches your network:

| Mode | When to pick | How |
|---|---|---|
| **A. Loopback + SSH tunnel** | Most secure. Single dev with SSH access; no shared VPN. | Code default — start `serve_rag` with no env override; do `ssh -L 5173:localhost:5173 -L 18000:localhost:18000 user@server` from your laptop. |
| **B. VPN-trusted LAN** | Solo dev or small team behind a corporate/private VPN (FortiClient, WireGuard, etc.). The VPN is the auth gate. | `LAI_BIND_HOST=0.0.0.0 ANALYZER_BIND_HOST=0.0.0.0 npm run dev -- --host 0.0.0.0`. Browse `http://<server-lan-ip>:5173/`. |
| **C. Public / shared host** | Multi-user or unreachable-by-VPN. | Not yet supported — see "Known gaps" below; needs reverse proxy + TLS + auth. |

## What's exposed (default code config)

| Service | Port | Default bind | Override env var |
|---|---|---|---|
| `serve_rag.py` (FastAPI) | 18000 | `127.0.0.1` | `LAI_BIND_HOST` |
| Vite dev server | 5173 | `127.0.0.1` (default — start with `npm run dev`) | pass `--host 0.0.0.0` if needed |
| Analyzer LLM (`lai_analyzer_llm`) | 8005 | `127.0.0.1` | `ANALYZER_BIND_HOST` |

Default code config = nothing reachable from the LAN/VPN. Override per-deployment to fit your trust model (above).

## Accessing from a remote machine — SSH tunnel

```bash
# On your laptop, with one terminal window:
ssh -N \
    -L 5173:localhost:5173 \
    -L 18000:localhost:18000 \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    user@server
```

`-N` = no remote shell, just port-forward. The two `-L` flags forward Vite + serve_rag onto your laptop's localhost. The `ServerAlive*` opts reconnect dropped tunnels (useful over flaky VPN).

Then in your browser: **`http://localhost:5173/`** — works exactly as before.

If your SSH connection drops the tunnel breaks. Easiest robust option: wrap with [`autossh`](https://www.harding.motd.ca/autossh/):

```bash
autossh -M 0 -N \
    -L 5173:localhost:5173 \
    -L 18000:localhost:18000 \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    user@server
```

`autossh` re-establishes the tunnel automatically when the underlying SSH session dies.

## VPN-trusted mode (current default at this site)

Used when access is gated by a VPN like FortiClient — anyone past the VPN can reach the services directly, no SSH tunnel needed.

```bash
# serve_rag
LAI_BIND_HOST=0.0.0.0 .venv/bin/python -m lai.api.serve_rag --port 18000

# analyzer LLM container
ANALYZER_BIND_HOST=0.0.0.0 docker compose -f Docker/llm-analyzer/docker-compose.yml up -d

# UI
cd web_ui/LAI && npm run dev -- --host 0.0.0.0
```

UI .env points at the server's LAN IP:
```
VITE_BACKEND_URL=http://<server-lan-ip>:18000
```

**Trust assumption:** the VPN access list is the auth boundary. Whoever your VPN admin lets in, can read every uploaded contract and run any query. If your VPN has many users, you should layer auth on top (see "Known gaps").

## Known gaps (not addressed in this iteration)

- **No transport encryption (HTTPS).** Tunnel-protected by SSH, but inside the host everything is plain HTTP. Add a Caddy/Traefik reverse proxy with TLS when going beyond solo use.
- **No authentication.** Any process on the host that can reach `127.0.0.1:18000` can read every session. Not a problem on a single-user workstation; revisit when sharing the host.
- **CORS still permissive (`allow_origins=["*"]`).** Fine while we're loopback-only; tighten to known origins when adding a reverse proxy.
- **No rate limits / quotas on `/query` and `/analyze-contract`.** A misbehaving caller can exhaust the LLM. OK for solo use.
- **Uploaded contracts stored unencrypted on disk** under `processed/uploads/`. Inherits the host's disk encryption posture.

When stepping up to a small-team or shared-host deployment, the right next move is option **B** from the design discussion: reverse proxy + TLS + HTTP basic auth, services still loopback. Multi-user JWT auth (option C) is only needed when there are actually multiple users with separate data.
