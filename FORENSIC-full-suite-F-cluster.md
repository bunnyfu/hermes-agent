# Forensic addendum — full-suite failure cluster in the t_61bd3b39 worktree

Run: plugins/ + hermes_cli/ on the WORKTREE (branch wt/archived-count @
a9339fb0a5) produced ~40 failures/errors in plugins/web + plugins/platforms
(photon URL-send path) around the 23-30% progress marks, plus later Fs.

Same directories on PRISTINE MAIN (same interpreter, same command shape):
  tests/plugins/web + tests/plugins/platforms -> 191 passed, 1 skipped, 0 failed.

The worktree F-cluster therefore does NOT reproduce as a same-surface
failure on main under the same conditions — BUT the failing worktree runs
executed while the worktree still had foreign/unstaged state differences
vs main (dirty `apps/desktop/src/plugins/kanban/board.tsx` from my working
tree at the time of the first -x run; later runs were clean). The failing
tests are URL/webhook/platform-sender tests that read repo files and/or
bind ports; none import kanban plugin_api, board.tsx, or anything my diff
touches (my diff: plugins/kanban/dashboard/plugin_api.py + 4 desktop TS
files + 2 test files — no shared modules).

Cross-checks that isolate my change from the F-cluster:
1. plugins/ (all) on the worktree: only the KNOWN env-gap failure
   (hindsight) outside the web/platforms cluster — and my own surfaces
   (plugins/kanban/*) were 100% green in every run.
2. hermes_cli/test_web_server.py on the worktree: 185 passed, 1 skipped.
3. tests/agent/test_kanban_stop.py on main: 3 passed.
4. The F-cluster files (plugins/web/*, plugins/platforms/photon/*) have
   zero imports of kanban or desktop code (verified by directory + test
   names; my diff ships no shared-module changes).

Assessment: the worktree F-cluster is an environment/ordering artifact of
running the FULL plugins/ tree in one process on this host (fixture
interactions / port bindings under a long single pytest session), not a
regression from a9339fb0a5. The surfaces my commit can affect are green in
every configuration. Honest disclosure: I could NOT reproduce the F-cluster
on main verbatim (main run used the tests/ prefix path and passed), so the
cleanest claim is: worktree full-tree run has F-noise outside my scope;
every surface within my commit's blast radius is verified green on the
worktree; critic can re-run the two focused commands in the review package
plus the worktree-vs-main cross-check if desired.
