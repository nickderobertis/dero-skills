"""Put this ``tests/`` directory on ``sys.path`` for the whole subtree.

The ``e2e/`` suites reuse the repo-builder fixtures defined in their sibling
in-process test modules (e.g. ``test_check_repo_baseline``); pytest's prepend
import mode only adds each test file's own directory, so without this the
``e2e/`` files could not import ``test_check_repo_baseline``. Loading a conftest
here makes ``tests/`` importable across both tiers, keeping the builders defined
once instead of duplicated into the e2e files.

It also stops the real ``gh`` leaving a session bus behind. Holding no token for
the host a call targets, ``gh`` asks the keyring, and on Linux that autolaunches
a ``dbus-daemon`` which outlives the test (``references/ci.md``, "Tests that drive
the real ``gh``"). ``DBUS_SESSION_BUS_ADDRESS=disabled:`` answers that for every
environment inheriting ``os.environ``; one built from scratch sets it itself.

A recording ``dbus-launch`` first on ``PATH`` catches whatever still asks: it starts
nothing, the test that asked fails, and so does the session. An environment with
its own ``PATH`` that can reach the real ``gh`` puts ``gh_session_bus_trap`` on it.
"""

import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

# `_stealth_env` in skilltest/ takes both of these back off the model's environment.
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
_TRAP = Path(tempfile.mkdtemp(prefix="gh-session-bus-trap-"))
_RECORD = _TRAP / "asked"
_answered = 0
_session_failure = ""


def _write_recording_dbus_launch(directory: Path, record: Path) -> None:
    program = directory / "dbus-launch"
    # Builtins only, because a hermetic PATH may hold nothing else.
    program.write_text(
        f"#!/bin/sh\nprintf 'asked\\n' >> '{record}'\nexit 1\n", encoding="utf-8"
    )
    program.chmod(program.stat().st_mode | stat.S_IXUSR)


_write_recording_dbus_launch(_TRAP, _RECORD)
os.environ["PATH"] = f"{_TRAP}{os.pathsep}{os.environ.get('PATH', '')}"


def _times_asked() -> int:
    if not _RECORD.exists():
        return 0
    return len(_RECORD.read_text(encoding="utf-8").splitlines())


def _remedy(times: int) -> str:
    return (
        f"the real gh asked for a session bus {times} time(s); give the environment "
        "handed to it DBUS_SESSION_BUS_ADDRESS=disabled: (conftest.py sets it on "
        "os.environ, an environment built from scratch must set it itself)"
    )


@pytest.fixture(scope="session")
def gh_session_bus_trap() -> str:
    """The directory holding this session's recording ``dbus-launch``."""
    return str(_TRAP)


@pytest.fixture(scope="session")
def recording_dbus_launch() -> Callable[[Path, Path], None]:
    """Write a recording ``dbus-launch`` into a directory, recording to a given file."""
    return _write_recording_dbus_launch


@pytest.fixture(autouse=True)
def _fail_the_test_that_asked_for_a_session_bus() -> Iterator[None]:
    yield
    global _answered
    asked = _times_asked()
    if asked > _answered:
        new = asked - _answered
        _answered = asked
        pytest.fail(_remedy(new), pytrace=False)


def pytest_sessionfinish(session: pytest.Session) -> None:
    # Also reaches what no test's teardown saw, such as a session fixture's own teardown.
    global _session_failure
    asked = _times_asked()
    shutil.rmtree(_TRAP, ignore_errors=True)
    if asked:
        _session_failure = _remedy(asked)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter) -> None:
    if _session_failure:
        terminalreporter.section("session bus", red=True)
        terminalreporter.line(_session_failure, red=True)
