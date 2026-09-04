"""Check the install-mdtablefix validator against its documented grammar.

The example-based tests pin the rejection messages. These check the shape of
the whole language: that the real Bash fragment accepts exactly the version
strings the README documents, and exactly the ``bin-dir`` values that are
absolute, bounded, free of parent-directory components, and free of the
runner's ``PATH`` separator. The oracle below is written from the
documentation, not from the fragment, so a fragment that drifts from what is
documented fails here.
"""

from __future__ import annotations

import re
import typing as typ

from _mdtablefix_manifest import step_by_name
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from composite_fragments import (
    ActionContext,
    FragmentEnvironment,
    ambient_env,
    bash_file_path,
    bash_path,
    require_posix_host,
    run_step,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

require_posix_host()

#: The version grammar the README documents: three components, no leading zeros.
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")

#: The documented ``bin-dir`` ceiling, in characters of the raw input.
_MAX_BIN_DIR = 240

#: Each Bash fragment is a process, so the example count is kept modest.
_PROFILE = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_well_formed_version = st.builds(
    lambda major, minor, patch: f"{major}.{minor}.{patch}",
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
)

_version_like = st.text(alphabet="0123456789.-abcv ", min_size=0, max_size=12)

_path_segment = st.text(
    alphabet="abcXY0123456789-_.:",
    min_size=1,
    max_size=8,
)

#: How a bin-dir candidate is rooted. ``absolute`` is rooted inside the test's
#: own sandbox: the property is about the documented grammar, not about whether
#: the host lets a process create a directory at the filesystem root.
_bin_dir_shape = st.tuples(
    st.sampled_from(["absolute", "home", "relative", "dot", "overlong"]),
    st.lists(_path_segment, min_size=1, max_size=4),
)


def _compose_bin_dir(shape: tuple[str, list[str]], sandbox: str) -> str:
    """Return the bin-dir candidate ``shape`` describes under ``sandbox``."""
    rooting, segments = shape
    tail = "/".join(segments)
    if rooting == "absolute":
        return f"{sandbox}/{tail}"
    if rooting == "home":
        return f"~/{tail}"
    if rooting == "dot":
        return f"./{tail}"
    if rooting == "overlong":
        return f"{sandbox}/{'a' * 240}"
    return tail


#: The earliest mdtablefix this action supports. Earlier releases publish
#: Linux archives only and declare binstall metadata cargo-binstall rejects,
#: so the platform list and the absent `--bin-dir` override are both true only
#: from here.
MINIMUM_VERSION = (0, 5, 1)


def _accepts_version(candidate: str) -> bool:
    """Return whether the documented grammar and floor admit ``candidate``.

    Two rules, not one. The grammar fixes the shape, three numeric components
    without leading zeros, and the floor fixes the range. A candidate has to
    satisfy both, which is what the validator does.
    """
    if _VERSION.fullmatch(candidate) is None:
        return False
    return tuple(int(part) for part in candidate.split(".")) >= MINIMUM_VERSION


def _expand_bin_dir(candidate: str, home: str) -> str | None:
    """Return ``candidate`` rooted as the documentation says, or ``None``.

    ``None`` means the value is neither absolute nor rooted at the home
    directory, which the documentation refuses outright.
    """
    if candidate == "~" or candidate.startswith("~/"):
        return home + candidate[1:]
    return candidate if candidate.startswith("/") else None


def _accepts_bin_dir(candidate: str, home: str) -> bool:
    """Return whether the documented rules admit ``candidate`` on a POSIX runner."""
    expanded = _expand_bin_dir(candidate, home)
    return (
        expanded is not None
        and "\r" not in candidate
        and "\n" not in candidate
        and len(candidate) <= _MAX_BIN_DIR
        and "/../" not in f"/{expanded}/"
        and ":" not in expanded
    )


def _run_validation(
    home: Path,
    workspace: Path,
    inputs: dict[str, str],
) -> tuple[int, str]:
    """Run the validation fragment and return its exit status and diagnostics."""
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    context = ActionContext(
        inputs=inputs,
        runner_os="Linux",
        runner_arch="X64",
        action_path=bash_path(workspace),
    )
    environment = FragmentEnvironment(
        base_env={
            **ambient_env(),
            "HOME": bash_path(home),
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "GITHUB_PATH": bash_file_path(workspace / "github-path"),
            "GITHUB_STEP_SUMMARY": bash_file_path(workspace / "step-summary"),
        },
        cwd=workspace,
        output_dir=workspace / "outputs",
    )
    step = step_by_name("Validate mdtablefix inputs")
    process = run_step(step, context, environment, "validate-output")
    return process.returncode, process.stderr


@given(candidate=st.one_of(_well_formed_version, _version_like))
@_PROFILE
def test_version_grammar_is_exactly_the_documented_one(
    tmp_path_factory: pytest.TempPathFactory,
    candidate: str,
) -> None:
    """Verify the validator admits a version string iff the grammar does."""
    root = tmp_path_factory.mktemp("version")
    status, _ = _run_validation(
        root / "home",
        root / "workspace",
        {"version": candidate, "binstall-version": "1.22.0", "bin-dir": "~/.local/bin"},
    )

    assert (status == 0) is _accepts_version(candidate), (
        f"the validator returned {status} for version {candidate!r}, which the "
        f"documented grammar {'admits' if _accepts_version(candidate) else 'rejects'}"
    )


@given(shape=_bin_dir_shape)
@_PROFILE
def test_bin_dir_rules_are_exactly_the_documented_ones(
    tmp_path_factory: pytest.TempPathFactory,
    shape: tuple[str, list[str]],
) -> None:
    """Verify the validator admits a bin-dir iff the documented rules do."""
    root = tmp_path_factory.mktemp("bin-dir")
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    candidate = _compose_bin_dir(shape, bash_path(root))
    admitted = _accepts_bin_dir(candidate, bash_path(home))
    status, _ = _run_validation(
        home,
        root / "workspace",
        {"version": "0.5.1", "binstall-version": "1.22.0", "bin-dir": candidate},
    )

    assert (status == 0) is admitted, (
        f"the validator returned {status} for bin-dir {candidate!r}, which the "
        f"documented rules {'admit' if admitted else 'reject'}"
    )


@given(shape=_bin_dir_shape, version=st.one_of(_well_formed_version, _version_like))
@_PROFILE
def test_a_rejection_is_always_annotated_and_measured(
    tmp_path_factory: pytest.TempPathFactory,
    shape: tuple[str, list[str]],
    version: str,
) -> None:
    """Verify no rejection is silent, whichever rule refused the input."""
    root = tmp_path_factory.mktemp("annotated")
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace"
    candidate = _compose_bin_dir(shape, bash_path(root))
    status, stderr = _run_validation(
        home,
        workspace,
        {
            "version": version,
            "binstall-version": "1.22.0",
            "bin-dir": candidate,
        },
    )
    summary = workspace / "step-summary"
    emitted = (
        summary.read_text(encoding="utf-8").splitlines() if summary.exists() else []
    )
    context = f"version {version!r} with bin-dir {candidate!r}"

    if status == 0:
        assert emitted == [], f"an accepted input, {context}, emitted {emitted}"
        return
    assert emitted.count("install-mdtablefix.result=invalid-input") == 1, (
        f"{context} was rejected; expected exactly one invalid-input metric but "
        f"the summary held {emitted}"
    )
    annotations = [
        line
        for line in stderr.splitlines()
        if line.startswith("::error title=Invalid mdtablefix input::")
    ]
    assert len(annotations) == 1, (
        f"{context} was rejected; expected exactly one annotation but stderr held "
        f"{stderr!r}"
    )
