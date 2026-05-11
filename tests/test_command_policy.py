from __future__ import annotations

from devforge.core.config_loader import CommandPolicy
from devforge.evaluators.command_policy_checker import check_command_policy


def test_blocked_pattern_detected() -> None:
    policy = CommandPolicy(blocked_patterns=["rm -rf", "git push", "curl * | sh", "sudo"])
    r = check_command_policy(["I will run rm -rf /tmp/stuff"], policy)
    assert r.has_blocked
    patterns = [p for p, _ in r.blocked_hits]
    assert "rm -rf" in patterns


def test_review_pattern_detected() -> None:
    policy = CommandPolicy(require_human_review=["pip install", "npm install"])
    r = check_command_policy(["running pip install foo"], policy)
    assert any(p == "pip install" for p, _ in r.review_hits)


def test_no_hits_on_clean_text() -> None:
    policy = CommandPolicy(blocked_patterns=["rm -rf"], require_human_review=["pip install"])
    r = check_command_policy(["everything is fine"], policy)
    assert not r.blocked_hits
    assert not r.review_hits
