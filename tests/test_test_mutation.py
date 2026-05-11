from __future__ import annotations

from devforge.evaluators.test_mutation_checker import check_test_mutation


def test_skip_added() -> None:
    diff = "+    @pytest.mark.skip\n+    def test_x(): ...\n"
    r = check_test_mutation(diff, ["tests/test_x.py"])
    assert r.suspicious_changes
    assert "tests/test_x.py" in r.weakened_tests


def test_assert_weakened() -> None:
    diff = "+    assert True  # always passes\n"
    r = check_test_mutation(diff, ["tests/test_x.py"])
    assert r.suspicious_changes


def test_assert_or_true() -> None:
    diff = "+    assert foo() == 1 or True\n"
    r = check_test_mutation(diff, ["tests/test_x.py"])
    assert r.suspicious_changes


def test_clean_diff() -> None:
    diff = "+    assert add(1, 2) == 3\n"
    r = check_test_mutation(diff, ["tests/test_x.py"])
    assert not r.has_concern
