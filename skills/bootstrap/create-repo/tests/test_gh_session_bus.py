"""The conftest keeps the real ``gh`` from starting a session bus, and traps what still asks.

Each ``gh`` here calls a closed loopback port, so nothing reaches GitHub, and any bus it
asks for goes to a recording ``dbus-launch`` that starts nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CONFTEST = Path(__file__).resolve().parent / "conftest.py"
REPO_ROOT = Path(__file__).resolve().parents[4]
VARIABLE = "DBUS_SESSION_BUS_ADDRESS"
FAKE_HOST_CALL = ["gh", "api", "http://127.0.0.1:1/repos/owner/name"]

pytestmark = pytest.mark.skipif(
    shutil.which("gh") is None or sys.platform != "linux",
    reason="needs the real gh on Linux: no bus can start anywhere else",
)


class Recorded:
    """An inherited environment whose first ``dbus-launch`` records to its own file."""

    def __init__(self, root: Path, recording_dbus_launch) -> None:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.record = root / "asked"
        recording_dbus_launch(bin_dir, self.record)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_TOKEN": "guard-token",
            "GH_CONFIG_DIR": str(root / "gh-config"),
        }

    def asks_from_one_gh_call(self, env: dict[str, str]) -> int:
        self.record.unlink(missing_ok=True)
        subprocess.run(FAKE_HOST_CALL, env=env, capture_output=True, timeout=30)
        if not self.record.exists():
            return 0
        return len(self.record.read_text(encoding="utf-8").splitlines())


@pytest.fixture
def recorded(tmp_path, recording_dbus_launch) -> Recorded:
    """The recorded environment, once a copy without the variable shows ``gh`` still asks."""
    recorded = Recorded(tmp_path, recording_dbus_launch)
    control = {k: v for k, v in recorded.env.items() if k != VARIABLE}
    if not recorded.asks_from_one_gh_call(control):
        pytest.skip("this gh does not ask for a session bus, so no bus can leak")
    return recorded


def test_the_conftest_environment_keeps_gh_from_asking_for_a_bus(recorded):
    assert recorded.env.get(VARIABLE) == "disabled:"
    assert recorded.asks_from_one_gh_call(recorded.env) == 0


def run_scratch_session(root: Path, test_source: str) -> subprocess.CompletedProcess:
    """A nested pytest session over the real conftest, with no change to the ambient PATH."""
    root.mkdir()
    shutil.copy(CONFTEST, root / "conftest.py")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "test_scratch.py").write_text(
        f"import os\nimport subprocess\n\nimport pytest\n\n"
        f"CALL = {FAKE_HOST_CALL!r}\n\n\n"
        "def drive_gh(tmp_path, keep_variable=True):\n"
        '    env = {**os.environ, "GH_TOKEN": "guard-token"}\n'
        '    env["GH_CONFIG_DIR"] = str(tmp_path)\n'
        "    if not keep_variable:\n"
        f"        del env[{VARIABLE!r}]\n"
        "    subprocess.run(CALL, env=env, capture_output=True, timeout=30)\n\n\n"
        f"{test_source}",
        encoding="utf-8",
    )
    return subprocess.run(
        # Nx forces colour on, which would split the summary line with escapes.
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT),
            "python",
            "-m",
            "pytest",
            "-q",
            "--color=no",
            "-p",
            "no:cacheprovider",
            str(root),
        ],
        cwd=root,
        env={
            **{
                k: v
                for k, v in os.environ.items()
                if k not in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")
            },
            # Most of a nested session's cost is importing plugins this one never uses.
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_session_fails_on_the_test_that_drops_the_variable(recorded, tmp_path):
    done = run_scratch_session(
        tmp_path / "scratch",
        "def test_keeps_it(tmp_path):\n"
        "    drive_gh(tmp_path)\n\n\n"
        "def test_drops_it(tmp_path):\n"
        "    drive_gh(tmp_path, keep_variable=False)\n",
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "ERROR at teardown of test_drops_it" in done.stdout
    assert "at teardown of test_keeps_it" not in done.stdout
    assert "DBUS_SESSION_BUS_ADDRESS=disabled:" in done.stdout
    assert "2 passed, 1 error" in done.stdout


def test_a_session_fails_on_an_ask_no_test_teardown_saw(recorded, tmp_path):
    done = run_scratch_session(
        tmp_path / "scratch",
        '@pytest.fixture(scope="session", autouse=True)\n'
        "def asks_while_the_session_tears_down(tmp_path_factory):\n"
        "    yield\n"
        '    drive_gh(tmp_path_factory.mktemp("gh"), keep_variable=False)\n\n\n'
        "def test_passes():\n"
        "    pass\n",
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "1 passed" in done.stdout and "error" not in done.stdout.lower()
    assert "the real gh asked for a session bus 1 time(s)" in done.stdout
