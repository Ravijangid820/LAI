#!/usr/bin/env python3
"""
eval_api.py — Lawyer-blind A/B evaluation backend (vm-9, roadmap §3.4 ship-gate).

The Phase 3 LoRA ships only if a lawyer-blind labelling session over 50 BImSchG
questions prefers it (or ties) vs the base. This is the runner: a tiny FastAPI
service that randomises **left / right** per question so the lawyer sees two
unlabelled answer panels and never which model produced which. The server keeps
the model-to-side mapping; the client only ever receives ``left`` / ``right``.

Design choices, since this is the only place where a mistake silently invalidates
the §3.4 ship-gate:

1. **L/R mapping never leaves the server.** ``GET /eval/question/{idx}`` returns
   ``{question, left, right}``. The strings ``model_a`` / ``model_b`` are
   internal-only; they show up in ``results`` and the CSV export (which the
   experimenter, not the lawyer, looks at). A leaked mapping would let the
   lawyer post-hoc skew an answer.

2. **Deterministic shuffle from a seed, persisted at first start.** The shuffle
   is computed once when the state file is created and stored alongside the
   scores. A restart of the service preserves the mapping (otherwise idx 12's
   "left" panel could swap between sessions, ruining intermediate scores). Seed
   is auto-generated unless ``EVAL_SHUFFLE_SEED`` is set (reproducible runs in
   tests).

3. **Last-write-wins on scores.** A lawyer who tapped the wrong button can come
   back to the same question via ``GET /eval/question/{idx}`` and re-score —
   the new POST overwrites. We do NOT keep a history of misclicks (avoids tempting
   anyone to "filter" the data later).

4. **JSONL questions, JSON state.** Questions are append-friendly JSONL so the
   set can be edited without parsing the whole file. State is a single JSON
   blob written via atomic-replace so a crash mid-write never corrupts it.

5. **No auth.** Per spec: runs on local network only. CORS is open so an iPad
   on the same LAN can hit it without a tunnel.

6. **Pre-generated answers preferred over live model calls.** Each question
   carries ``model_a_answer`` and ``model_b_answer`` inline. An ops pipeline
   generates both sides offline (or via a one-shot batch script), so the
   labelling session is not coupled to vLLM uptime.

Endpoints
---------
* ``GET /eval/health``                — ``{ok, total, scored, seed}``
* ``GET /eval/question/{idx}``        — ``{idx, total, id, question, category, left, right, scored}``
* ``POST /eval/score/{idx}``          — body ``{choice: "left"|"right"|"equal"}`` → ``{ok}``
* ``GET /eval/results``               — ``{model_a_wins, model_b_wins, ties, total, scored}``  (DEBLINDED — experimenter only)
* ``GET /eval/export.csv``            — CSV with deblinded rows for the §3.4 write-up

Run
---
::

    cd LAI/micro-services
    uvicorn eval_api:app --host 0.0.0.0 --port 18002

Env
---
* ``EVAL_QUESTIONS_PATH`` — default ``LAI/eval_questions/bimschg_50.jsonl``
* ``EVAL_STATE_PATH``     — default ``LAI/eval_questions/results.json``
* ``EVAL_SHUFFLE_SEED``   — optional int; deterministic shuffle for tests
"""

from __future__ import annotations

import csv
import io
import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# `Path(__file__).parent` = `LAI/micro-services/`; `.parent` = `LAI/`.
_LAI_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_QUESTIONS = _LAI_ROOT / "eval_questions" / "bimschg_50.jsonl"
_DEFAULT_STATE = _LAI_ROOT / "eval_questions" / "results.json"

# Placeholder shown when ``model_a_answer`` / ``model_b_answer`` is empty in
# the questions file. The lawyer should never see this in a real session;
# making it loud means the experimenter notices a missing populate step
# before the lawyer arrives.
_MISSING_ANSWER = (
    "[no answer recorded yet — populate model_a_answer / model_b_answer "
    "in bimschg_50.jsonl before the labelling session]"
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class _EvalState:
    """Single-process state holder. The labelling session runs on local network
    with one lawyer at a time, so a process-level lock is enough — no DB."""

    def __init__(
        self,
        questions_path: Path,
        state_path: Path,
        seed: int | None,
    ) -> None:
        self.questions_path = questions_path
        self.state_path = state_path
        self.lock = Lock()
        self.questions = self._load_questions()
        if not self.questions:
            raise RuntimeError(
                f"no questions loaded from {self.questions_path} — "
                f"populate the 50-question set before starting the eval API"
            )
        self.state = self._load_or_init_state(seed)

    def _load_questions(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.questions_path.exists():
            raise FileNotFoundError(
                f"questions file not found: {self.questions_path}. "
                f"Create it from the template in LAI/eval_questions/README.md."
            )
        with self.questions_path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                try:
                    d = json.loads(s)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"{self.questions_path}:{line_no} is not valid JSON: {exc}") from exc
                if "question" not in d:
                    raise RuntimeError(f"{self.questions_path}:{line_no} missing required 'question' field")
                rows.append(d)
        return rows

    def _load_or_init_state(self, seed: int | None) -> dict[str, Any]:
        if self.state_path.exists():
            existing = json.loads(self.state_path.read_text(encoding="utf-8"))
            # If the questions file grew since last start, extend the mapping
            # with deterministically-shuffled new entries. The seed in the
            # existing state file is the source of truth.
            mapping = existing.get("mapping", {})
            if len(mapping) < len(self.questions):
                rng = random.Random(existing["seed"])
                # Re-derive the first N entries so the RNG state advances
                # identically to the original session, then add the rest.
                for i in range(len(self.questions)):
                    flip = rng.random() < 0.5
                    key = str(i)
                    if key not in mapping:
                        mapping[key] = {"left": "a", "right": "b"} if flip else {"left": "b", "right": "a"}
                existing["mapping"] = mapping
                self._write_state(existing)
            return existing

        rng_seed = seed if seed is not None else random.randrange(2**31)
        rng = random.Random(rng_seed)
        mapping = {
            str(i): ({"left": "a", "right": "b"} if rng.random() < 0.5 else {"left": "b", "right": "a"})
            for i in range(len(self.questions))
        }
        state: dict[str, Any] = {
            "seed": rng_seed,
            "started_at": datetime.now(UTC).isoformat(),
            "mapping": mapping,
            "scores": {},
        }
        self._write_state(state)
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: write to sibling .tmp then rename. A crash mid-write
        # never leaves a partial state file (which would lose the L/R mapping
        # and silently re-randomise the next start).
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.state_path)

    # ---- public ops ----

    def get_question_view(self, idx: int) -> dict[str, Any]:
        self._check_idx(idx)
        q = self.questions[idx]
        mapping = self.state["mapping"][str(idx)]
        left_src = mapping["left"]  # "a" or "b"
        right_src = mapping["right"]
        a_ans = q.get("model_a_answer", "") or ""
        b_ans = q.get("model_b_answer", "") or ""
        left = (a_ans if left_src == "a" else b_ans) or _MISSING_ANSWER
        right = (a_ans if right_src == "a" else b_ans) or _MISSING_ANSWER
        score_rec = self.state["scores"].get(str(idx))
        return {
            "idx": idx,
            "total": len(self.questions),
            "id": q.get("id", f"q{idx + 1:02d}"),
            "question": q["question"],
            "category": q.get("category"),
            "left": left,
            "right": right,
            "scored": score_rec["choice"] if score_rec else None,
        }

    def record_score(self, idx: int, choice: str) -> None:
        self._check_idx(idx)
        with self.lock:
            self.state["scores"][str(idx)] = {
                "choice": choice,
                "ts": datetime.now(UTC).isoformat(),
            }
            self._write_state(self.state)

    def results(self) -> dict[str, int]:
        a_wins = b_wins = ties = 0
        for idx_str, sc in self.state["scores"].items():
            choice = sc["choice"]
            if choice == "equal":
                ties += 1
                continue
            picked_side = self.state["mapping"][idx_str][choice]  # "a" or "b"
            if picked_side == "a":
                a_wins += 1
            else:
                b_wins += 1
        return {
            "model_a_wins": a_wins,
            "model_b_wins": b_wins,
            "ties": ties,
            "total": len(self.questions),
            "scored": len(self.state["scores"]),
        }

    def export_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "idx",
                "id",
                "category",
                "question",
                "model_a_answer",
                "model_b_answer",
                "left_model",
                "lawyer_choice",
                "choice_resolved",
                "ts",
            ]
        )
        for i, q in enumerate(self.questions):
            mapping = self.state["mapping"][str(i)]
            sc = self.state["scores"].get(str(i), {})
            choice = sc.get("choice", "")
            if choice == "equal":
                resolved = "equal"
            elif choice in ("left", "right"):
                resolved = "model_a" if mapping[choice] == "a" else "model_b"
            else:
                resolved = ""
            w.writerow(
                [
                    i,
                    q.get("id", ""),
                    q.get("category", ""),
                    q.get("question", ""),
                    q.get("model_a_answer", ""),
                    q.get("model_b_answer", ""),
                    "model_a" if mapping["left"] == "a" else "model_b",
                    choice,
                    resolved,
                    sc.get("ts", ""),
                ]
            )
        return buf.getvalue()

    def _check_idx(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.questions):
            raise HTTPException(
                status_code=404,
                detail=f"question idx {idx} out of range (0..{len(self.questions) - 1})",
            )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="LAI lawyer-blind A/B eval", version="1.0.0")

# Per spec: runs on local network only; CORS open so an iPad on the same LAN
# can hit it without a tunnel. If this is ever exposed beyond a LAN, lock CORS
# down — but the threat model right now is "wrong button on the wrong network",
# not "internet attacker".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScoreBody(BaseModel):
    choice: Literal["left", "right", "equal"] = Field(
        ...,
        description="Lawyer's pick: 'left' (Antwort A besser), 'right' (Antwort B besser), or 'equal' (Beide gleich).",
    )


_STATE: _EvalState | None = None


def _state() -> _EvalState:
    global _STATE
    if _STATE is None:
        questions = Path(os.environ.get("EVAL_QUESTIONS_PATH") or _DEFAULT_QUESTIONS)
        state_path = Path(os.environ.get("EVAL_STATE_PATH") or _DEFAULT_STATE)
        seed_env = os.environ.get("EVAL_SHUFFLE_SEED")
        seed = int(seed_env) if seed_env else None
        _STATE = _EvalState(questions, state_path, seed)
    return _STATE


@app.get("/eval/health")
def health() -> dict[str, Any]:
    s = _state()
    return {
        "ok": True,
        "total": len(s.questions),
        "scored": len(s.state["scores"]),
        "seed": s.state["seed"],
        "started_at": s.state["started_at"],
    }


@app.get("/eval/question/{idx}")
def get_question(idx: int) -> dict[str, Any]:
    return _state().get_question_view(idx)


@app.post("/eval/score/{idx}")
def post_score(idx: int, body: ScoreBody) -> dict[str, Any]:
    _state().record_score(idx, body.choice)
    return {"ok": True, "idx": idx, "choice": body.choice}


@app.get("/eval/results")
def get_results() -> dict[str, int]:
    """DEBLINDED results — for the experimenter only. The lawyer should not
    navigate here mid-session; the FE never links to it."""
    return _state().results()


@app.get("/eval/export.csv")
def export_csv() -> Response:
    csv_text = _state().export_csv()
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="bimschg_50_eval.csv"'},
    )


if __name__ == "__main__":  # pragma: no cover — convenience launcher
    import uvicorn

    uvicorn.run(
        "eval_api:app",
        host=os.environ.get("EVAL_HOST", "0.0.0.0"),
        port=int(os.environ.get("EVAL_PORT", "18002")),
        log_level="info",
    )
