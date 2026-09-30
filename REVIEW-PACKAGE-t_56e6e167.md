# REVIEW PACKAGE / EVIDENCE BATTERY — t_56e6e167 (2026-09-30, forge)

Server-side hash+size checks on the kanban_attach path: replay of the
t_96fffbef integrity layer (39c734edaa) onto current main + declared-content
guard + real-incident regression suite.

## Chain (local branch wt/attach-guard-t56e6e167; fork-pushed before complete)

| commit | what |
|---|---|
| 99721dca80 | main base (Sep 30) |
| 3d9f72717c | merge 39c734edaa (fleet-pin/attach-replay-t96) — sha256 column+migration, store_attachment_bytes guard+byte-identity self-check, kanban_attach_file, sha256 echo, completion-stager hashing, dashboard digest. One conflict (test imports, union-resolved). Zero production conflicts. |
| 2bda5c009b | delta: declared expected_size/expected_sha256 on inline kanban_attach (schema + handler + docs), connect/connect_closing re-export drift repair, tests/tools/test_attach_declared_size.py (18 tests) |
| (this commit) | contract-boundary leg + this ops record |

## Incident binding

The fixture IS the t_a45e7dcd attachment-38 event (2026-09-30 07:22 MSK):
junk constant decodes to the exact 380-byte blob recovered from the emitting
session's model-emitted args (sha256 c1c0de0821fac1cf9d28ced73d517002aa74d571c87319837449c059e23f69d3,
"Real Content Size : 57226 Bytes" x2); clean original rebuilt from a
gzip-embedded copy verified to sha256 7a9fb2a2863d961362210b79d05b514410f4edae969e62557bda60ff7fa6f8d3
/ 57,226 B. Both constants were embedded MECHANICALLY (scripts in the kanban
workspace) and roundtrip-verified; the first hand-typed JUNK_B64 failed that
check (one wrong char, sha 95a312a6…) and was replaced — the failure mode the
guard exists for, caught by the same discipline.

## Battery receipts

- POSITIVE (branch tip): tests/tools/test_attach_declared_size.py → 18 passed;
  tests/tools/test_attach_integrity.py (replay fix's own suite) → 19 passed
  (with the new file: 37 passed in one run).
- TEETH (negative controls) — the new test file run against pre-delta trees in
  detached throwaway worktrees (/private/tmp/t56-base-merge @ 3d9f72717c,
  /private/tmp/t56-base-main @ 99721dca80; copies patched ONLY in the two
  kb.connect() helper sites to route through kanban_db_connect.connect, which
  is what those trees used — the re-export under test is delta work):
  - merge-base: 7 failed / 11 passed — all three incident legs FAIL
    ("unknown parameter(s): expected_size" — the knobs did not exist; the
    undeclared incident call lands silently); honest+board-shape+digest legs
    FAIL; malformed-declaration legs PASS vacuously (strict arg validation
    already rejected unknown params post-replay).
  - pristine main: 10 failed / 2 passed — additionally the malformed
    declaration legs FAIL on REAL teeth (old handler had no arg validation:
    bogus expected_size ignored, junk stored ok:true) and the
    undeclared-corrupt-digest leg FAILS (row carried no sha256 at all).
  - Fixture-integrity legs (the only 2) PASS on all trees — they test
    constants, not code, by design.
- MIGRATION: options-worker-shape leg (board DB with task_attachments lacking
  the sha256 column entirely) — column added on connect, digest recorded,
  guard fires: green on branch, fails on bases (mechanism absent there).
- FULL SUITE (card step 4): tests/tools + tests/hermes_cli + tests/plugins
  (the touched packages), ~25.7k tests. Three PRE-EXISTING collection errors
  (fal_client / telegram deps missing in the local env — identical on
  pristine main, excluded by base-parity). Result recorded in the completion
  report; claim backed by the run log, not asserted.

## Known weaknesses (disclosed)

1. The guard is contract-based: an UNDECLARED corrupt attach still stores
   (nothing server-side to compare against; the payload's self-describing
   header is never parsed — deliberate). It now records the true digest, so
   post-hoc audits catch what was previously invisible. The structural kill
   for the family remains kanban_attach_file / kanban_complete(artifacts=…)
   (bytes never pass through the model) — both live again with this replay.
2. Workers only benefit once they pass the knobs; the schema description and
   the doc line push the habit ("declare expected_size whenever the true size
   is known"). A fleet-convention note for the profiles repo is follow-up
   work (planner/ikavt surface, not this card).
3. Deployment gap (outside this card): the running gateway executes the
   shared checkout @ main and needs a restart AFTER this lands there — the
   fix executes nowhere until then (t_bbc1a4b8's F1 note). No restart was
   performed by this card.
4. kanban_attach_url computes no digest of the downloaded bytes on the merged
   code (echo only on file/inline paths + completion stagers). Tracked as a
   residual gap for a follow-up card if wanted.

## Verify recipe

cd /Users/ikavt/Developer/worktrees/hermes-agent/wt-t56e6e167
python3 -m pytest tests/tools/test_attach_declared_size.py tests/tools/test_attach_integrity.py -q
# expect: 37 passed
git log --oneline main..HEAD           # 3d9f72717c, 2bda5c009b, +ops record
git ls-remote --heads fork wt/attach-guard-t56e6e167   # tip == local HEAD
