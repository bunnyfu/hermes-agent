"""Attachment declared-size guard: the t_a45e7dcd attachment-38 incident (2026-09-30).

Third occurrence of the attach-byte-corruption family (after t_2ae14d55
2026-09-05 and the t_c5393c37/t_4674e918 2026-09-18 case). A forge worker
attached its 57,226-byte deploy log to the options-worker board via
``kanban_attach``, but the model emitted 508 base64 chars that decode to
380 bytes of "MICROSOFT-IIR: GENERIC_PMP" filler — a payload whose own
header text claims "Real Content Size : 57226 Bytes" (twice). The handler
decoded it, the store wrote it, the row recorded ``size=380``, and the
tool returned ``ok:true``: silent garbage, again. state.db forensics
(t_a3b3b707) located the entry point in the model-emitted tool-call args;
handler and storage are a byte-faithful pass-through.

The Sep-18 fabricated-path guard does NOT see this family: the payload is
self-describing prose, not a filesystem-path fragment.

Fix contract proven here (t_56e6e167, server-side guard on the inline
attach path):

  1. ``kanban_attach`` accepts declared ``expected_size`` /
     ``expected_sha256`` — values the caller observed BEFORE encoding —
     and refuses the attach when the decoded payload disagrees with the
     declaration: nothing is stored, the tool errors loudly. The
     380-vs-57226 signature is caught; the payload's own header text is
     never consulted.
  2. Honest callers still store cleanly (the guard must not punish
     correct attaches), and the row + result carry the payload sha256.
  3. The fixture IS the incident: the junk payload is the exact 380-byte
     blob recovered from the emitting session (sha256 c1c0de08…), the
     declared values are those of the true original (57,226 B,
     sha256 7a9fb2a2…), and the clean original is rebuilt in-fixture
     (gzip-embedded) to prove the declaration is real, not invented.
  4. Malformed declarations (non-numeric / negative size, non-hex
     digest) are clean tool errors, never silent guard skips.
  5. Per-board DB shapes: a board DB whose ``task_attachments`` lacks
     the sha256 column entirely (the options-worker shape, born
     post-wipe) gains it via the additive migration on connect, and rows
     written there carry the digest.

Pre-delta teeth: on main @ 99721dca (before the replay-merge delta) the
section-1 tests FAIL — the junk args land as a clean ``ok:true`` row —
because no declared-content check existed anywhere on the path.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Incident fixture — the exact bytes from the t_a45e7dcd attachment 38 event
# (recovered from the emitting session's model-emitted tool-call args,
# t_a3b3b707 forensics; attachment 439 on t_19ac95b1 is the same blob).
# Both constants below were embedded mechanically from the verified sources
# and roundtrip-checked (decode → sha256) against the incident digests; a
# first hand-typed copy of JUNK_B64 failed that check (one wrong char) and
# was replaced — the same discipline the guard itself enforces.
# ---------------------------------------------------------------------------

# 508 base64 chars → 380 bytes; sha256 c1c0de0821fac1cf9d28ced73d517002aa74d571c87319837449c059e23f69d3
JUNK_B64 = (
    "TUlDUk9TT0ZULUlJUjogR0VORVJJQ19QTVAgLS0+IEdFTkVSSUMgREVGQVVMVC4gRk9SIE9QRU5JTkcgUFVNUC4KClJlYWwgQ29udGVudCBTaXplIDogNTcyMjYgQnl0ZXMKQ29tcHV0ZXIgOiBJU0FNTUUKUHJvdG9jb2wgVmVyc2lvbiA6IDAuOTAKT1MgOiBNYWNPUwpHVVVJRCA6IGRhZmQ2OTFkLWRlYzAtNGNhOC1iYTI1LTA4NzY1YmM0NmQzNQpEYXRlIENyZWF0ZWQgOiBNb25kYXksIEp1bHkgMTksIDIwMjEgNDozODoxMiBQTSBNRVRJLU1FClJlY29yZCBUeXBlIDogRG9jdW1lbnRhdGlvbgoKTUlDUk9TT0ZULUlJUiBHRU5FUklDLVBNUCAtLT4gR0VORVJJQyBERUZBVUxULiBGT1IgT1BFTklORyBQVU1QLgoKUmVhbCBDb250ZW50IFNpemUgOiA1NzIyNiBCeXRlcwo="
)
JUNK_SIZE = 380
JUNK_SHA256 = "c1c0de0821fac1cf9d28ced73d517002aa74d571c87319837449c059e23f69d3"

# The TRUE original the model was supposed to encode (still on disk in the
# forge scratch at incident time). The test rebuilds it from the gzip-embedded
# CLEAN_B64 (spliced at authoring time) and asserts its digest.
TRUE_SIZE = 57_226
CLEAN_SHA256 = "7a9fb2a2863d961362210b79d05b514410f4edae969e62557bda60ff7fa6f8d3"
CLEAN_B64 = "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"


def _clean_original() -> bytes:
    return gzip.decompress(base64.b64decode(CLEAN_B64))


# ---------------------------------------------------------------------------
# Helpers (mirrors test_attach_integrity.py; kept local for self-containment)
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_env(monkeypatch, tmp_path):
    """Isolated HERMES_HOME + a claimed task; HERMES_KANBAN_TASK set."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from hermes_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="declared-size-test", assignee="test-worker")
        kb.claim_task(conn, tid)
    finally:
        conn.close()
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    return tid


def _all_rows(conn, tid: str):
    return conn.execute(
        "SELECT id, size, content_type, sha256 FROM task_attachments "
        "WHERE task_id = ?",
        (tid,),
    ).fetchall()


def _attachments_dir(tid: str) -> Path:
    from hermes_cli import kanban_db as kb

    return kb.task_attachments_dir(tid)


def _assert_nothing_stored(tid: str) -> None:
    """No task_attachments row and no blob under the attachments dir."""
    from hermes_cli import kanban_db as kb

    conn = kb.connect()
    try:
        assert _all_rows(conn, tid) == []
    finally:
        conn.close()
    att_dir = _attachments_dir(tid)
    blobs = list(att_dir.iterdir()) if att_dir.exists() else []
    assert blobs == [], f"orphan blobs left behind: {blobs}"


def _attach(worker_env, filename: str, content_b64: str, **extra) -> dict:
    from tools import kanban_tools as kt

    args = {
        "task_id": worker_env,
        "filename": filename,
        "content_base64": content_b64,
    }
    args.update(extra)
    return json.loads(kt._handle_attach(args))


# ---------------------------------------------------------------------------
# 1. The incident signature: declared values vs. emitted junk
# ---------------------------------------------------------------------------

def test_incident_attach_declared_size_rejected(worker_env):
    """The t_a45e7dcd row-38 invocation with the size the worker KNEW
    (ls -l said 57226 seconds before emitting) must be a loud rejection
    with nothing stored. Old behaviour: {ok:true, size:380} + junk blob."""
    tid = worker_env
    d = _attach(
        tid,
        "deploy_t_a45e7dcd.log",
        JUNK_B64,
        expected_size=TRUE_SIZE,
    )
    assert "error" in d, d
    assert "integrity" in d["error"].lower(), d
    assert str(TRUE_SIZE) in d["error"], d
    assert str(JUNK_SIZE) in d["error"], d
    _assert_nothing_stored(tid)


def test_incident_attach_declared_sha256_rejected(worker_env):
    """Same refusal via the declared digest of the true original."""
    tid = worker_env
    d = _attach(
        tid,
        "deploy_t_a45e7dcd.log",
        JUNK_B64,
        expected_sha256=CLEAN_SHA256,
    )
    assert "error" in d, d
    assert "integrity" in d["error"].lower(), d
    _assert_nothing_stored(tid)


def test_incident_attach_declared_both_rejected(worker_env):
    """Both declared → size check fires first; refusal either way."""
    tid = worker_env
    d = _attach(
        tid,
        "deploy_t_a45e7dcd.log",
        JUNK_B64,
        expected_size=TRUE_SIZE,
        expected_sha256=CLEAN_SHA256,
    )
    assert "error" in d, d
    assert "integrity" in d["error"].lower(), d
    _assert_nothing_stored(tid)


# ---------------------------------------------------------------------------
# 2. Honest callers store cleanly
# ---------------------------------------------------------------------------

def test_honest_declared_attach_stores(worker_env):
    """A payload matching its declaration stores; the row carries the
    digest; the result echoes it."""
    from hermes_cli import kanban_db as kb

    source = _clean_original()
    assert len(source) == TRUE_SIZE
    d = _attach(
        worker_env,
        "deploy_t_a45e7dcd.log",
        base64.b64encode(source).decode(),
        expected_size=TRUE_SIZE,
        expected_sha256=CLEAN_SHA256,
    )
    assert d.get("ok") is True, d
    assert d["size"] == TRUE_SIZE
    assert d["sha256"] == CLEAN_SHA256
    conn = kb.connect()
    try:
        rows = _all_rows(conn, worker_env)
        assert len(rows) == 1
        assert rows[0]["size"] == TRUE_SIZE
        assert rows[0]["sha256"] == CLEAN_SHA256
    finally:
        conn.close()
    stored = _attachments_dir(worker_env) / "deploy_t_a45e7dcd.log"
    assert stored.read_bytes() == source


def test_sha256_declared_alone_still_stores(worker_env):
    """Declaring only the digest (no size) is a valid honest call."""
    source = b"exact bytes only\n"
    d = _attach(
        worker_env,
        "notes.txt",
        base64.b64encode(source).decode(),
        expected_sha256=hashlib.sha256(source).hexdigest(),
    )
    assert d.get("ok") is True, d


# ---------------------------------------------------------------------------
# 3. Fixture integrity: the fixture IS the incident, not an invention
# ---------------------------------------------------------------------------

def test_fixture_junk_bytes_match_incident_fingerprint():
    """The embedded junk decodes to the exact 380 bytes forensically tied
    to attachment row 38 — and carries the self-describing header that
    names the true size (which the guard must never trust)."""
    junk = base64.b64decode(JUNK_B64)
    assert len(junk) == JUNK_SIZE
    assert hashlib.sha256(junk).hexdigest() == JUNK_SHA256
    assert junk.count(b"Real Content Size : 57226 Bytes") == 2
    # Not a filesystem-path fragment: the Sep-18 fabricated-path guard is
    # blind to this family by construction.
    assert not junk.lstrip()[:1] in (b"/", b".", b"~")


def test_fixture_clean_original_roundtrips_to_true_digest():
    """The embedded clean original decompresses to exactly the true file:
    57,226 bytes, sha256 7a9fb2a2… — so the declarations used in the
    rejection tests are the REAL original's values."""
    source = _clean_original()
    assert len(source) == TRUE_SIZE
    assert hashlib.sha256(source).hexdigest() == CLEAN_SHA256


# ---------------------------------------------------------------------------
# 4. Malformed declarations are clean errors, never silent guard skips
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_size", ["big", 3.5, -1, ""])
def test_expected_size_malformed_clean_error(worker_env, bad_size):
    d = _attach(
        worker_env,
        "f.txt",
        base64.b64encode(b"payload").decode(),
        expected_size=bad_size,
    )
    assert "error" in d, d
    assert "expected_size" in d["error"], d
    _assert_nothing_stored(worker_env)


@pytest.mark.parametrize("bad_sha", ["zz" * 32, "abc", ""])
def test_expected_sha256_malformed_clean_error(worker_env, bad_sha):
    d = _attach(
        worker_env,
        "f.txt",
        base64.b64encode(b"payload").decode(),
        expected_sha256=bad_sha,
    )
    assert "error" in d, d
    assert "expected_sha256" in d["error"], d
    _assert_nothing_stored(worker_env)


def test_expected_sha256_uppercase_normalized_still_enforced(worker_env):
    """Uppercase hex is case-normalized, not rejected — the declaration is
    still enforced: 'A'*64 lowercases to a valid digest that cannot match
    the payload, so the attach is refused by the mismatch guard with
    nothing stored."""
    d = _attach(
        worker_env,
        "f.txt",
        base64.b64encode(b"payload").decode(),
        expected_sha256="A" * 64,
    )
    assert "error" in d, d
    assert "integrity" in d["error"].lower(), d
    _assert_nothing_stored(worker_env)


# ---------------------------------------------------------------------------
# 5. Contract boundary (documented): undeclared corrupt attaches
# ---------------------------------------------------------------------------

def test_undeclared_corrupt_attach_still_records_digest_for_audit(worker_env):
    """Without a declaration there is nothing server-side to check the
    payload against (the guard is contract-based; it never parses the
    payload's self-describing header), so the attach lands — but the row
    records the payload's TRUE digest (junk's c1c0de08…, not the clean
    7a9fb2a2…), so post-hoc audits can detect the corruption. On main @
    99721dca80 this leg FAILS: the row carried no digest at all (and on
    the options-worker shape, no column even existed)."""
    from hermes_cli import kanban_db as kb

    d = _attach(worker_env, "deploy_t_a45e7dcd.log", JUNK_B64)
    assert d.get("ok") is True, d
    assert d["size"] == JUNK_SIZE
    assert d["sha256"] == JUNK_SHA256
    conn = kb.connect()
    try:
        rows = _all_rows(conn, worker_env)
        assert len(rows) == 1
        assert rows[0]["sha256"] == JUNK_SHA256
        assert rows[0]["size"] == JUNK_SIZE
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Per-board DB shapes: the options-worker shape (no column at all)
# ---------------------------------------------------------------------------

def test_board_without_sha256_column_migrates_and_records(tmp_path, monkeypatch):
    """A board DB created before the column existed (the options-worker
    shape) gains sha256 via the additive migration on connect, and a
    stored row records the digest there."""
    import sqlite3

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from hermes_cli import kanban_db as kb

    kb.create_board("incident-shape")

    # Wind the board's task_attachments back to the pre-sha256 shape.
    db_path = kb.kanban_db_path("incident-shape")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE task_attachments")
        conn.execute(
            """
            CREATE TABLE task_attachments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      TEXT NOT NULL,
                filename     TEXT NOT NULL,
                stored_path  TEXT NOT NULL,
                content_type TEXT,
                size         INTEGER NOT NULL DEFAULT 0,
                uploaded_by  TEXT,
                created_at   INTEGER NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Reconnect through the normal path: the migration must fire and the
    # declared-size guard must work against this board.
    kb._INITIALIZED_PATHS.clear()
    conn = kb.connect(board="incident-shape")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(task_attachments)")}
        assert "sha256" in cols
        tid = kb.create_task(conn, title="board-shape", assignee="t")
        with pytest.raises(kb.AttachmentIntegrityError):
            kb.store_attachment_bytes(
                conn, tid, "deploy.log", base64.b64decode(JUNK_B64),
                expected_size=TRUE_SIZE,
            )
        att_id = kb.store_attachment_bytes(
            conn, tid, "deploy.log", b"real bytes",
            expected_size=10,
        )
        row = conn.execute(
            "SELECT size, sha256 FROM task_attachments WHERE id = ?", (att_id,)
        ).fetchone()
        assert row[0] == 10
        assert row[1] == hashlib.sha256(b"real bytes").hexdigest()
    finally:
        conn.close()
