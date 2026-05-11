from __future__ import annotations

from devforge.core.config_loader import FilePolicy
from devforge.evaluators.file_policy_checker import check_file_policy


def test_blocked_paths() -> None:
    policy = FilePolicy(blocked_paths=[".env", ".env.*", ".git/**", "secrets/**"])
    r = check_file_policy([".env", ".env.production", ".git/HEAD", "secrets/key.pem"], policy)
    assert sorted(r.blocked) == sorted(
        [".env", ".env.production", ".git/HEAD", "secrets/key.pem"]
    )
    assert not r.has_review


def test_require_review() -> None:
    policy = FilePolicy(
        require_human_review_if_modified=["Dockerfile", "package-lock.json", "infra/**"],
    )
    r = check_file_policy(["Dockerfile", "infra/main.tf", "src/x.py"], policy)
    assert "Dockerfile" in r.require_review
    assert "infra/main.tf" in r.require_review
    assert "src/x.py" not in r.require_review


def test_allowed_outside() -> None:
    policy = FilePolicy(allowed_paths=["src/**", "tests/**"])
    r = check_file_policy(["src/x.py", "weird.txt"], policy)
    assert "src/x.py" in r.allowed
    assert "weird.txt" in r.outside_allowed


def test_blocked_takes_precedence_over_allowed() -> None:
    policy = FilePolicy(
        allowed_paths=["**"],
        blocked_paths=[".env"],
    )
    r = check_file_policy([".env"], policy)
    assert r.blocked == [".env"]
    assert ".env" not in r.allowed
