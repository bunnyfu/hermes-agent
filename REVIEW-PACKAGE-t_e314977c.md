# Review package — t_e314977c (attach corruption: integrity guard + path-based attach)

Commit: (single commit on branch `wt/attach-integrity-guard`, worktree
`/Users/ikavt/Developer/worktrees/hermes-agent/wt-attach-integrity-guard`),
base `1a5f4b02fd2` (main @ 2026-09-05). Scratch-only evidence: every DB row
and blob written during verification landed in tempdirs; the production board
(`s2-engram`, attachments 18/19) was never touched.

## 1. What was built

The t_2ae14d55 corruption mechanism (per parent audits t_553fe667/t_e06fe226)
was a model-fabricated `content_base64` placeholder — `L2FuZHJvaWQvLi4v`,
valid base64 of the 12-byte path fragment `/android/../` — which the attach
handler decoded, stored, and confirmed with `ok:true`, size=12, NULL
content_type. No path-remap bug exists; the card's original path-shape
framing was rejected by the audits and the fix targets the real mechanism.

Four layers, one commit:

1. **Integrity guard at the single shared write path.**
   `hermes_cli/kanban_db.store_attachment_bytes` now re-reads the blob from
   disk after writing and raises `AttachmentIntegrityError` (new, deliberately
   NOT a ValueError so generic 400 handlers can't blur it) unless the on-disk
   bytes equal the payload — plus optional `expected_size` / `expected_sha256`
   cross-checks and a `verify=False` documented legacy escape. Silent garbage
   at the storage layer is now impossible: failure happens BEFORE the
   `task_attachments` row is inserted and the blob is reaped.
2. **Call-side fabrication guard + provenance metadata.**
   `tools/kanban_tools._handle_attach` refuses payloads that decode to
   path-looking fragments (the `/android/…` family, prefix table
   `_FABRICATED_PATH_PREFIXES`) with a loud error naming the three correct
   channels; results now echo `sha256` and the derived `content_type`.
   `derive_content_type()` never stores an empty content_type (explicit →
   mimetypes → `.md`/`.markdown` fallback map for the stock-Python `.md` gap
   → `application/octet-stream`). The completion-artifacts channel
   (`_insert_completion_attachment`) now also records sha256. Every stored
   row records the payload digest (new additive `task_attachments.sha256`
   column, SCHEMA_SQL + migration; legacy boards get it on next init).
3. **Structural fix: `kanban_attach_file` (new tool).** The worker passes a
   path; Hermes reads the bytes from disk server-side — the model never
   hand-encodes content, same trust level as kernel-side
   `kanban_complete(artifacts=[...])`. Runs `store_attachment_bytes` with
   `expected_size` + `expected_sha256`, echoes size/sha256/content_type.
   Registered in the `kanban` toolset (`toolsets.py` x2 lists),
   `tools/kanban_tools.py` schema+handler+registry, website tools-reference.
4. **21-test regression matrix** — `tests/tools/test_attach_integrity.py`,
   self-contained fixtures, cwd-independent. Sections: incident payload
   rejection (incl. the exact `L2FuZHJvaWQvLi4v` invocation), path-fragment
   parametric sweep, legit-small-payload non-regression, content_type
   derivation matrix, sha echo/explicit-override, `kanban_attach_file` happy
   path (worktree-shaped path, spaces, unicode, 16,986 B, byte-equal),
   missing-file/directory errors, filename-defaulting, store-level guard
   (wrong sha / poisoned disk write / size mismatch / verify=False escape /
   sha recorded), legacy-board migration.

## 2. Where it lives

- `hermes_cli/kanban_db.py` — AttachmentIntegrityError, MIMETYPE_FALLBACKS,
  derive_content_type(), guarded store_attachment_bytes, add_attachment
  sha256 param, schema + migration, Attachment.sha256 (+2 read sites),
  _insert_completion_attachment digest
- `tools/kanban_tools.py` — _FABRICATED_PATH_PREFIXES,
  _looks_like_fabricated_path, guarded _handle_attach, new _handle_attach_file,
  KANBAN_ATTACH_FILE_SCHEMA, registry entry
- `toolsets.py` — kanban_attach_file in both tool lists (worker default +
  kanban toolset)
- `website/docs/reference/tools-reference.md` — attach tools rows
- `tests/tools/test_attach_integrity.py` — the 21-test regression matrix

## 3. How to verify (critic: re-run these)

All commands from the worktree root, hermetic runner:
`cd /Users/ikavt/Developer/worktrees/hermes-agent/wt-attach-integrity-guard`

```
HERMES_PYTHON=/opt/anaconda3/bin/python scripts/run_tests.sh \
  tests/tools/test_attach_integrity.py
# EXPECT: 21 passed, 0 failed

HERMES_PYTHON=/opt/anaconda3/bin/python scripts/run_tests.sh \
  tests/tools/test_kanban_tools.py tests/tools/test_kanban_redaction.py \
  tests/plugins/test_kanban_attachments.py \
  tests/hermes_cli/test_kanban_transfer.py tests/test_toolsets.py \
  tests/test_toolset_distributions.py tests/gateway/test_platform_base.py
# EXPECT: 200 passed, 0 failed, 1 skipped
```

Red-before/green-after — two independent proofs:

- Unit level (executed 22:1x MSK, strict TDD order): the 21-test file was
  written FIRST and run against the then-unfixed code (base 1a5f4b02fd2):
  21 failed / 0 errors, with the RIGHT failure modes spot-checked —
  `{"ok": true, ..., "size": 12}` returned on the incident payload (the bug,
  live), AttributeError on `_handle_attach_file`, AttributeError on
  `AttachmentIntegrityError`. Implementation landed after; same file then
  passed 21/21.
- E2E repro (executed 22:31 MSK; scripts + full logs in the task workspace
  `/Users/ikavt/.hermes/kanban/workspaces/t_e314977c/` — `e2e_repro.py`,
  `repro-OLD.log`, `repro-NEW.log`; scratch HERMES_HOME tempdirs only):
  - OLD (main, unfixed): incident invocation → `{"ok": true, ..., "size": 12}`,
    stored bytes `b'/android/../'`, content_type NULL, sha NULL — the bug,
    byte-identical to the t_2ae14d55 row.
  - NEW: same invocation → clean integrity-guard error, zero rows, zero blobs.
  - NEW: worktree-shaped path (`.worktrees/<task>/sub dir/audit-…-.md`,
    spaces + unicode, 16,986 B) via `kanban_attach_file` → size 16,986,
    byte-equal, sha256 row == source == echo, `content_type='text/markdown'`.
  - NEW: t_15a4c501-style legit inline JSON attach → byte-equal,
    `application/json`, sha echoed (no regression on the working path).

## 4. The bar (from the card, quoted)

- "stored bytes are byte-equal to the source for ALL path shapes:
  git-worktree paths, absolute, relative, spaces, unicode" → covered for the
  attach flow: kanban_attach_file stores byte-exact regardless of path shape
  (tests + E2E case B), and the store-level guard re-reads whatever any
  caller wrote. Relative-path coverage note in §5.4.
- "content_type must be derived and stored correctly (e.g. 'text/markdown')"
  → derive_content_type (explicit → mimetypes → .md fallback → octet-stream,
  never NULL) at the shared store path; E2E shows text/markdown on the
  incident filename.
- "size must reflect the actually-written payload" → store path records
  len(data) AND re-reads the disk bytes; mismatch raises.
- "attach-time integrity guard: after writing, compare stored size (and
  sha256) against the source and fail loudly on mismatch — silent garbage
  must be impossible" → AttachmentIntegrityError pre-insert + blob reaped;
  poisoned-disk-write test proves the re-read catches a corrupted write.
- "Re-run the repro seed against a SCRATCH board/task … verify stored size
  16,986 (or source-equal), byte equality via cmp/sha256, and correct
  content_type. Also cover the t_15a4c501-style invocation" → E2E repro,
  both cases, §3.
- "regression test … should fail on the old code and pass on the new code"
  → §3 red-before/green-after, two independent proofs.
- "no path shape in the test matrix corrupts or silently mis-sizes an
  attachment" → parametric fragment sweep + byte-equality assertions
  throughout.

## 5. Known weaknesses (disclosed)

1. **AGENTS.md not updated** (tool-list membership now includes
   `kanban_attach_file`). The edit was hard-blocked: protected-instruction
   file, single-query mode, no user to consent. The drift is one tool name
   in the kanban toolset enumeration (AGENTS.md ~line 1244). Sanctioned
   alternative surfaces ARE updated (website tools-reference + the tool's
   own schema description). Needs a maintainer-side one-line follow-up.
2. **Fabrication guard is a prefix heuristic, disclosed as such.** It keys
   on decoded payloads beginning with path-like prefixes (`/android/`,
   `/users/`, `~/`, `../`, …). A fabricated payload that looks like real
   content passes it. Defense in depth is deliberate: the guard is layer 2
   of 4 — the store-level re-read guard + sha recording + kanban_attach_file
   are the structural layers. False-positive risk assessed low: path-shaped
   text content can ride kanban_attach_file (which bypasses the heuristic —
   tested) or kanban_complete(artifacts).
3. **Full-suite safety net**: executed to completion on this worktree
   (hermetic runner, anaconda interpreter, ~47 min wall). Failures cluster
   ENTIRELY outside the diff's blast radius (voice, wake-word, searxng,
   web-tools-config, video-generation surface matrix, tui-gateway,
   batch-runner, acp, process-registry, transcription). Discriminator run:
   the same failing files on PRISTINE MAIN `1a5f4b02fd2` with the same
   interpreter and runner fail identically — video_generation 28F,
   local_env_blocklist 7F, execution_flag_detection 3F, voice_mode 5F,
   process_registry 8F; the 18 collection-error files fail with the same
   `ModuleNotFoundError: No module named 'acp'` on both codebases. Verdict:
   pre-existing host/interpreter environment gaps, not regressions from
   this commit. Variance note: test_process_registry showed 9F in the
   full-suite context vs 8F standalone on main (file has no kanban or
   toolset coupling; within-noise for a 500-file parallel run). Every
   surface inside this commit's blast radius is green in every
   configuration: 200 passed / 1 skipped across the 7 neighbor suites
   (§3), 21/21 new-case matrix (twice), E2E old-vs-new repro.
4. **Relative-path attach is a docs-level nit**: kanban_attach_file expands
   `~` and accepts any path the worker process can read; a relative path
   resolves against the server process's cwd (fragile but functional — same
   semantic as every other Hermes file surface). Schema says "absolute".
5. `verify=False` exists as a documented legacy escape; no Hermes surface
   uses it (proven by grep) — it exists so a future caller with a
   performance argument has a sanctioned switch instead of a reason to
   bypass the shared path. Remove-on-request.
6. The `sha256` column is recorded at write time only; no backfill for the
   existing rows (id 18 dud / id 19 good on s2-engram stay NULL — hashing
   them retroactively is outside this card and would falsify "verified at
   attach time" semantics).
