"""Park-vs-loop guard semantics: supervisor E3 re-parks are not loops.

Regression coverage for the unblock-loop breaker fix (2026-09-19 incident on
card t_a2f50a6c): the old guard counted same-KIND re-blocks only, so a
supervisor re-parking an E3 card after a legitimate unblock + productive work
tripped ``block_loop_detected`` (recurrences 2 -> 3) and the CLI refused the
park outright (``cannot block`` exit 1) because the triage UPDATE gate had no
re-assert door.

Shipped semantics (all on temp-board fixtures, never the live board):

* dispatcher claim-loops with an identical reason still trip at
  ``BLOCK_RECURRENCE_LIMIT`` and demote to ``triage`` (guard's original purpose);
* a changed block reason resets the counter (new cause, not a loop);
* a marker-bearing nexus/supervisor comment newer than the last block event
  resets the counter (a supervisor episode completed in between);
* ``needs_input`` blocks authored by a non-worker surface never enter the
  counter at all (supervisor parks are deliberate, not claim-loops);
* ``kanban block`` on a triage card whose ``block_kind`` is already set
  re-asserts the park idempotently (exit 0, no counter increment) —
  including legacy rows carrying a stale counter from the pre-fix era.

Surface simulation: a dispatcher-spawned worker carries
``HERMES_KANBAN_TASK`` in its environment; a supervisor/CLI surface does not.
The fixtures toggle that variable per call.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Iterator

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

E3_REASON_A = "E3 waiting-ikavt: [E3 MEMO] decision A/B needed"
E3_REASON_B = "E3 waiting-ikavt: [E3 MEMO] fresh decision needed"


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@contextlib.contextmanager
def surface(worker: bool) -> Iterator[None]:
    """Simulate the blocking surface via the worker env marker."""
    if worker:
        monkey_marker = "t_surface_sim"
        os.environ["HERMES_KANBAN_TASK"] = monkey_marker
    else:
        os.environ.pop("HERMES_KANBAN_TASK", None)
    try:
        yield
    finally:
        os.environ.pop("HERMES_KANBAN_TASK", None)


def _block(conn, tid: str, reason: str, kind: str | None, *, worker: bool = True) -> bool:
    with surface(worker):
        return kb.block_task(conn, tid, reason=reason, kind=kind)


def _running_task(conn, title: str) -> str:
    tid = kb.create_task(conn, title=title, assignee="worker")
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    claimed = kb.claim_task(conn, tid, claimer="worker")
    assert claimed is not None
    return tid


def _make_running_again(conn, tid: str) -> None:
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    assert kb.claim_task(conn, tid, claimer="worker") is not None


def _triple(conn, tid: str) -> tuple[str, str | None, int]:
    row = conn.execute(
        "SELECT status, block_kind, block_recurrences FROM tasks WHERE id=?", (tid,)
    ).fetchone()
    return row[0], row[1], row[2]


def _tripped(conn, tid: str) -> bool:
    events = [e for e in kb.list_events(conn, tid) if e.kind == "block_loop_detected"]
    return _triple(conn, tid)[0] == "triage" and bool(events)


def _backdate_last_block_event(conn, tid: str, seconds: int = 10) -> None:
    """Fixture helper: make the newest block event strictly older than the next
    comment (same-second created_at granularity would race in a fast suite)."""
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE task_events SET created_at = created_at - ? "
            "WHERE id = (SELECT id FROM task_events WHERE task_id = ? AND kind IN "
            "('blocked', 'block_loop_detected') ORDER BY created_at DESC, id DESC LIMIT 1)",
            (seconds, tid),
        )


def _backdate_last_comment(conn, tid: str, seconds: int = 10) -> None:
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE task_comments SET created_at = created_at - ? "
            "WHERE id = (SELECT id FROM task_comments WHERE task_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1)",
            (seconds, tid),
        )


# ---------------------------------------------------------------------------
# (1) The guard's original purpose: dispatcher claim-loop still trips
# ---------------------------------------------------------------------------


def test_dispatcher_identical_reason_loop_still_trips(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "claim loop")
        _block(conn, tid, "claim failed", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "claim failed", "needs_input", worker=True)
        assert _tripped(conn, tid)
        assert _triple(conn, tid) == ("triage", "needs_input", 2)
        events = [e for e in kb.list_events(conn, tid) if e.kind == "block_loop_detected"]
        payload = events[-1].payload or {}
        assert payload.get("surface") == "worker"
        assert payload.get("limit") == kb.BLOCK_RECURRENCE_LIMIT


# ---------------------------------------------------------------------------
# (2) A changed block reason resets the counter
# ---------------------------------------------------------------------------


def test_changed_reason_resets_counter(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "reason change")
        _block(conn, tid, "first cause", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "a DIFFERENT cause", "needs_input", worker=True)
        assert _triple(conn, tid) == ("blocked", "needs_input", 1)


def test_identical_reason_without_marker_still_counts(kanban_home: Path) -> None:
    """Without a reset trigger, same-reason worker re-blocks still arm the breaker."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "same reason counts")
        _block(conn, tid, "same", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "same", "needs_input", worker=True)
        assert _tripped(conn, tid)


# ---------------------------------------------------------------------------
# (3) Marker-bearing supervisor comment newer than the last block resets
# ---------------------------------------------------------------------------


def test_fresh_supervisor_marker_resets_counter(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "marker reset")
        _block(conn, tid, "same", "needs_input", worker=True)
        _backdate_last_block_event(conn, tid)
        kb.add_comment(conn, tid, "nexus", "E3 waiting-ikavt: [E3 MEMO] memo")
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "same", "needs_input", worker=True)
        assert _triple(conn, tid) == ("blocked", "needs_input", 1)


def test_stale_supervisor_marker_does_not_reset(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "stale marker")
        kb.add_comment(conn, tid, "nexus", "old [E3 MEMO] memo")
        _backdate_last_comment(conn, tid)
        _block(conn, tid, "same", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "same", "needs_input", worker=True)
        assert _tripped(conn, tid)


# ---------------------------------------------------------------------------
# (4) Non-worker needs_input blocks never enter the counter
# ---------------------------------------------------------------------------


def test_non_worker_needs_input_blocks_are_exempt(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "supervisor parks")
        _block(conn, tid, "sup park one", "needs_input", worker=False)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "sup park two", "needs_input", worker=False)
        assert _triple(conn, tid) == ("blocked", "needs_input", 0)


def test_non_worker_capability_blocks_still_count(kanban_home: Path) -> None:
    """The exemption is needs_input-scoped; other kinds keep the breaker."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "capability counted")
        _block(conn, tid, "cap one", "capability", worker=False)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "cap one", "capability", worker=False)
        assert _tripped(conn, tid)


def test_worker_surface_transient_loop_still_trips(kanban_home: Path) -> None:
    """``transient`` keeps counting toward the breaker from the worker surface."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "flaky worker")
        _block(conn, tid, "flaky", "transient", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "flaky", "transient", worker=True)
        assert _tripped(conn, tid)


# ---------------------------------------------------------------------------
# (5) Idempotent re-park of an already-typed triage card
# ---------------------------------------------------------------------------


def test_triage_repark_is_idempotent(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "idempotent repark")
        _block(conn, tid, "loop cause", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "loop cause", "needs_input", worker=True)
        assert _triple(conn, tid)[0] == "triage"
        assert _block(conn, tid, "re-assert", "needs_input", worker=False)
        assert _triple(conn, tid) == ("triage", "needs_input", 0)
        events = [e for e in kb.list_events(conn, tid) if e.kind == "blocked"]
        payload = events[-1].payload or {}
        assert payload.get("reassert") is True
        assert payload.get("source_status") == "triage"


def test_triage_repark_refuses_kind_mismatch(kanban_home: Path) -> None:
    """A re-park with a DIFFERENT kind on a typed triage card is still refused —
    re-asserting a capability park as needs_input is a routing decision, not a refresh."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "kind mismatch")
        _block(conn, tid, "loop cause", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "loop cause", "needs_input", worker=True)
        assert _triple(conn, tid)[0] == "triage"
        assert not _block(conn, tid, "different kind", "capability", worker=False)
        assert _triple(conn, tid) == ("triage", "needs_input", 2)


def test_legacy_residue_repark_clamps_counter(kanban_home: Path) -> None:
    """Pre-fix rows (block_recurrences=3 residue from the 2026-09-19 incident)
    re-park cleanly and land the counter at a safe value — no DB surgery needed."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "legacy residue")
        with kb.write_txn(conn):
            conn.execute(
                "UPDATE tasks SET status='triage', block_kind='needs_input', "
                "block_recurrences=3 WHERE id=?",
                (tid,),
            )
        assert _block(conn, tid, E3_REASON_B, "needs_input", worker=False)
        assert _triple(conn, tid) == ("triage", "needs_input", 0)


# ---------------------------------------------------------------------------
# (6) End-to-end: the 2026-09-19 incident shape lands instead of refusing
# ---------------------------------------------------------------------------


def test_incident_e2e_supervisor_repark_lands(kanban_home: Path) -> None:
    """Worker loop trips -> triage; supervisor drops an E3 marker and re-parks.
    Pre-fix this exact sequence hard-errored (`cannot block`, exit 1); post-fix
    the park is re-asserted with the counter reset."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "incident e2e")
        _block(conn, tid, "lane block", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "lane block", "needs_input", worker=True)
        assert _triple(conn, tid)[0] == "triage"
        kb.add_comment(conn, tid, "nexus", f"{E3_REASON_A} — memo attached")
        assert _block(conn, tid, E3_REASON_B, "needs_input", worker=False)
        assert _triple(conn, tid)[0] == "triage"


def test_incident_e2e_reason_change_repark_lands(kanban_home: Path) -> None:
    """Same landing via the OTHER reset trigger: the park reason text changed."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "reason change repark")
        _block(conn, tid, "lane block", "needs_input", worker=True)
        kb.unblock_task(conn, tid)
        _make_running_again(conn, tid)
        assert _block(conn, tid, "lane block", "needs_input", worker=True)
        assert _triple(conn, tid)[0] == "triage"
        assert _block(conn, tid, E3_REASON_B, "needs_input", worker=True)
        assert _triple(conn, tid)[0] == "triage"
        assert _triple(conn, tid)[2] == 0
