# Review package — t_61bd3b39 (archived-count on board header + persisted Show archived)

Commit: a9339fb0a5 on branch wt/archived-count (worktree
/Users/ikavt/Developer/worktrees/hermes-agent/wt-archived-count), base
63279301bc = the reviewed surface from verdict t_b502fe91.

## 1. What was built

Display-only visibility fix for drained boards, per the card's four-point FIX
spec. Backend: `GET /board` now reports `archived_count` (single COUNT, same
tenant filter as list_tasks) even when the archived lane is filtered out.
Desktop: the board header renders an "N archived" chip beside the live total
when N>0 and the lane is hidden; clicking the chip flips Show archived on.
The Show archived toggle moved from `useState(false)` to a persisted
`$showArchived` atom (plugin storage, Intro-dismissal precedent) and is
seeded ONCE from `dashboard.kanban.include_archived_by_default` on first run
(no stored choice yet); a stored choice always outranks the knob afterwards.
i18n: `chipArchived` added to en/ja/zh/zh-Hant. No archived rows, statuses,
or child data are touched by any code path.

## 2. Where it lives

- plugins/kanban/dashboard/plugin_api.py — archived_count in get_board payload
- apps/desktop/src/plugins/kanban/api.ts — $showArchived atom + storage + /config seed
- apps/desktop/src/plugins/kanban/board.tsx — chip in header, toggle via store
- apps/desktop/src/plugins/kanban/i18n.ts — chipArchived x4 locales
- apps/desktop/src/plugins/kanban/types.ts — archived_count on KanbanBoard
- tests/plugins/test_kanban_dashboard_plugin.py — 3 backend cases
- apps/desktop/src/plugins/kanban/archived-count.test.tsx — 6 UI cases
- archive-fingerprint.py (worktree root) — integrity drill, see §5

## 3. How to verify (critic: re-run these)

Backend (from the worktree; unset the env pin first — it outranks everything):

  cd /Users/ikavt/Developer/worktrees/hermes-agent/wt-archived-count/tests
  HERMES_KANBAN_DB= /opt/anaconda3/bin/python3 -m pytest \
    plugins/test_kanban_dashboard_plugin.py -k archived_count -q
  EXPECT: 3 passed

  HERMES_KANBAN_DB= /opt/anaconda3/bin/python3 -m pytest plugins/ hermes_cli/ -q \
    --deselect plugins/memory/test_hindsight_provider.py::TestConfig::test_get_client_passes_idle_timeout_to_hindsight_embedded
  EXPECT: all passed (the deselected test fails on PRISTINE MAIN too —
  proven pre-existing: hindsight-client not installed in /opt/anaconda3,
  error "lazy installs disabled (security.allow_lazy_installs=false)").

Frontend (toolchain is installed at the MAIN checkout, not the worktree;
the worktree's apps/desktop/node_modules is a sanctioned merged-symlink dir
created by apps/desktop/scripts/merge-node-modules.sh — untracked scaffold):

  cd /Users/ikavt/Developer/worktrees/hermes-agent/wt-archived-count/apps/desktop
  /Users/ikavt/.hermes/hermes-agent/node_modules/.bin/vitest run --project ui \
    src/plugins/kanban/archived-count.test.tsx
  EXPECT: 6 passed, 0 unhandled errors

  /Users/ikavt/.hermes/hermes-agent/node_modules/.bin/tsc -p . --noEmit
  EXPECT: only 2 pre-existing errors in ../shared/src/*.test.ts (vitest
  resolution inside ../shared — byte-identical on the main checkout).

Integrity (data untouched):

  /opt/anaconda3/bin/python3 archive-fingerprint.py
  EXPECT total_archived: 90, hashes identical to the baseline recorded in
  the completion comment (default 18 / d2-pass2 20 / s1-research 18 /
  s1plus 34). 90 vs the verdict's 89 = +1 card on the live default board
  since the snapshot (fleet kept operating); the three named boards'
  hashes match byte-for-byte from before my first commit.

## 4. The bar (from the card, quoted)

- "On a fully drained board (e.g. d2-pass2: 20 archived, 0 visible), the
  operator sees an archived count without touching any menu." ->
  covered: backend payload carries archived_count on d2-pass2 (test +
  live query), header chip renders whenever archived_count>0 and lane
  hidden (UI test).
- "Toggle state survives app restart." -> covered: $showArchived persisted
  via plugin storage; UI test rebinds (fresh plugin-load simulation) and
  asserts the stored value wins over the import default.
- "No change to archived card rows, statuses, or child data (89 cards …
  byte-intact — critic re-checks)." -> covered by §5 fingerprint recipe;
  no write path touched anywhere in the diff.
- "Tests: backend payload includes archived_count on both flag states; UI
  chip renders when count>0." -> covered: test_board_archived_count_
  counts_archived_without_lane asserts both flag states; chip render test.
- Rebuild note: ikavt runs the PACKAGED build — fix is invisible until the
  desktop app is rebuilt (release/mac-arm64, app.asar.unpacked/dist) and he
  relaunches. Plugin backend (plugin_api.py) is served from the checkout.

## 5. Known weaknesses (disclosed)

1. eslint/prettier were NOT run — the worktree has no installed toolchain
   and I refused to run installs (config-level gate). Formatting risk is
   real (long className strings); typecheck IS clean for all touched files.
2. The 90-vs-89 count delta is a LIVE-BOARD delta, not data drift: default
   board archived 17 -> 18 between the critic's snapshot (2026-09-04 early)
   and my run, while d2-pass2/s1-research/s1plus are hash-identical. If the
   critic wants a same-content comparison rather than count equality, the
   fingerprint script provides it (re-run + compare per-board hashes).
3. include_archived_by_default seeding is once-per-install by design (spec
   said "initial value"); after the first explicit toggle the knob is
   ignored. Anyone expecting the knob to re-assert on every boot would
   call this a behavior gap; I assert it is the correct reading and wrote
   the rationale inline in api.ts.
4. The seed path races app shutdown (fetch resolves after plugin unload);
   guarded with a disposed flag (write-after-unload protection), but the
   seed could still be missed if the fetch lands after unload — cosmetic,
   next launch re-seeds (storage still empty).
5. Chip click-through ships without a hover Tip (spec marked Tip "maybe");
   aria-label + title are present. Dashboard web surface untouched — chip
   is desktop-only this round (spec's FIX items named the desktop board
   header; the dashboard web app already renders archived when flagged).
6. merge-node-modules.sh (untracked, apps/desktop/scripts/) is session
   scaffolding for running vitest in a worktree, NOT part of the fix. It
   symlinks ~780 entries from the main checkout's two installs; anyone
   running `npm install` in the worktree would be better served deleting
   it first. Left on disk intentionally so the critic can re-run UI tests.
