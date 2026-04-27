# Security Posture

> Status: V1 hardening — loopback-only services + SSH tunnel for remote access. No HTTPS, no auth yet. Suitable for solo development / a single trusted developer accessing over SSH. Not suitable for multi-user or public deployment.

## What's exposed

| Service | Port | Bind |
|---|---|---|
| `serve_rag.py` (FastAPI) | 18000 | `127.0.0.1` |
| Vite dev server | 5173 | `127.0.0.1` (default — start with `npm run dev`) |
| Analyzer LLM (`lai_analyzer_llm`) | 8005 | `127.0.0.1` |

Nothing reachable from the LAN/VPN by default. Anyone wanting to hit these services has to log in to the host first.

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

## Overriding the bind address

If you need to expose temporarily on a fully trusted local network (e.g. for a demo to a colleague on the same LAN) and have decided the data is OK to share:

```bash
LAI_BIND_HOST=0.0.0.0 .venv/bin/python scripts/serve_rag.py --port 18000
ANALYZER_BIND_HOST=0.0.0.0 docker compose -f Docker/llm-analyzer/docker-compose.yml up -d
npm --prefix web_ui/LAI run dev -- --host 0.0.0.0
```

**Don't do this on an untrusted network.** Anyone who can reach the IP will be able to read every uploaded contract and run unbounded LLM queries.

## Known gaps (not addressed in this iteration)

- **No transport encryption (HTTPS).** Tunnel-protected by SSH, but inside the host everything is plain HTTP. Add a Caddy/Traefik reverse proxy with TLS when going beyond solo use.
- **No authentication.** Any process on the host that can reach `127.0.0.1:18000` can read every session. Not a problem on a single-user workstation; revisit when sharing the host.
- **CORS still permissive (`allow_origins=["*"]`).** Fine while we're loopback-only; tighten to known origins when adding a reverse proxy.
- **No rate limits / quotas on `/query` and `/analyze-contract`.** A misbehaving caller can exhaust the LLM. OK for solo use.
- **Uploaded contracts stored unencrypted on disk** under `processed/uploads/`. Inherits the host's disk encryption posture.

When stepping up to a small-team or shared-host deployment, the right next move is option **B** from the design discussion: reverse proxy + TLS + HTTP basic auth, services still loopback. Multi-user JWT auth (option C) is only needed when there are actually multiple users with separate data.
