# REVIEW PACKAGE / EVIDENCE BATTERY — t_56e6e167 (2026-09-30, forge)

Server-side hash+size checks on the kanban_attach path: replay of the
t_96fffbef integrity layer (39c734edaa) onto current main + declared-content
guard + real-incident regression suite.

## Chain (local branch wt/attach-guard-t56e6e167; fork-pushed before complete)

| commit | what |
|---|---|
| 99721dca80 | main base (Sep 30) |
| 3d9f72717c | merge 39c734edaa (fleet-pin/attach-replay-t96) — sha256 column+migration, store_attachment_bytes guard+byte-identity self-check, kanban_attach_file, sha256 echo, completion-stager hashing, dashboard digest. One conflict (test imports, union-resolved). Zero production conflicts. |
| 2bda5c009b | delta: declared expected_size/expected_sha256 on inline kanban_attach (schema + handler + docs), connect/connect_closing re-export drift repair, tests/tools/test_attach_declared_size.py |
| b0619e7fc2 | contract-boundary leg (undeclared corrupt attach stores but records the true digest) + this ops record |

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

- POSITIVE (branch tip): tests/tools/test_attach_declared_size.py → **17 passed**;
  together with the replay fix's own suite tests/tools/test_attach_integrity.py
  → 37 passed in one run (pre-delta-split file set).
- TEETH (negative controls) — the FINAL test file run against pre-delta trees in
  detached throwaway worktrees (/private/tmp/t56-base-merge @ 3d9f72717c,
  /private/tmp/t56-base-main @ 99721dca80; copies patched ONLY in the two
  kb.connect() helper sites to route through kanban_db_connect.connect, which
  is what those trees used — the re-export under test is delta work):
  - merge-base 3d9f72717c: **7 failed, 10 passed** — all three incident legs
    FAIL ("unknown parameter(s): expected_size" — the knobs did not exist; the
    undeclared incident call lands silently), honest/boundary/digest/board-shape
    legs FAIL. Passing: the 2 constants-only fixture legs + the malformed-
    declaration legs (post-replay strict arg validation already rejected
    unknown params — vacuous teeth THERE, real on main, see next).
  - pristine main 99721dca80: **15 failed, 2 passed** — only the two
    constants-only fixture legs survive. Additionally vs merge-base: the
    malformed-declaration legs FAIL on REAL teeth (old handler had no arg
    validation: bogus expected_size ignored, junk stored ok:true) and the
    undeclared-corrupt-digest leg FAILS (row carried no sha256 at all).
- MIGRATION: options-worker-shape leg (board DB with task_attachments lacking
  the sha256 column entirely) — column added on connect, digest recorded,
  guard fires: green on branch, fails on bases (mechanism absent there).
- FULL SUITE (card step 4): tests/tools + tests/hermes_cli + tests/plugins
  (~25.7k tests). Finding first: a SINGLE pytest process over the combined
  packages SEGFAULTS mid-run on BOTH trees (pristine main at 54%, branch at
  58%) — native crash in plugin-loader background imports
  (plugins/platforms/raft → pathlib resolve under tests/home_io_guard);
  host/interpreter-level instability (anaconda py3.12), unrelated to this
  delta and present on pristine main. Verdict therefore from CHUNKED
  per-package runs (4/2/1 chunks, per-chunk pytest processes): 0 chunk
  crashes on either tree; branch 1145 vs base 1108 failed ids under
  deliberately DUAL CONCURRENT load. Adjudication (pitfall: combined-run
  failures are not auto-regressions): all 44 branch-only failures PASS
  isolated on the branch (44/44 in 62s — contention flakes); of the 7
  base-only failures, 6 pass isolated on base and 1 is a timing flake that
  passes on rerun on BOTH trees; 2 kanban-named failures persist isolated
  but fail IDENTICALLY on pristine main (pre-existing local-env: spawn
  guard + board-pin env on this host). Net regressions attributable to the
  branch: ZERO. No kanban/attach-surface test fails on the branch beyond
  what pristine main fails. Three collection errors are pre-existing env
  misses (fal_client/telegram deps absent locally), identical on both trees.
- tests/tools/test_attach_declared_size.py: 17/17 on the branch; teeth
  matrix above.

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
python3 -m pytest tests/tools/test_attach_declared_size.py -q
# expect: 17 passed
git log --oneline main..HEAD           # b0619e7fc2, 2bda5c009b, 3d9f72717c
git ls-remote --heads fork wt/attach-guard-t56e6e167   # tip == local HEAD
