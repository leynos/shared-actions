"""Guard the bounded cmd-mox IPC retry attached to the ``shell_stubs`` fixture.

``conftest.py`` in this directory attaches ``pytest.mark.flaky(reruns=1)`` to
every collected item whose ``fixturenames`` contain ``shell_stubs``, so that a
single cmd-mox IPC race cannot fail a whole CI job (leynos/cmd-mox#256). That
hook changes test *execution*, which nothing else in the suite covers: the
``shell_stubs``-backed tests in ``test_scripts.py`` and
``test_generate_coverage_feature_selection.py`` pass whether or not the hook
exists, so they cannot notice if it is deleted, made a no-op, or widened to
mark unrelated items.

This module tests the retry policy itself, at three levels:

1. **Decision** -- load the real ``conftest.py`` by path and call its
   ``pytest_collection_modifyitems`` with stand-in items, pinning the marker
   name, the ``reruns`` value, and the matching rule without needing pytest's
   collection to run.
2. **Reach** -- collect ``.github/actions`` in a child pytest running a
   reporting plugin, then assert that the items the hook marked are exactly the
   items that request ``shell_stubs``. This proves the retry is scoped rather
   than suite-wide, and it exercises the hook against pytest's real collection.
3. **Consequences** -- run small suites in a child pytest and assert on what
   ``pytest-rerunfailures`` actually did: a transient failure in a
   ``shell_stubs`` test is retried once and then passes, a deterministic
   failure in one still fails after its retry, and failures in tests that do
   not request the fixture are never retried at all.

The marker name is load-bearing and cannot be renamed: ``pytest-rerunfailures``
resolves it with a literal ``get_closest_marker("flaky")`` and silently ignores
any other name.
``test_the_hook_uses_the_marker_name_rerunfailures_actually_reads`` checks that
literal against the installed plugin's own source, so an upstream rename fails
here loudly instead of quietly disabling every retry.

The child runs are hermetic: they use this interpreter and write only under
``tmp_path``. None contacts the network. The one real-suite child run is a
``--collect-only`` report, which executes no test.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import textwrap
import typing as typ
from pathlib import Path

from plumbum import local

from cmd_utils_importer import import_cmd_utils
from test_support.ansi import strip_ansi
from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:
    from types import ModuleType

    import pytest

    from cmd_utils import RunResult
else:
    RunResult = import_cmd_utils().RunResult

_TESTS_DIRECTORY = Path(__file__).resolve().parent
_ACTIONS_DIRECTORY = _TESTS_DIRECTORY.parents[1]
_REPOSITORY_ROOT = _TESTS_DIRECTORY.parents[3]
CONFTEST_PATH = _TESTS_DIRECTORY / "conftest.py"

# The plugin reads this literal name; see
# ``test_the_hook_uses_the_marker_name_rerunfailures_actually_reads``.
_RETRY_MARKER_NAME = "flaky"
_EXPECTED_RERUNS = 1

# Generous in absolute terms -- the child runs measured under a second each --
# but bounded, so a child that hangs fails the test instead of the job.
_CHILD_PYTEST_TIMEOUT = 120


def _load_real_conftest() -> ModuleType:
    """Load this directory's ``conftest.py`` by path under a fresh identity.

    Importing it as ``conftest`` would collide with the conftest pytest has
    already put in ``sys.modules``, so the spec is given its own name. The
    ``GITHUB_ACTION_PATH`` default mirrors the root conftest's
    ``_pin_action_path``; under pytest it is already set, so ``setdefault`` is
    only a fallback for a direct import.
    """
    os.environ.setdefault(
        "GITHUB_ACTION_PATH", str(_REPOSITORY_ROOT / ".github" / "actions")
    )
    for entry in (str(_REPOSITORY_ROOT), str(_TESTS_DIRECTORY)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    spec = importlib.util.spec_from_file_location(
        "shell_stubs_retry_conftest", CONFTEST_PATH
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import failure
        message = f"could not load {CONFTEST_PATH}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StandInItem:
    """Minimal stand-in for a collected item, recording added markers.

    The hook only reads ``fixturenames`` and calls ``add_marker``, so pinning
    its decision needs nothing else from pytest's real ``Item``.
    """

    def __init__(self, fixturenames: typ.Sequence[str]) -> None:
        self.fixturenames = list(fixturenames)
        self.added_markers: list[pytest.MarkDecorator] = []

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        """Record ``marker`` as if pytest had attached it to this item."""
        self.added_markers.append(marker)

    def marker_named(self, name: str) -> pytest.MarkDecorator | None:
        """Return the last marker added under ``name``, or ``None``."""
        matching = [marker for marker in self.added_markers if marker.name == name]
        return matching[-1] if matching else None


def _child_environment() -> dict[str, str]:
    """Return the environment overrides every child pytest run needs.

    ``GITHUB_ACTION_PATH`` and ``PYTHONPATH`` are what let a child import this
    directory's conftest and its helpers, which the root conftest normally
    supplies. ``FORCE_COLOR`` is cleared so the capture stays plain text.
    """
    search_path = os.pathsep.join([str(_TESTS_DIRECTORY), str(_REPOSITORY_ROOT)])
    existing = os.environ.get("PYTHONPATH", "")
    return {
        "PYTHONPATH": (
            f"{search_path}{os.pathsep}{existing}" if existing else search_path
        ),
        "GITHUB_ACTION_PATH": os.environ.get(
            "GITHUB_ACTION_PATH", str(_REPOSITORY_ROOT / ".github" / "actions")
        ),
        "FORCE_COLOR": "",
        "PYTHONIOENCODING": "utf-8",
    }


def _run_child_pytest(
    directory: Path,
    *args: str,
    extra_environment: dict[str, str] | None = None,
) -> RunResult:
    """Run this interpreter's pytest over ``directory`` and capture the result.

    ``--color=no`` and ``-p no:cacheprovider`` keep the output plain and stop
    the child writing a cache, and ``-p rerunfailures`` loads the plugin
    explicitly so a missing or renamed one is an error rather than silence. A
    non-zero exit is returned, not raised, because some callers run suites that
    are meant to fail; a child that hangs raises ``ProcessTimedOut`` instead of
    stalling the job, because ``run_plumbum_command`` re-raises that rather
    than folding it into a ``RunResult``.
    """
    environment = _child_environment()
    if extra_environment:
        environment |= extra_environment
    argv = [
        "-m",
        "pytest",
        str(directory),
        "--color=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "rerunfailures",
        *args,
    ]
    return run_plumbum_command(
        local[sys.executable][argv],
        method="run",
        env=environment,
        timeout=_CHILD_PYTEST_TIMEOUT,
    )


def test_the_hook_marks_only_shell_stubs_items_with_one_rerun() -> None:
    """The hook marks ``shell_stubs`` items, and only those, with ``reruns=1``.

    Both directions matter: marking an item that does not request the fixture
    would retry an unrelated failure, while failing to mark one leaves it
    exposed to the race.
    """
    hook = _load_real_conftest().pytest_collection_modifyitems
    requesting = _StandInItem(["cmd_mox", "shell_stubs", "monkeypatch"])
    unrelated = _StandInItem(["tmp_path", "monkeypatch"])
    bare = _StandInItem([])

    hook([requesting, unrelated, bare])

    marker = requesting.marker_named(_RETRY_MARKER_NAME)
    assert marker is not None
    assert marker.kwargs == {"reruns": _EXPECTED_RERUNS}
    assert unrelated.added_markers == []
    assert bare.added_markers == []


def test_the_hook_matches_the_fixture_name_whenever_it_appears() -> None:
    """Membership in ``fixturenames`` decides; position and near-misses do not.

    A prefix test, or a substring test over a joined list, would satisfy the
    test above while both missing a real item that lists the fixture last and
    falsely matching a differently named fixture.
    """
    hook = _load_real_conftest().pytest_collection_modifyitems
    late = _StandInItem(["request", "monkeypatch", "cmd_mox", "shell_stubs"])
    lookalikes = _StandInItem(["shell_stubs_backup", "shell_stubs_fixture"])

    hook([late, lookalikes])

    assert late.marker_named(_RETRY_MARKER_NAME) is not None
    assert lookalikes.added_markers == []


def test_the_hook_uses_the_marker_name_rerunfailures_actually_reads() -> None:
    """``flaky`` is the name the installed plugin looks up, per its own source.

    ``pytest-rerunfailures`` resolves the marker with a literal
    ``item.get_closest_marker("flaky")``. Any other name is ignored without
    warning, so renaming it here would disable every retry while every other
    test in this module still passed.
    """
    spec = importlib.util.find_spec("pytest_rerunfailures")
    assert spec is not None, "pytest-rerunfailures is not installed"
    assert spec.origin is not None
    source = Path(spec.origin).read_text(encoding="utf-8")

    assert f'get_closest_marker("{_RETRY_MARKER_NAME}")' in source


_REPORT_PLUGIN_SOURCE = textwrap.dedent(
    '''
    """Report, per collected item, what the retry hook decided."""

    import json
    import os

    import pytest


    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(config, items):
        """Write one record per item to the path in RETRY_REPORT_PATH.

        ``trylast`` guarantees this reads the item state after the conftest
        hook has run, regardless of plugin registration order.
        """
        report = []
        for item in items:
            marker = item.get_closest_marker("flaky")
            report.append(
                {
                    "nodeid": item.nodeid,
                    "requests_shell_stubs": "shell_stubs"
                    in getattr(item, "fixturenames", ()),
                    "marked": marker is not None,
                    "reruns": None if marker is None else marker.kwargs.get("reruns"),
                }
            )
        with open(os.environ["RETRY_REPORT_PATH"], "w", encoding="utf-8") as handle:
            json.dump(report, handle)
    '''
)


def test_the_retry_reaches_exactly_the_tests_that_request_the_fixture(
    tmp_path: Path,
) -> None:
    """Collecting the whole actions tree marks the requesters and nothing else.

    This is the scoping guarantee, measured against pytest's real collection:
    every marked item requests ``shell_stubs``, every item that requests it is
    marked, no item carries any other rerun count, and most of the tree is left
    alone. The assertion is a set comparison rather than a fixed count, so
    adding or renaming tests does not make it stale.
    """
    plugin = tmp_path / "retry_report_plugin.py"
    plugin.write_text(_REPORT_PLUGIN_SOURCE, encoding="utf-8")
    report_path = tmp_path / "retry_report.json"

    result = _run_child_pytest(
        _ACTIONS_DIRECTORY,
        "--collect-only",
        "-q",
        "-p",
        "retry_report_plugin",
        extra_environment={
            "PYTHONPATH": os.pathsep.join(
                [str(tmp_path), _child_environment()["PYTHONPATH"]]
            ),
            "RETRY_REPORT_PATH": str(report_path),
        },
    )
    assert result.returncode == 0, result.stderr
    assert report_path.is_file(), result.stdout

    report = typ.cast(
        "list[dict[str, typ.Any]]",
        json.loads(report_path.read_text(encoding="utf-8")),
    )
    requesting = {
        record["nodeid"] for record in report if record["requests_shell_stubs"]
    }
    marked = {record["nodeid"] for record in report if record["marked"]}

    assert requesting, "no collected item requests shell_stubs"
    assert marked == requesting
    assert len(marked) < len(report), "the retry must not cover the whole suite"
    assert {record["reruns"] for record in report if record["marked"]} == {
        _EXPECTED_RERUNS
    }
    assert all(record["reruns"] is None for record in report if not record["marked"])


_SUITE_CONFTEST_SOURCE = textwrap.dedent(
    '''
    """Apply the real retry hook to stand-in fixtures.

    The hook keys on the fixture *name*, so a dummy ``shell_stubs`` fixture is
    enough to exercise it; the real one needs cmd-mox and a live IPC server,
    which would put the race back into the test that guards against it.
    """

    import importlib.util
    import os
    from pathlib import Path

    import pytest

    _spec = importlib.util.spec_from_file_location(
        "real_retry_conftest",
        Path(os.environ["RETRY_CONFTEST_PATH"]),
    )
    _real = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_real)

    pytest_collection_modifyitems = _real.pytest_collection_modifyitems


    @pytest.fixture
    def shell_stubs():
        """Stand in for the real StubManager-backed fixture."""
        return "stub-manager"


    @pytest.fixture
    def cmd_mox():
        """Stand in for the cmd-mox controller fixture."""
        return "cmd-mox"
    '''
)


def _run_suite(tmp_path: Path, tests_source: str) -> str:
    """Run a throwaway suite in a child pytest and return its summary line.

    Only the summary line is returned. The full output carries a ``plugins:``
    line naming ``rerunfailures``, so asserting ``"rerun" not in`` the whole
    capture would fail for every run, including the ones where nothing was
    retried. The summary is where pytest reports reruns and pass/fail counts,
    so it is also the only part these tests should read.
    """
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(_SUITE_CONFTEST_SOURCE, encoding="utf-8")
    (suite / "test_policy.py").write_text(
        textwrap.dedent(tests_source), encoding="utf-8"
    )

    result = _run_child_pytest(
        suite,
        extra_environment={"RETRY_CONFTEST_PATH": str(CONFTEST_PATH)},
    )
    output = strip_ansi(result.stdout)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    assert lines, f"no pytest output:\n{result.stdout}\n{result.stderr}"
    return lines[-1].strip("= ").strip()


_TRANSIENT_FAILURE_SUITE = '''
"""A failure that clears on the retry, mimicking a dropped IPC reply."""

_attempts = 0


def test_transient_failure_is_retried(shell_stubs):
    """Fail on the first attempt, pass on the second."""
    global _attempts
    _attempts += 1
    assert _attempts > 1
'''

_DETERMINISTIC_FAILURE_SUITE = '''
"""A genuine regression: it must still fail, retry or not."""


def test_deterministic_failure_still_fails(shell_stubs):
    """Fail identically on both attempts."""
    assert 1 == 2
'''

_UNRELATED_FAILURE_SUITE = '''
"""A failure outside the fixture's reach: it must never be retried."""


def test_unrelated_failure_is_not_retried():
    """Fail without requesting the fixture at all."""
    assert 1 == 2
'''


def test_a_transient_failure_is_retried_once_and_then_passes(tmp_path: Path) -> None:
    """One IPC race must not fail the job: the retry turns the failure green.

    The attempt counter is a module global, so it survives into the rerun while
    staying invisible to the real suite.
    """
    output = _run_suite(tmp_path, _TRANSIENT_FAILURE_SUITE)

    assert "1 passed" in output
    assert "1 rerun" in output
    assert "failed" not in output


def test_a_deterministic_failure_still_fails_after_being_retried(
    tmp_path: Path,
) -> None:
    """The retry must not mask a real regression, only absorb a race.

    The same assertion fails on both attempts, so the run ends red and the
    rerun is visible in the summary rather than hidden.
    """
    output = _run_suite(tmp_path, _DETERMINISTIC_FAILURE_SUITE)

    assert "1 failed" in output
    assert "1 rerun" in output
    assert "passed" not in output


def test_a_failure_outside_the_fixture_is_never_retried(tmp_path: Path) -> None:
    """Scoping is what stops the retry from covering unrelated regressions.

    The test does not request ``shell_stubs``, so it must report a single plain
    failure with no rerun anywhere in the run.
    """
    output = _run_suite(tmp_path, _UNRELATED_FAILURE_SUITE)

    assert "1 failed" in output
    assert "rerun" not in output
