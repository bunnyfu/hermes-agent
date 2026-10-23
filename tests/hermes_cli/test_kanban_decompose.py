"""Tests for the decomposer module + `hermes kanban decompose` CLI surface.

The auxiliary LLM client is mocked — no network calls. Tests exercise the
prompt plumbing, response parsing, DB writes (via the real DB helper),
and the assignee-fallback logic.
"""

from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_decompose as decomp


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _fake_aux_response(content: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


def _mock_client_returning(content: str):
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=_fake_aux_response(content))
    return client


def _patch_aux_client(content: str, *, model: str = "test-model"):
    # decompose_task now routes through call_llm (see #35566) — mock it at
    # the source module so task config, extra_body, and retries stay out of
    # unit-test scope.
    return patch(
        "agent.auxiliary_client.call_llm",
        return_value=_fake_aux_response(content),
    )


def _patch_extra_body():
    # No-op shim retained for call-site compatibility: extra_body plumbing
    # now lives inside call_llm, which _patch_aux_client already mocks.
    return patch("agent.auxiliary_client.get_auxiliary_extra_body", return_value={})


def _profile_modules():
    """Distinct module objects through which the decomposer resolves profiles.

    Normally ``hermes_cli.profiles`` is the only one, but
    ``test_kanban_cli_dispatch_passthrough.isolated_kanban_home`` deletes every
    ``hermes_cli*`` entry from ``sys.modules`` without restoring it. After that
    file runs, ``sys.modules["hermes_cli.profiles"]`` is a FRESH object while this
    module (and the decomposer it imported at collection time) still holds the
    original — so a string-target ``patch("hermes_cli.profiles...")`` silently
    patches the wrong object and the real registry leaks into the test.
    """
    mods = []
    for mod in (decomp.profiles_mod, sys.modules.get("hermes_cli.profiles")):
        if mod is not None and not any(mod is m for m in mods):
            mods.append(mod)
    return mods


def _patch_list_profiles(names: list[str]):
    """Pretend the named profiles exist. The decomposer uses
    profiles_mod.list_profiles() to build the roster + valid-set, and
    profiles_mod.profile_exists() to resolve orchestrator/default."""
    from types import SimpleNamespace
    fake_profiles = [
        SimpleNamespace(
            name=n, is_default=(i == 0), description=f"desc for {n}",
            description_auto=False, model="m", provider="p", skill_count=1,
        )
        for i, n in enumerate(names)
    ]
    patches = []
    for mod in _profile_modules():
        patches.extend([
            patch.object(mod, "list_profiles", return_value=fake_profiles),
            patch.object(mod, "profile_exists", side_effect=lambda x: x in names),
            patch.object(mod, "get_active_profile_name",
                         return_value=names[0] if names else "default"),
        ])
    return patches


def test_decompose_with_fanout_creates_children(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="ship a feature", triage=True)

    llm_payload = jsonlib.dumps({
        "fanout": True,
        "rationale": "test split",
        "tasks": [
            {"title": "research", "body": "look it up", "assignee": "researcher", "parents": []},
            {"title": "build", "body": "code it", "assignee": "engineer", "parents": [0]},
        ],
    })

    patches = _patch_list_profiles(["orchestrator", "researcher", "engineer"])
    for p in patches:
        p.start()
    try:
        with _patch_aux_client(llm_payload), _patch_extra_body():
            outcome = decomp.decompose_task(tid, author="me")
    finally:
        for p in patches:
            p.stop()

    assert outcome.ok, outcome.reason
    assert outcome.fanout is True
    assert outcome.child_ids and len(outcome.child_ids) == 2

    with kbc.connect() as conn:
        root = kb.get_task(conn, tid)
        c0 = kb.get_task(conn, outcome.child_ids[0])
        c1 = kb.get_task(conn, outcome.child_ids[1])
    assert root.status == "todo"
    assert c0.status == "ready"
    assert c1.status == "todo"
    assert c0.assignee == "researcher"
    assert c1.assignee == "engineer"


def test_decompose_fanout_children_inherit_root_assignee_when_unrouted(kanban_home):
    """Unrouted children fall back to the ROOT task's assignee, not
    the decomposer's active profile (#114294). The active profile here is ``private``
    (an incognito profile with no credentials), so the old fallback spawned
    workers that deadlocked on capability blockers."""
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="ship it", assignee="zdr", triage=True)

    llm_payload = jsonlib.dumps({
        "fanout": True,
        "rationale": "test split",
        "tasks": [
            {"title": "research", "body": "look it up", "assignee": "made_up", "parents": []},
            {"title": "build", "body": "code it", "assignee": None, "parents": [0]},
        ],
    })

    # get_active_profile_name() is mocked to names[0] = "private" — the
    # global default chain would resolve there without kanban.default_assignee.
    patches = _patch_list_profiles(["private", "zdr"])
    for p in patches:
        p.start()
    try:
        with _patch_aux_client(llm_payload), _patch_extra_body(), patch(
            "hermes_cli.config.load_config_readonly",
            return_value={},
        ):
            outcome = decomp.decompose_task(tid, author="me")
    finally:
        for p in patches:
            p.stop()

    assert outcome.ok, outcome.reason
    with kbc.connect() as conn:
        root = kb.get_task(conn, tid)
        c0 = kb.get_task(conn, outcome.child_ids[0])
        c1 = kb.get_task(conn, outcome.child_ids[1])
    assert c0.assignee == "zdr"
    assert c1.assignee == "zdr"
    # Same class for the root: no ``orchestrator_profile`` must not hand the
    # orchestration card to the dispatcher's own (here: incognito) profile.
    assert root.assignee == "zdr"


def test_decompose_explicit_default_assignee_wins_over_root_assignee(kanban_home):
    """An explicitly configured ``kanban.default_assignee`` stays
    authoritative for unroutable children; the root task's assignee only
    fills in when no explicit default is set (explicit config → card
    assignee → active profile)."""
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="ship it", assignee="engineer", triage=True)

    llm_payload = jsonlib.dumps({
        "fanout": True,
        "rationale": "test split",
        "tasks": [
            {"title": "research", "body": "look it up", "assignee": "made_up", "parents": []},
            {"title": "build", "body": "code it", "assignee": None, "parents": [0]},
        ],
    })

    patches = _patch_list_profiles(["engineer", "docs", "private"])
    for p in patches:
        p.start()
    try:
        with _patch_aux_client(llm_payload), _patch_extra_body(), patch(
            "hermes_cli.config.load_config_readonly",
            return_value={"kanban": {"default_assignee": "docs"}},
        ):
            outcome = decomp.decompose_task(tid, author="me")
    finally:
        for p in patches:
            p.stop()

    assert outcome.ok, outcome.reason
    with kbc.connect() as conn:
        c0 = kb.get_task(conn, outcome.child_ids[0])
        c1 = kb.get_task(conn, outcome.child_ids[1])
    assert c0.assignee == "docs"
    assert c1.assignee == "docs"


def test_decompose_fanout_false_invalid_llm_assignee_uses_default(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="route me safely", triage=True)

    llm_payload = jsonlib.dumps({
        "fanout": False,
        "rationale": "single unit",
        "title": "Tightened title",
        "body": "Route to fallback.",
        "assignee": "made_up",
    })

    patches = _patch_list_profiles(["orchestrator", "fallback"])
    for p in patches:
        p.start()
    try:
        with _patch_aux_client(llm_payload), _patch_extra_body(), patch(
            "hermes_cli.config.load_config_readonly",
            return_value={"kanban": {"default_assignee": "fallback"}},
        ):
            outcome = decomp.decompose_task(tid, author="me")
    finally:
        for p in patches:
            p.stop()

    assert outcome.ok, outcome.reason
    with kbc.connect() as conn:
        task = kb.get_task(conn, tid)
    assert task is not None
    assert task.assignee == "fallback"


def test_load_routing_falls_back_to_defaults_when_config_unreadable(kanban_home, monkeypatch):
    """decompose_task promises ok=False on expected failures; a config read that raises (missing
    profile home, HomeInitializationError) must not escape _load_routing as an exception."""
    from hermes_cli import config as config_mod

    def _boom():
        raise FileNotFoundError("profile home is gone")

    monkeypatch.setattr(config_mod, "load_config_readonly", _boom)
    routing = decomp._load_routing()
    assert routing.default_assignee == "default" and routing.auto_promote is True


def test_decompose_returns_false_when_task_not_triage(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="x")  # ready, not triage

    patches = _patch_list_profiles(["orchestrator"])
    for p in patches:
        p.start()
    try:
        outcome = decomp.decompose_task(tid, author="me")
    finally:
        for p in patches:
            p.stop()
    assert outcome.ok is False


# ---------------------------------------------------------------------------
# kanban.fleet_profiles — decompose-path routing allowlist
# ---------------------------------------------------------------------------

#: Byte-for-byte rendering of the legacy roster block for
#: ``["orchestrator", "researcher", "engineer"]`` (all described). Frozen here so
#: any drift in the unset-key path fails loudly instead of silently re-formatting
#: the prompt.
_LEGACY_ROSTER_TEXT = (
    "  - orchestrator: desc for orchestrator\n"
    "  - researcher: desc for researcher\n"
    "  - engineer: desc for engineer"
)

#: Full legacy user prompt for the same roster — the byte-identity oracle for a
#: config with no ``fleet_profiles`` key.
_LEGACY_PROMPT_TEXT = (
    "Task id: t_frozen\n"
    "Title: title\n"
    "Body:\n"
    "body\n"
    "\n"
    "Available profiles (assignees you may pick from):\n"
    f"{_LEGACY_ROSTER_TEXT}\n"
    "\n"
    "Default assignee (used when no profile fits a task): orchestrator\n"
)


def _patch_env(names: list[str], cfg: dict):
    """Registry + config patches for one routing scenario (context manager)."""
    import contextlib

    stack = contextlib.ExitStack()
    for p in _patch_list_profiles(names):
        stack.enter_context(p)
    stack.enter_context(
        patch("hermes_cli.config.load_config_readonly", return_value=cfg))
    return stack


def _prompt_roster_names(prompt: str) -> list[str]:
    """Profile names as rendered in the prompt's 'Available profiles' block."""
    names: list[str] = []
    in_roster = False
    for line in prompt.splitlines():
        if line.startswith("Available profiles"):
            in_roster = True
            continue
        if not in_roster:
            continue
        if line.startswith("  - "):
            names.append(line[len("  - "):].split(":", 1)[0].strip())
        elif not line.strip():
            in_roster = False
    return names


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= 30]


def test_fleet_profiles_allowlist_restricts_roster_and_prompt(kanban_home):
    """(a) key set -> roster filtered; the prompt offers ONLY allowlisted names."""
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="route by allowlist", triage=True)
    payload = jsonlib.dumps({
        "fanout": True,
        "rationale": "one child",
        "tasks": [{"title": "research it", "body": "go", "assignee": "researcher", "parents": []}],
    })
    cfg = {"kanban": {"fleet_profiles": ["orchestrator", "researcher"],
                      "default_assignee": "orchestrator"}}

    with _patch_env(["orchestrator", "researcher", "engineer", "ebrd"], cfg), \
            _patch_aux_client(payload) as llm:
        outcome = decomp.decompose_task(tid, author="me")

    assert outcome.ok, outcome.reason
    prompt = llm.call_args.kwargs["messages"][1]["content"]
    assert _prompt_roster_names(prompt) == ["orchestrator", "researcher"]
    # Naming a non-allowlisted profile anywhere in the prompt fails this leg.
    assert "engineer" not in prompt
    assert "ebrd" not in prompt

    with kbc.connect() as conn:
        child = kb.get_task(conn, outcome.child_ids[0])
    assert child.assignee == "researcher"


def test_fleet_profiles_unknown_entry_is_warned_and_skipped(kanban_home, caplog):
    """(b) an entry naming no installed profile warns and is dropped."""
    with kbc.connect() as conn:
        kb.create_task(conn, title="triage work")
    cfg = {"kanban": {"fleet_profiles": ["orchestrator", "ghost", "  "]}}

    with _patch_env(["orchestrator", "researcher"], cfg):
        with caplog.at_level(10, logger="hermes_cli.kanban_decompose"):
            routing = decomp._load_routing()

    assert [e["name"] for e in routing.roster] == ["orchestrator"]
    assert routing.valid_names == {"orchestrator"}
    warns = _warnings(caplog)
    assert any("'ghost' names no installed profile" in w for w in warns), warns
    assert any("'  ' is not a profile name" in w for w in warns), warns


def test_fleet_profiles_unset_keeps_legacy_roster_bytes(kanban_home, caplog):
    """(c) key unset -> roster, valid set and prompt bytes are the legacy ones."""
    with kbc.connect() as conn:
        kb.create_task(conn, title="triage work")
    names = ["orchestrator", "researcher", "engineer"]

    with _patch_env(names, {}):
        with caplog.at_level(10, logger="hermes_cli.kanban_decompose"):
            routing = decomp._load_routing()

    assert [e["name"] for e in routing.roster] == names
    assert routing.valid_names == set(names)
    assert decomp._format_roster(routing.roster) == _LEGACY_ROSTER_TEXT
    rendered = decomp._USER_TEMPLATE.format(
        task_id="t_frozen", title="title", body="body",
        roster=decomp._format_roster(routing.roster),
        default_assignee=routing.default_assignee,
    )
    assert rendered == _LEGACY_PROMPT_TEXT
    assert not [w for w in _warnings(caplog) if "fleet_profiles" in w]


def test_fleet_profiles_rewrites_existing_but_non_roster_pick(kanban_home, caplog):
    """(d) an installed profile outside the allowlist is rewritten to the default.

    Closes the ebrd incident path (run 1051): the LLM picked an installed but
    non-allowlisted profile and the mechanical backstop, keyed on the FILTERED
    valid set, now catches it.
    """
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="route me", triage=True)
    payload = jsonlib.dumps({
        "fanout": True,
        "rationale": "two children",
        "tasks": [
            {"title": "one", "body": "b", "assignee": "ebrd", "parents": []},
            {"title": "two", "body": "b", "assignee": "ebrd", "parents": []},
        ],
    })
    cfg = {"kanban": {"fleet_profiles": ["orchestrator", "engineer"],
                      "default_assignee": "engineer"}}

    with _patch_env(["orchestrator", "engineer", "ebrd"], cfg):
        with caplog.at_level(10, logger="hermes_cli.kanban_decompose"), \
                _patch_aux_client(payload) as llm:
            outcome = decomp.decompose_task(tid, author="me")

    assert outcome.ok, outcome.reason
    prompt = llm.call_args.kwargs["messages"][1]["content"]
    assert _prompt_roster_names(prompt) == ["orchestrator", "engineer"]
    assert "ebrd" not in prompt

    with kbc.connect() as conn:
        root = kb.get_task(conn, tid)
        children = [kb.get_task(conn, cid) for cid in outcome.child_ids]
    assert [c.assignee for c in children] == ["engineer", "engineer"]
    assert root.assignee == "orchestrator"
    assert any("picked unknown assignee 'ebrd'" in r.getMessage()
               for r in caplog.records if r.levelno == 20)


@pytest.mark.parametrize("value", [[], ["ghost"], None])
def test_fleet_profiles_empty_after_validation_falls_back_with_warning(kanban_home, caplog, value):
    """(e) set-but-unusable -> full registry, loudly warned (never strands)."""
    with kbc.connect() as conn:
        kb.create_task(conn, title="triage work")
    names = ["orchestrator", "researcher", "engineer"]
    cfg = {"kanban": {"fleet_profiles": value}}

    with _patch_env(names, cfg):
        with caplog.at_level(10, logger="hermes_cli.kanban_decompose"):
            routing = decomp._load_routing()

    assert [e["name"] for e in routing.roster] == names
    assert routing.valid_names == set(names)
    warns = _warnings(caplog)
    assert any("yields no usable profile" in w and "NOT restricted" in w for w in warns), warns


def test_fleet_profiles_wrong_type_falls_back_with_warning(kanban_home, caplog):
    """A scalar (the common YAML typo) is rejected loudly, not silently honoured."""
    with kbc.connect() as conn:
        kb.create_task(conn, title="triage work")
    names = ["orchestrator", "researcher"]
    cfg = {"kanban": {"fleet_profiles": "orchestrator"}}

    with _patch_env(names, cfg):
        with caplog.at_level(10, logger="hermes_cli.kanban_decompose"):
            routing = decomp._load_routing()

    assert [e["name"] for e in routing.roster] == names
    assert routing.valid_names == set(names)
    assert any("must be a list of profile names" in w and "NOT restricted" in w
               for w in _warnings(caplog))

