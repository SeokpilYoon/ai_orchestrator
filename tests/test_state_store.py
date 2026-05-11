from __future__ import annotations

import json
from pathlib import Path

import pytest

from devforge.core.state_store import StateStore, StateStoreError


def _store(tmp_path: Path) -> StateStore:
    run_root = tmp_path / "20260511_001"
    run_root.mkdir()
    return StateStore(run_root)


def test_init_run_creates_files(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref="input.md", stages=["a", "b"])
    assert s.run_path.exists()
    assert s.steps_path.exists()
    assert s.candidates_path.exists()
    run = s.load_run()
    assert run["status"] == "pending"
    assert run["workflow"] == "feature"
    assert run["stages"] == ["a", "b"]
    steps = s.load_steps()
    assert [step["stage_id"] for step in steps] == ["a", "b"]
    assert all(step["status"] == "pending" for step in steps)


def test_init_run_idempotent(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    s.update_run_status("running")
    # Second init must not reset status to pending.
    s.init_run(workflow="feature", input_ref=None, stages=["a", "b"])
    assert s.load_run()["status"] == "running"


def test_update_run_status_terminal_sets_completed_at(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    s.update_run_status("completed")
    run = s.load_run()
    assert run["status"] == "completed"
    assert run["completed_at"] is not None


def test_update_run_status_running_no_completed_at(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    s.update_run_status("running")
    assert s.load_run()["completed_at"] is None


def test_update_run_status_invalid(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    with pytest.raises(StateStoreError):
        s.update_run_status("wobbling")


def test_save_step_running_then_completed(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    s.save_step("a", "running")
    a = s.load_steps()[0]
    assert a["status"] == "running"
    assert a["started_at"] is not None
    assert a["completed_at"] is None

    s.save_step("a", "completed", artifact_ref="a.json")
    a = s.load_steps()[0]
    assert a["status"] == "completed"
    assert a["completed_at"] is not None
    assert a["artifact_ref"] == "a.json"


def test_save_step_unknown_stage_appended(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    s.save_step("dynamic", "completed")
    ids = [step["stage_id"] for step in s.load_steps()]
    assert ids == ["a", "dynamic"]


def test_save_step_invalid_status(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    with pytest.raises(StateStoreError):
        s.save_step("a", "weird")


def test_save_candidate_dedupes(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    s.save_candidate(
        "cand1",
        provider_id="codex_sub_cli",
        decision="revise",
        score=70.0,
        decision_ref="candidates/cand1/decision.json",
    )
    s.save_candidate(
        "cand1",
        provider_id="codex_sub_cli",
        decision="accept",
        score=95.0,
        decision_ref="candidates/cand1/decision.json",
    )
    cands = s.load_candidates()
    assert len(cands) == 1
    assert cands[0]["decision"] == "accept"
    assert cands[0]["score"] == 95.0


def test_save_final_decision_updates_run(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    s.save_final_decision("decision.json", "cand1")
    run = s.load_run()
    assert run["final_decision_ref"] == "decision.json"
    assert run["chosen_candidate"] == "cand1"


def test_state_does_not_clobber_run_root_run_json(tmp_path: Path) -> None:
    run_root = tmp_path / "abc"
    run_root.mkdir()
    legacy = run_root / "run.json"
    legacy.write_text('{"legacy": true}', encoding="utf-8")
    s = StateStore(run_root)
    s.init_run(workflow="feature", input_ref=None, stages=["a"])
    # legacy file untouched
    legacy_doc = json.loads(legacy.read_text(encoding="utf-8"))
    assert legacy_doc == {"legacy": True}
    # state lives in a separate subdir
    assert s.run_path != legacy
    assert s.run_path.parent.name == "state"


def test_summary_line(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.init_run(workflow="feature", input_ref=None, stages=["a", "b"])
    s.save_step("a", "completed")
    s.save_candidate(
        "cand1",
        provider_id="p",
        decision="accept",
        score=90.0,
        decision_ref="x.json",
    )
    s.save_final_decision("decision.json", "cand1")
    s.update_run_status("completed")
    line = s.summary_line()
    assert line is not None
    assert "completed" in line
    assert "1/2 steps completed" in line
    assert "1 candidate" in line
    assert "chosen=cand1" in line


def test_summary_line_missing_returns_none(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.summary_line() is None
