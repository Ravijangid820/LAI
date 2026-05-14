# `lai.infra` — infrastructure clients

Thin clients for the backing services. No business logic — just connection
management and primitive operations.

| Module | Role |
|---|---|
| `database.py` | PostgreSQL / pgvector client. |
| `minio.py` | MinIO object-storage client. |
| `redis.py` | Redis client. |

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
