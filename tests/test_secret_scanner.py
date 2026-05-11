from __future__ import annotations

from devforge.evaluators.secret_scanner import scan_diff_and_logs, scan_text


def test_openai_key_detected() -> None:
    fake = "sk-" + "A" * 30
    hits = scan_text(f"key = '{fake}'", "diff")
    assert any(h.kind == "openai_api_key" for h in hits)


def test_anthropic_key_detected() -> None:
    fake = "sk-ant-" + "A" * 30
    hits = scan_text(f"x = '{fake}'", "diff")
    assert any(h.kind == "anthropic_api_key" for h in hits)


def test_private_key_marker() -> None:
    blob = "-----BEGIN RSA PRIVATE KEY-----\nbla\n-----END RSA PRIVATE KEY-----"
    hits = scan_text(blob, "diff")
    assert any(h.kind == "private_key" for h in hits)


def test_env_file_modified() -> None:
    r = scan_diff_and_logs(diff_text="", changed_files=[".env"])
    assert r.env_file_modified
    assert r.has_secret


def test_env_value_in_diff() -> None:
    r = scan_diff_and_logs(diff_text="OPENAI_API_KEY=foo\n")
    assert r.env_file_modified


def test_no_secret_clean() -> None:
    r = scan_diff_and_logs(diff_text="just code\n", changed_files=["src/x.py"])
    assert not r.has_secret


def test_secret_values_not_stored() -> None:
    fake = "sk-" + "B" * 30
    r = scan_diff_and_logs(diff_text=f"x = '{fake}'")
    # the SecretHit should only record kind/location/line, never the value
    for h in r.hits:
        for attr in (h.kind, h.location):
            assert fake not in attr
