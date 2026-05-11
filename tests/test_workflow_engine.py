from __future__ import annotations

from pathlib import Path

import pytest

from devforge.core.workflow_engine import (
    WorkflowDefinition,
    WorkflowLoadError,
    load_workflow,
)

VALID_YAML = """
workflow: feature
stages:
  - id: normalize_task
    role: technical_planner
    mode: read_only
    output: normalized_task.json

  - id: implement
    role: implementer
    mode: tournament
    composite: true
    output: candidate_summaries

  - id: write_report
    type: local_writer
    output: final_report.md
"""


def _write_workflow(tmp_path: Path, workflow_id: str, body: str) -> Path:
    p = tmp_path / f"{workflow_id}.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_packaged_feature_workflow_succeeds() -> None:
    definition = load_workflow("feature")
    assert isinstance(definition, WorkflowDefinition)
    assert definition.workflow == "feature"
    assert len(definition.stages) >= 1
    ids = {s.id for s in definition.stages}
    assert "implement_candidates" in ids or "implement" in ids or any(s.role == "implementer" for s in definition.stages)


def test_load_valid_custom_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "feature", VALID_YAML)
    definition = load_workflow("feature", base_dir=tmp_path)
    assert definition.workflow == "feature"
    assert [s.id for s in definition.stages] == ["normalize_task", "implement", "write_report"]
    impl = next(s for s in definition.stages if s.id == "implement")
    assert impl.composite is True
    assert impl.mode == "tournament"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WorkflowLoadError, match="not found"):
        load_workflow("nope", base_dir=tmp_path)


def test_invalid_yaml(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "feature", "workflow: feature\nstages: [unclosed")
    with pytest.raises(WorkflowLoadError, match="Invalid YAML"):
        load_workflow("feature", base_dir=tmp_path)


def test_root_not_mapping(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "feature", "- one\n- two\n")
    with pytest.raises(WorkflowLoadError, match="root must be a mapping"):
        load_workflow("feature", base_dir=tmp_path)


def test_missing_workflow_key(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "feature", "stages: []\n")
    with pytest.raises(WorkflowLoadError, match="'workflow' must"):
        load_workflow("feature", base_dir=tmp_path)


def test_workflow_id_mismatch(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "feature", "workflow: bugfix\nstages: [{id: a, role: implementer}]\n")
    with pytest.raises(WorkflowLoadError, match="workflow id mismatch"):
        load_workflow("feature", base_dir=tmp_path)


def test_empty_stages(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "feature", "workflow: feature\nstages: []\n")
    with pytest.raises(WorkflowLoadError, match="non-empty list"):
        load_workflow("feature", base_dir=tmp_path)


def test_missing_stage_id(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - role: implementer\n",
    )
    with pytest.raises(WorkflowLoadError, match="missing 'id'"):
        load_workflow("feature", base_dir=tmp_path)


def test_duplicate_stage_id(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - {id: a, role: implementer}\n  - {id: a, role: reviewer}\n",
    )
    with pytest.raises(WorkflowLoadError, match="duplicate stage id"):
        load_workflow("feature", base_dir=tmp_path)


def test_unknown_role(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - {id: a, role: wizard}\n",
    )
    with pytest.raises(WorkflowLoadError, match="unknown role"):
        load_workflow("feature", base_dir=tmp_path)


def test_unknown_type(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - {id: a, type: ghost_writer}\n",
    )
    with pytest.raises(WorkflowLoadError, match="unknown type"):
        load_workflow("feature", base_dir=tmp_path)


def test_role_and_type_mutually_exclusive(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - {id: a, role: implementer, type: local_writer}\n",
    )
    with pytest.raises(WorkflowLoadError, match="both 'role' and 'type'"):
        load_workflow("feature", base_dir=tmp_path)


def test_neither_role_nor_type(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - {id: a, output: foo.txt}\n",
    )
    with pytest.raises(WorkflowLoadError, match="must specify either 'role' or 'type'"):
        load_workflow("feature", base_dir=tmp_path)


def test_unknown_mode(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - {id: a, role: implementer, mode: yolo}\n",
    )
    with pytest.raises(WorkflowLoadError, match="unknown mode"):
        load_workflow("feature", base_dir=tmp_path)


def test_unknown_strategy(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "feature",
        "workflow: feature\nstages:\n  - {id: a, role: reviewer, strategy: vibes}\n",
    )
    with pytest.raises(WorkflowLoadError, match="unknown strategy"):
        load_workflow("feature", base_dir=tmp_path)
