from __future__ import annotations

from pathlib import Path

from devforge.providers.base import AgentProvider, AgentRequest, AgentResult
from devforge.providers.mock import MockProvider, write_files_behavior


def test_mock_provider_protocol() -> None:
    p = MockProvider()
    assert isinstance(p, AgentProvider)
    assert p.healthcheck() is True
    assert p.supports("edit_files")


def test_mock_provider_default_success(tmp_path: Path) -> None:
    p = MockProvider()
    req = AgentRequest(role="implementer", prompt="x", cwd=tmp_path, run_id="r1")
    res = p.run(req)
    assert isinstance(res, AgentResult)
    assert res.success is True
    assert res.provider_id == "mock"


def test_mock_provider_write_files(tmp_path: Path) -> None:
    p = MockProvider(behavior=write_files_behavior({"src/x.py": "print(1)\n"}))
    req = AgentRequest(role="implementer", prompt="x", cwd=tmp_path, run_id="r1")
    res = p.run(req)
    assert res.success
    assert "src/x.py" in res.changed_files
    assert (tmp_path / "src" / "x.py").read_text() == "print(1)\n"
