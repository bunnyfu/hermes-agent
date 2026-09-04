#!/usr/bin/env python3
"""t_61bd3b39 run-37: brute-force the 13:41 baseline set membership.

Try every 2-card exclusion from today's 21 archived rows; report which
pair (if any) reproduces the zombie commit's recorded 19-row hash. Also
probe 1-card and 0-card exclusions for completeness.
"""
import hashlib
import itertools
import sqlite3
from pathlib import Path

db = Path.home() / ".hermes" / "kanban.db"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(
    "SELECT * FROM tasks WHERE status='archived' ORDER BY id").fetchall()]
conn.close()

TARGET = "d33e4a6fe3a374ac1149b8bd2cf9af34a674e9bbf373f0dc0d6e22641ea82bf1"


def set_sha(subset):
    h = hashlib.sha256()
    for r in subset:
        h.update(repr(r).encode())
    return h.hexdigest()


n = len(rows)
print(f"today's archived rows: {n}")

hits = []
# 0-card exclusion (hash over all 21 — sanity check)
if set_sha(rows) == TARGET:
    hits.append(("exclude-0", []))
# 1-card exclusions
for i in range(n):
    if set_sha([r for j, r in enumerate(rows) if j != i]) == TARGET:
        hits.append(("exclude-1", [rows[i]["id"]]))
# 2-card exclusions
for i, j in itertools.combinations(range(n), 2):
    sub = [r for k, r in enumerate(rows) if k not in (i, j)]
    if set_sha(sub) == TARGET:
        hits.append(("exclude-2", [rows[i]["id"], rows[j]["id"]]))

if hits:
    for kind, ids in hits:
        print(f"MATCH [{kind}]: 13:41 baseline = today's set minus {ids}")
    print("APPEND-ONLY GROWTH PROVEN — no archived row mutated.")
else:
    print("NO pair/single/zero exclusion reproduces the 13:41 hash.")
    print("=> archived-row CONTENT changed between 13:41 and now. STOP.")
