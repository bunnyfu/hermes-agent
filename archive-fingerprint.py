#!/usr/bin/env python3
"""Integrity drill for t_61bd3b39: hash the archived rows of every board DB.

Proves the display-only claim — the archived_count feature touches no task
rows. Hash is computed over the archived tasks' full row content + count, so
any mutation of archived data (status, body, child links, ids) shifts it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

BOARDS_ROOT = Path.home() / ".hermes" / "kanban" / "boards"
BOARDS = ["default", "d2-pass2", "s1-research", "s1plus"]


def archive_fingerprint(db_path: Path) -> dict:
    """SHA-256 over archived rows' full content, read-only (WAL-safe URI)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'archived' ORDER BY id"
        ).fetchall()
        h = hashlib.sha256()
        for r in rows:
            h.update(repr(dict(r)).encode())
        return {
            "board": db_path.parent.name,
            "archived_rows": len(rows),
            "sha256_archived_rows": h.hexdigest(),
        }
    finally:
        conn.close()


def main() -> None:
    out = []
    total = 0
    # The default board's DB is the back-compat path <kanban_home>/kanban.db
    # (hermes_cli/kanban_db.py kanban_db_path) — NOT boards/default/. Note
    # HERMES_KANBAN_DB, when set, pins every lookup at exactly this file.
    for slug, db in [
        *[(s, BOARDS_ROOT / s / "kanban.db") for s in BOARDS[1:]],
        ("default", Path.home() / ".hermes" / "kanban.db"),
    ]:
        if not db.exists():
            out.append({"board": slug, "error": f"db missing: {db}"})
            continue
        fp = archive_fingerprint(db)
        fp["board"] = slug  # not the parent dir name (default's parent = home)
        total += fp.get("archived_rows", 0)
        out.append(fp)
    print(json.dumps({"boards": out, "total_archived": total}, indent=1))


if __name__ == "__main__":
    main()
