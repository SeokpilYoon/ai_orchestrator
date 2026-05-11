"""Fallback executor — try providers in order until one succeeds.

Authoritative reference: docs/plan/03 DEVF-052.

The orchestrator iterates ``provider_ids`` and stops at the first success.
A failure causes a fallback ONLY when its ``failure_class`` is recoverable
(auth expired, usage limit, rate limit, timeout, command missing,
malformed output). Other failures are treated as fatal and short-circuit
the loop so the orchestrator can surface them.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from devforge.providers.base import (
    FAILURE_AUTH_EXPIRED,
    FAILURE_COMMAND_MISSING,
    FAILURE_MALFORMED_OUTPUT,
    FAILURE_RATE_LIMIT,
    FAILURE_TIMEOUT,
    FAILURE_USAGE_LIMIT,
)

FALLBACK_TRIGGERS: frozenset[str] = frozenset(
    {
        FAILURE_AUTH_EXPIRED,
        FAILURE_USAGE_LIMIT,
        FAILURE_RATE_LIMIT,
        FAILURE_TIMEOUT,
        FAILURE_COMMAND_MISSING,
        FAILURE_MALFORMED_OUTPUT,
    }
)


@dataclass
class FallbackEntry:
    provider: str
    failure_class: str | None
    error: str | None


T = TypeVar("T")


def run_with_fallback(
    provider_ids: list[str],
    runner: Callable[[str], T],
    is_success: Callable[[T], bool],
    classify: Callable[[T], tuple[str | None, str | None]],
) -> tuple[T | None, list[FallbackEntry]]:
    """Run ``runner(pid)`` for each provider until one succeeds.

    Args:
        provider_ids: ordered list of provider ids to try.
        runner: invokes one provider; returns the candidate result of type T.
        is_success: ``True`` if the result is acceptable.
        classify: returns ``(failure_class, error_msg)`` for unsuccessful results.

    Returns:
        ``(last_result, history)`` — ``last_result`` is the final successful
        result, or the last failed result if none succeeded.
    """
    if not provider_ids:
        return None, []

    history: list[FallbackEntry] = []
    last_result: T | None = None
    for pid in provider_ids:
        result = runner(pid)
        last_result = result
        if is_success(result):
            return result, history
        fc, err = classify(result)
        history.append(FallbackEntry(provider=pid, failure_class=fc, error=err))
        if fc not in FALLBACK_TRIGGERS:
            break  # fatal — do not try the next provider
    return last_result, history
