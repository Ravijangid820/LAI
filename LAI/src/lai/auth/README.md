# `lai.auth` — authentication

User authentication and account management.

| Module | Role |
|---|---|
| `jwt.py` | JWT issue / verify (HMAC-signed, shared `AUTH_SECRET`). |
| `repository.py` | User persistence. |
| `routes.py` | FastAPI auth routes (signup, login). |

See [`LAI/TODO.md`](../../../TODO.md) for the auth roadmap.

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
