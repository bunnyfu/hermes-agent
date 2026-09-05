"""Completion events record the CALLING session's identity, not just the run.

Incident t_fd052480 (resolved 2026-09-05): a critic desktop session executed
``hermes kanban --board B complete t_X --result ...`` while a dispatcher
worker held the claim. The CLI passes ``expected_run_id=None`` for that shape,
so the unconditional write stamped the completion with the WORKER's run id
(run 23) — the event log could not name the real caller, and the incident
investigation had to reconstruct authorship from session stores.

The fix: ``complete_task`` accepts ``actor_session_id``/``actor_profile`` and
records them on the ``completed`` event payload; the CLI handlers pass their
own env-derived identity (HERMES_SESSION_ID / HERMES_PROFILE[_NAME]) — same
no-override rule as comment author. The CLI comment path now also records
session provenance via the ``task_comments.session_id`` column added by the
profile-identity lifecycle bundle (PR #103457), which previously only the
tool handler populated.
"""
from __future__ import annotations

import argparse

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def board(monkeypatch, tmp_path):
    """Fresh board DB (HERMES_KANBAN_DB pinned), the way the bundle's
    claim-identity fixtures build one. Returns the kanban_db module."""
    from hermes_cli import kanban_db as kb

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
    return kb


@pytest.fixture
def cli_env(monkeypatch):
    """The incident's caller shape: a full agent session of profile 'critic'
    with NO dispatcher task/run env (its ``kanban complete`` therefore runs
    with expected_run_id=None)."""
    monkeypatch.setenv("HERMES_PROFILE", "critic")
    monkeypatch.setenv("HERMES_AGENT", "true")
    monkeypatch.setenv("HERMES_SESSION_ID", "20260830_145045_b8e20d")
    for var in ("HERMES_PROFILE_NAME", "HERMES_KANBAN_TASK",
                "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK"):
        monkeypatch.delenv(var, raising=False)


def _completed_payload(kb, conn, tid) -> dict:
    ev = [e for e in kb.list_events(conn, tid) if e.kind == "completed"][-1]
    return ev.payload or {}


def _completed_run_id(kb, conn, tid):
    ev = [e for e in kb.list_events(conn, tid) if e.kind == "completed"][-1]
    return ev.run_id


# ---------------------------------------------------------------------------
# complete_task actor binding (DB layer)
# ---------------------------------------------------------------------------

def test_complete_task_records_actor_on_event(board):
    kb = board
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="forge")
        ok = kb.complete_task(
            conn, tid, result="done", actor_session_id="sess-abc",
            actor_profile="critic")
        assert ok is True
        payload = _completed_payload(kb, conn, tid)
        assert payload.get("actor_session_id") == "sess-abc"
        assert payload.get("actor_profile") == "critic"
    finally:
        conn.close()


def test_complete_task_without_actor_stays_clean(board):
    """No actor args -> no provenance keys (historical shape unchanged)."""
    kb = board
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="forge")
        assert kb.complete_task(conn, tid, result="done") is True
        payload = _completed_payload(kb, conn, tid)
        assert "actor_session_id" not in payload
        assert "actor_profile" not in payload
    finally:
        conn.close()


def test_complete_task_blank_actor_normalized_to_absent(board):
    kb = board
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="forge")
        assert kb.complete_task(conn, tid, result="done", actor_session_id="   ",
                                actor_profile="") is True
        payload = _completed_payload(kb, conn, tid)
        assert "actor_session_id" not in payload
        assert "actor_profile" not in payload
    finally:
        conn.close()


def test_actor_distinct_from_stamped_run(board):
    """The incident's forensic core: the completion event's run_id is whichever
    run held the claim (here run 1, the claimant), while the actor fields name
    the real caller — the two are recorded independently."""
    kb = board
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="worker")
        kb.claim_task(conn, tid)  # run 1 holds the claim
        ok = kb.complete_task(
            conn, tid, result="done", expected_run_id=None,
            actor_session_id="20260830_145045_b8e20d", actor_profile="critic")
        assert ok is True
        payload = _completed_payload(kb, conn, tid)
        assert payload.get("actor_session_id") == "20260830_145045_b8e20d"
        assert payload.get("actor_profile") == "critic"
        assert _completed_run_id(kb, conn, tid) == 1  # stamped with the claimant's run
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI handler passes its own env identity (no caller-supplied override exists)
# ---------------------------------------------------------------------------

def _kanban_cli():
    import hermes_cli.kanban as kanban_cli

    return kanban_cli


def test_cli_complete_stamps_env_actor(board, cli_env):
    kb = board
    kcli = _kanban_cli()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="forge")
    finally:
        conn.close()
    args = argparse.Namespace(task_ids=[tid], task_id=tid, result="r", summary=None,
                              metadata=None, json=False, max_len=None, board=None)
    rc = kcli._cmd_complete(args)
    assert rc == 0
    conn = kb.connect()
    try:
        payload = _completed_payload(kb, conn, tid)
    finally:
        conn.close()
    assert payload.get("actor_session_id") == "20260830_145045_b8e20d"
    assert payload.get("actor_profile") == "critic"


def test_cli_complete_without_session_env_has_no_actor(board, monkeypatch):
    """Human shell / plain process: no HERMES_SESSION_ID -> no actor keys."""
    kb = board
    kcli = _kanban_cli()
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="forge")
    finally:
        conn.close()
    args = argparse.Namespace(task_ids=[tid], task_id=tid, result="r", summary=None,
                              metadata=None, json=False, max_len=None, board=None)
    assert kcli._cmd_complete(args) == 0
    conn = kb.connect()
    try:
        payload = _completed_payload(kb, conn, tid)
    finally:
        conn.close()
    assert "actor_session_id" not in payload


# ---------------------------------------------------------------------------
# CLI comment authorship provenance (session_id column from the bundle)
# ---------------------------------------------------------------------------

def test_cli_comment_records_session_id(board, cli_env):
    kb = board
    kcli = _kanban_cli()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="forge")
    finally:
        conn.close()
    args = argparse.Namespace(task_id=tid, text=["hello", "world"], max_len=None,
                              author=None, json=False, board=None)
    assert kcli._cmd_comment(args) == 0
    conn = kb.connect()
    try:
        (c,) = kb.list_comments(conn, tid)
    finally:
        conn.close()
    assert c.body == "hello world"
    assert c.session_id == "20260830_145045_b8e20d"


def test_cli_block_reason_comment_records_session_id(board, cli_env):
    kb = board
    kcli = _kanban_cli()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="forge")
    finally:
        conn.close()
    args = argparse.Namespace(task_ids=[tid], task_id=tid,
                              reason=["need", "input"], kind="needs_input",
                              json=False, board=None)
    assert kcli._cmd_block(args) == 0
    conn = kb.connect()
    try:
        comments = kb.list_comments(conn, tid)
    finally:
        conn.close()
    assert any(c.session_id == "20260830_145045_b8e20d" and
               c.body.startswith("BLOCKED:") for c in comments)
