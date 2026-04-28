"""One-shot backfill — synthesize message rows from session metadata.

Used after we shipped POST /sessions/{id}/messages: existing sessions
with an upload + analysis but no message rows would otherwise show up
empty on refresh. This walks every session, and for any that has a
filename and/or analysis but no messages, inserts the bubbles the UI
*would have* produced if persistence had been wired at the time.

Idempotent — only writes when the session has zero messages and there's
something to recover. Safe to re-run.

Usage:  .venv/bin/python LAI/scripts/backfill_session_messages.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

DB = Path("/data/projects/lai/LAI/processed/sessions.db")


def render_v2_analysis_md(analysis: dict, filename: str | None) -> str:
    """V2 ContractAnalysis dict → markdown, matching the UI's renderer.

    V2 issues use int severity 1-5; we map to the V1 low/medium/high
    label the UI shows.
    """
    def sev_label(sev) -> str:
        if isinstance(sev, int):
            return "LOW" if sev <= 2 else "MEDIUM" if sev == 3 else "HIGH"
        return str(sev).upper()

    lines: list[str] = []
    lines.append(f"**Contract analysis** — {filename or '(uploaded document)'}")
    n_clauses = len(analysis.get("clauses") or [])
    lines.append(f"Detected {n_clauses} clauses (re-rendered from saved analysis)")
    lines.append("")

    missing = analysis.get("missing_required_clauses") or []
    if missing:
        lines.append("### ❌ Missing required clauses")
        for m in missing:
            sev = sev_label(m.get("severity"))
            title = m.get("title") or m.get("type") or ""
            desc = m.get("description") or m.get("rationale") or ""
            lines.append(f"- **[{sev}] {title}** — {desc}")
        lines.append("")

    flagged = [c for c in (analysis.get("clauses") or []) if c.get("issues")]
    if flagged:
        lines.append("### ⚠️ Flagged clauses")
        for c in flagged:
            lines.append(f"#### {c.get('id', '?')} · {c.get('type', 'Sonstiges')}")
            if c.get("summary"):
                lines.append(f"> {c['summary']}")
            for i in c.get("issues") or []:
                sev = sev_label(i.get("severity"))
                desc = i.get("description") or i.get("title") or ""
                rec = i.get("suggested_redline") or i.get("recommendation")
                line = f"- **[{sev}]** {desc}"
                if rec:
                    line += f"\n   _Empfehlung: {rec}_"
                lines.append(line)
            if c.get("legal_basis"):
                lines.append(f"- 📎 {', '.join(c['legal_basis'])}")
            lines.append("")
    else:
        lines.append("✅ No issues flagged in any clause.")

    eq = analysis.get("extraction_quality")
    if eq and eq.get("confidence") == "low":
        lines.append("")
        lines.append(
            "⚠️ _Niedrige Extraktionsqualität — diese Befunde könnten "
            "falsch positiv sein. " + (eq.get("reason") or "") + "_"
        )

    return "\n".join(lines)


def main() -> int:
    if not DB.exists():
        print(f"[err] sessions.db not found at {DB}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB), isolation_level=None)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT s.id, s.filename, s.n_pages, s.analysis_json, s.created_at,
               (SELECT COUNT(*) FROM messages WHERE session_id = s.id) AS nm
        FROM sessions s
        ORDER BY created_at ASC
        """
    ).fetchall()

    inserted = 0
    skipped = 0
    for r in rows:
        sid = r["id"]
        if r["nm"] > 0:
            skipped += 1
            continue
        if not (r["filename"] or r["analysis_json"]):
            skipped += 1
            continue

        ts = r["created_at"] or time.time()
        # Spread synthesized timestamps slightly so order is stable.
        cursor = ts

        # 1. Upload bubbles — if there's a filename
        if r["filename"]:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, mode, created_at) VALUES (?, ?, ?, ?, ?)",
                (sid, "user", f"📎 {r['filename']}", "upload", cursor),
            )
            cursor += 0.001
            confirmation = (
                f"📄 **Document uploaded:** {r['filename']}\n"
                f"- Pages: {r['n_pages'] or 0}\n\n"
                "_(Backfilled — original message bubble was not persisted.)_"
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, mode, created_at) VALUES (?, ?, ?, ?, ?)",
                (sid, "assistant", confirmation, "upload", cursor),
            )
            cursor += 0.001
            inserted += 2

        # 2. Analyze bubbles — if there's a stored analysis
        if r["analysis_json"]:
            try:
                analysis = json.loads(r["analysis_json"])
            except json.JSONDecodeError:
                analysis = None
            if analysis:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, mode, created_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, "user", "analyze contract", "analyze", cursor),
                )
                cursor += 0.001
                rendered = render_v2_analysis_md(analysis, r["filename"])
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, mode, created_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, "assistant", rendered, "analyze", cursor),
                )
                inserted += 2

        print(f"  backfilled {sid[:8]}  filename={r['filename'] or '-'}  has_analysis={bool(r['analysis_json'])}")

    print(f"\nDone — inserted {inserted} messages, skipped {skipped} sessions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
