# LAI Infrastructure Guide

## Overview

LAI runs as a Python application on the host, backed by containerized services. Each Docker service lives in its own directory under `/data/projects/lai/Docker/` with its own `docker-compose.yml` and local data persistence.

The application code lives in `/data/projects/lai/LAI/`.

```
/data/projects/lai/
|-- LAI/                          # Application code (Python, src/lai/)
|   |-- src/lai/                  # All packages
|   |-- training/                 # Training scripts + configs
|   |-- tests/
|   |-- docs/
|   |-- pyproject.toml
|-- Docker/                       # Containerized services (one dir per service)
|   |-- database/
|   |   |-- pgvector/             # PostgreSQL + pgvector
|   |   |-- minio/                # Object storage
|   |   |-- redis/                # Cache + queue broker
|   |-- embedding/                # BGE-M3 via vLLM
|   |-- reranker/                 # Cross-encoder via vLLM
|   |-- llm/                      # Qwen2.5-7B via vLLM
|   |-- mlflow/                   # Experiment tracking
|   |-- monitoring/               # Prometheus + Grafana
```

---

## Docker Services

### Shared Network

All services communicate on the `lai_network` bridge network. Create it once:

```bash
docker network create lai_network
```

### Service Map

> **Runtime today (Apr 2026).** Models and ports below reflect what's actually running. The pipeline DB on `:5433` is from the v5-planning-target (used by `src/lai/` data pipeline); the runtime DB used by the DDiQ microservice is `lai_postgres_main` on `:5434`. The reranker and analyzer LLM have both been upgraded since this doc was first written; ML-services entries that just say "vLLM" or "Docker" were swapped for newer model checkpoints in place.

| Service | Directory / source | Container | Port | GPU | Notes |
|---|---|---|---|---|---|
| Pipeline pgvector | `Docker/database/pgvector/` | `lai_postgres` | 5433 | – | Used by `lai.pipeline` (corpus chunks, classifications). |
| **Runtime pgvector** | `Docker/database/pgvector-v2/` | `lai_postgres_main` | 5434 | – | **Used by the DDiQ microservice + chat — this is the live runtime DB.** |
| MinIO | `Docker/database/minio/` | `lai_minio` | 9000, 9001 | – | Raw corpus + segments + report exports. |
| Redis | `Docker/database/redis/` | `lai_redis` | 6380 | – | |
| Embedding | `Docker/embedding/` | `lai_embedding` | 8003 | GPU 0 | **Qwen3-Embedding-8B** (4096 dims, halfvec). Replaced BGE-M3. |
| Analyzer LLM | `Docker/llm-analyzer/` (or `Docker/llm/`) | `lai_analyzer_llm` | 8005 | GPU 0 | **Qwen3.6-27B with thinking-mode** + `--enable-prefix-caching`. Used by both serve_rag (`/query`) and DDiQ. |
| Reranker | host-side (in-process) | – | – | (host) | **Qwen3-Reranker-8B** loaded into the `serve_rag.py` host process; the standalone `Docker/reranker/` container is legacy (still tracked in compose but not started by `start.sh`). |
| Backend microservice (DDiQ) | `LAI/micro-services/` | `lai-backend` | 18001 | – | FastAPI; uses `lai_postgres_main`, talks HTTP to `lai_analyzer_llm` + `lai_embedding`, talks back to `serve_rag` for the in-process reranker. Async report flow + dedup + incremental persistence. |
| MLflow | `Docker/mlflow/` | `lai_mlflow` | 5000 | – | |
| Prometheus | `Docker/monitoring/` | `lai_prometheus` | 9090 | – | |
| Grafana | `Docker/monitoring/` | `lai_grafana` | 3001 | – | |

### Host processes (not Docker)

Started via `bash scripts/start.sh` from the LAI repo root:

| Process | Port | Notes |
|---|---|---|
| `serve_rag.py` | 18000 | Conversational chat backend — RAG pipeline + clause analyzer + 16-message conversation memory + in-process Qwen3-Reranker-8B. Default bind `127.0.0.1`; override with `LAI_BIND_HOST=0.0.0.0` for VPN-trusted LAN. |
| Vite UI | 5173 | Frontend lives in its own repo at `/data/projects/lai/LAI-UI/` (the [LAI-UI](https://github.com/Ravijangid820/LAI-UI) clone, sibling to `LAI/`). Override with `LAI_UI_DIR`. `start.sh` runs `npm install` on first launch. |

### Starting Services

Each service is independent. Start only what you need:

```bash
# Infrastructure (almost always needed)
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d
cd /data/projects/lai/Docker/database/redis && docker compose up -d

# ML services (start what you need)
cd /data/projects/lai/Docker/embedding && docker compose up -d
cd /data/projects/lai/Docker/llm && docker compose up -d

# Optional
cd /data/projects/lai/Docker/database/minio && docker compose up -d
cd /data/projects/lai/Docker/reranker && docker compose up -d
cd /data/projects/lai/Docker/mlflow && docker compose up -d
cd /data/projects/lai/Docker/monitoring && docker compose up -d
```

### Stopping Services

```bash
cd /data/projects/lai/Docker/llm && docker compose down        # stop one service
cd /data/projects/lai/Docker/llm && docker compose down -v     # stop + delete volumes
```

### Data Persistence

Each service stores data locally in its own directory (not Docker volumes):

| Service | Persistence Path | What's Stored |
|---------|-----------------|---------------|
| pgvector | `Docker/database/pgvector/data/` | PostgreSQL data files |
| MinIO | `Docker/database/minio/data/` | Object storage buckets |
| Redis | `Docker/database/redis/data/` | AOF append-only file |
| Embedding | `Docker/embedding/model-cache/` | HuggingFace model weights |
| Reranker | `Docker/reranker/model-cache/` | HuggingFace model weights |
| LLM | `Docker/llm/model-cache/` | HuggingFace model weights |
| MLflow | Uses PostgreSQL + MinIO | Metrics in DB, artifacts in MinIO |

Data directories are `.gitignore`d — only the compose files and configs are tracked.

---

## Experiment Tracking (MLflow)

MLflow tracks every training run so you can compare configurations, metrics, and model artifacts across experiments.

### How It Works

```
Training Script --> MLflow Server (port 5000)
                        |
              +---------+---------+
              |                   |
         PostgreSQL          MinIO
    (params, metrics,    (model checkpoints,
     run metadata)        plots, artifacts)
```

### Prerequisites

MLflow needs PostgreSQL and MinIO running first:

```bash
cd /data/projects/lai/Docker/database/pgvector && docker compose up -d
cd /data/projects/lai/Docker/database/minio && docker compose up -d
cd /data/projects/lai/Docker/mlflow && docker compose up -d
```

The MLflow compose includes an init container that auto-creates the `mlflow-artifacts` bucket in MinIO.

### Using MLflow in Training Code

Install the training dependencies:

```bash
cd /data/projects/lai/LAI
uv pip install -e ".[training]"
```

In your training script:

```python
import mlflow

mlflow.set_tracking_uri("http://localhost:5000")
mlflow.set_experiment("qwen-finetune")

with mlflow.start_run(run_name="lr2e5-ep3-lora-r16"):
    # Log all training parameters
    mlflow.log_params({
        "base_model": "Qwen/Qwen2.5-7B-Instruct",
        "learning_rate": 2e-5,
        "epochs": 3,
        "batch_size": 32,
        "lora_r": 16,
        "lora_alpha": 32,
        "max_seq_len": 8192,
        "warmup_steps": 100,
        "dataset_size": 15000,
        "domains": "bimschg,ewg,contract,land",
    })

    # Log metrics during training
    for epoch in range(3):
        train_loss, eval_loss = train_epoch(...)
        mlflow.log_metrics({
            "train_loss": train_loss,
            "eval_loss": eval_loss,
        }, step=epoch)

    # Log final evaluation metrics
    mlflow.log_metrics({
        "ndcg@10": 0.82,
        "mrr": 0.76,
        "legal_ref_recall": 0.65,
        "legal_ref_precision": 0.58,
    })

    # Log model checkpoint as artifact
    mlflow.log_artifact("checkpoints/best-model/")

    # Log training config file
    mlflow.log_artifact("training/configs/qwen_lora.yaml")
```

### Comparing Runs

Open the MLflow UI at `http://localhost:5000`:

1. Select an experiment (e.g., "qwen-finetune")
2. Check multiple runs
3. Click "Compare" to see parameter and metric differences side by side
4. View loss curves, download artifacts

### What to Track

| Category | Examples |
|----------|---------|
| **Params** | model name, lr, epochs, batch size, LoRA rank, dataset size, chunk size |
| **Metrics** | train/eval loss, NDCG, MRR, legal ref recall/precision, latency |
| **Artifacts** | model checkpoints, training configs, evaluation reports, plots |
| **Tags** | `mlflow.note.content` for free-text notes about the run |

---

## Running the Application

The LAI application runs on the host (not in a container):

```bash
cd /data/projects/lai/LAI
uv run main.py serve   # starts FastAPI on port 8000
```

It connects to the Docker services via localhost:

| Service | Connection |
|---------|-----------|
| PostgreSQL | `localhost:5433` |
| Redis | `localhost:6380` |
| MinIO | `localhost:9000` |
| Embedding | `localhost:8003` |
| Reranker | `localhost:8004` |
| LLM | `localhost:8001` |

These are configured via environment variables or `.env` file (see `.env.example`).

---

## Environment Variables

Key variables (all have defaults in `lai.core.config`):

```bash
# Database
PGHOST=localhost
PGPORT=5433
PGDATABASE=lai_db
PGUSER=lai_user
PGPASSWORD=lai_test_password_2024

# Redis
REDIS_HOST=localhost
REDIS_PORT=6380

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=laiadmin
MINIO_SECRET_KEY=superStrongPassword123!

# ML Services
EMBEDDING_URL=http://localhost:8003/v1
RERANKER_CROSS_ENCODER_URL=http://localhost:8004/v1
LLM_URL=http://localhost:8001/v1
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct

# MLflow
MLFLOW_TRACKING_URI=http://localhost:5000

# GPU assignment
CUDA_DEVICE=1          # LLM GPU (embedding+reranker share GPU 0)
```

---

## Versioning Strategy

- **Code:** Git tags (`v5.0.0`, `v5.1.0`) — no version directories
- **Experiments:** MLflow run IDs — every training run is logged with full config
- **Models:** MLflow artifact store (MinIO) — checkpoints stored per run
- **Data:** DVC (future) — version large datasets alongside git
- **Docker images:** Pin versions in compose files for production

---

## Typical Workflows

### Training a New Model

```bash
# 1. Start infrastructure
cd Docker/database/pgvector && docker compose up -d
cd Docker/database/minio && docker compose up -d
cd Docker/mlflow && docker compose up -d

# 2. Run training (MLflow logs everything automatically)
cd LAI
uv run python training/train_qwen_lora.py --config training/configs/qwen_lora.yaml

# 3. Compare with previous runs at http://localhost:5000
# 4. If better, deploy the new checkpoint to Docker/llm/
```

### Serving the Full Stack

```bash
# 1. Start all services
cd Docker/database/pgvector && docker compose up -d
cd Docker/database/redis && docker compose up -d
cd Docker/embedding && docker compose up -d
cd Docker/reranker && docker compose up -d
cd Docker/llm && docker compose up -d

# 2. Start the application
cd LAI
uv run main.py serve
```

### Adding a New Docker Service

1. Create `Docker/<category>/<service-name>/`
2. Add `docker-compose.yml` with `lai_network` (external)
3. Add `data/` or `model-cache/` for persistence
4. Add `.gitkeep` in the data dir
5. Update `Docker/.gitignore`
