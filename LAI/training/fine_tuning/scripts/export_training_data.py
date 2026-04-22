"""
Export training_samples from the local SQLite pipeline DB to JSONL files
suitable for fine-tuning with TRL SFTTrainer.

Output format (one JSON object per line):
    {"messages": [{"role": "system"|"user"|"assistant", "content": "..."}, ...],
     "task_type": "rag_qa|summarize|...",
     "domain": "energierecht|...",
     "parent_id": 42}

Train/val split: 95/5, stratified by task_type so both sets see every task.

Usage:
    python -m training.fine_tuning.scripts.export_training_data
    # -> training/fine_tuning/data/train.jsonl, val.jsonl
"""

import json
import os
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

LAI_DIR     = Path(__file__).resolve().parents[3]
DB_PATH     = LAI_DIR / "processed" / "pipeline_local.db"
OUT_DIR     = LAI_DIR / "training" / "fine_tuning" / "data"
VAL_RATIO   = 0.05
SEED        = 42


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    conn = sqlite3.connect(str(DB_PATH))
    total = conn.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0]
    print(f"Source: {DB_PATH}")
    print(f"Total training_samples: {total:,}")

    # The `training_samples.id` column is not unique in the current SQLite
    # (artifact of export_to_sqlite.py not preserving the PRIMARY KEY).
    # Use the built-in rowid as a guaranteed-unique row identifier instead.
    by_task: dict[str, list[int]] = defaultdict(list)
    for rowid, task in conn.execute(
        "SELECT rowid, task_type FROM training_samples"
    ):
        by_task[task or "unknown"].append(rowid)

    train_ids: set[int] = set()
    val_ids:   set[int] = set()
    for task, ids in by_task.items():
        rng.shuffle(ids)
        n_val = max(1, int(len(ids) * VAL_RATIO))
        val_ids.update(ids[:n_val])
        train_ids.update(ids[n_val:])

    print(f"Train: {len(train_ids):,}   Val: {len(val_ids):,}")
    print("Task distribution (train / val):")
    for task in sorted(by_task):
        t = sum(1 for i in by_task[task] if i in train_ids)
        v = sum(1 for i in by_task[task] if i in val_ids)
        print(f"  {task:15s}  {t:>7,}  /  {v:>6,}")

    train_path = OUT_DIR / "train.jsonl"
    val_path   = OUT_DIR / "val.jsonl"

    written = {"train": 0, "val": 0}
    with open(train_path, "w", encoding="utf-8") as tf, \
         open(val_path, "w", encoding="utf-8") as vf:

        cursor = conn.execute("""
            SELECT rowid, parent_id, task_type, domain, messages, quality_score
            FROM training_samples
            ORDER BY rowid
        """)

        for rowid, parent_id, task_type, domain, messages_json, quality in cursor:
            try:
                messages = json.loads(messages_json)
                if not (isinstance(messages, list) and
                        all(isinstance(m, dict) and "role" in m and "content" in m
                            for m in messages)):
                    continue
            except (json.JSONDecodeError, TypeError):
                continue

            record = {
                "messages":      messages,
                "task_type":     task_type,
                "domain":        domain,
                "parent_id":     parent_id,
                "quality_score": quality,
            }

            bucket = "val" if rowid in val_ids else "train"
            (vf if bucket == "val" else tf).write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )
            written[bucket] += 1

    print()
    print(f"Wrote {written['train']:,} rows -> {train_path}")
    print(f"Wrote {written['val']:,} rows -> {val_path}")

    # Also write a stats file for reproducibility
    stats = {
        "total": total,
        "train": written["train"],
        "val":   written["val"],
        "val_ratio": VAL_RATIO,
        "seed": SEED,
        "task_counts": {task: len(ids) for task, ids in by_task.items()},
    }
    stats_path = OUT_DIR / "stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"Wrote stats -> {stats_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
