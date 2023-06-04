import pytest


@pytest.mark.xfail(
    reason="This test should fail because it leaves a file behind.",
    raises=AssertionError,
)
def test_cleanup() -> None:
    with open("leftover.txt", "w") as f:
        f.write("This file is left behind.")
