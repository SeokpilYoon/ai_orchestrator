from __future__ import annotations

from devforge.providers.base import (
    FAILURE_AUTH_EXPIRED,
    FAILURE_POLICY_VIOLATION,
    FAILURE_USAGE_LIMIT,
)
from devforge.stages.fallback import run_with_fallback


def test_first_provider_succeeds() -> None:
    calls: list[str] = []

    def runner(pid):
        calls.append(pid)
        return ("ok", pid, None, None)

    result, history = run_with_fallback(
        ["a", "b", "c"],
        runner=runner,
        is_success=lambda r: r[0] == "ok",
        classify=lambda r: (r[2], r[3]),
    )
    assert calls == ["a"]
    assert result == ("ok", "a", None, None)
    assert history == []


def test_recoverable_failure_falls_back() -> None:
    behaviors = {
        "a": ("fail", "a", FAILURE_USAGE_LIMIT, "out of credits"),
        "b": ("ok", "b", None, None),
    }
    calls: list[str] = []

    def runner(pid):
        calls.append(pid)
        return behaviors[pid]

    result, history = run_with_fallback(
        ["a", "b"],
        runner=runner,
        is_success=lambda r: r[0] == "ok",
        classify=lambda r: (r[2], r[3]),
    )
    assert calls == ["a", "b"]
    assert result == ("ok", "b", None, None)
    assert len(history) == 1
    assert history[0].provider == "a"
    assert history[0].failure_class == FAILURE_USAGE_LIMIT


def test_fatal_failure_short_circuits() -> None:
    behaviors = {
        "a": ("fail", "a", FAILURE_POLICY_VIOLATION, "refused"),
        "b": ("ok", "b", None, None),
    }
    calls: list[str] = []

    def runner(pid):
        calls.append(pid)
        return behaviors[pid]

    result, history = run_with_fallback(
        ["a", "b"],
        runner=runner,
        is_success=lambda r: r[0] == "ok",
        classify=lambda r: (r[2], r[3]),
    )
    assert calls == ["a"]   # fatal stops the loop
    assert result == ("fail", "a", FAILURE_POLICY_VIOLATION, "refused")
    assert len(history) == 1


def test_all_recoverable_failures() -> None:
    behaviors = {
        "a": ("fail", "a", FAILURE_AUTH_EXPIRED, "401"),
        "b": ("fail", "b", FAILURE_USAGE_LIMIT, "limit"),
    }

    def runner(pid):
        return behaviors[pid]

    result, history = run_with_fallback(
        ["a", "b"],
        runner=runner,
        is_success=lambda r: r[0] == "ok",
        classify=lambda r: (r[2], r[3]),
    )
    assert result == ("fail", "b", FAILURE_USAGE_LIMIT, "limit")
    assert len(history) == 2


def test_empty_list() -> None:
    result, history = run_with_fallback(
        [],
        runner=lambda pid: None,
        is_success=lambda r: True,
        classify=lambda r: (None, None),
    )
    assert result is None
    assert history == []
