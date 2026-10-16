"""Prove wrapper environment scope without bypassing managed compilation.

Bash-only observers exercise the guide's opt-out forms. Real Cargo builds use
an observing workspace spy nested inside the unchanged inherited RUSTC_WRAPPER.
It delegates any inherited workspace wrapper, retaining that compiler chain,
RUSTDOC and the shared Cargo cache. Opt-out syntax remains Bash-only evidence.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path, PureWindowsPath

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"
REPOSITORY = Path(__file__).resolve().parents[4]

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None or shutil.which("bash") is None,
    reason="needs cargo and bash",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a crate and a spy that preserves the inherited compiler chain."""
    crate = tmp_path / "crate"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.0.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (crate / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            echo called >> "$WRAPPER_LOG"
            if [[ -n "$INHERITED_WORKSPACE_WRAPPER" ]]; then
                exec "$INHERITED_WORKSPACE_WRAPPER" "$@"
            fi
            exec "$@"
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return crate


def _reject_drive_relative_target(inherited: str) -> None:
    """Reject a target whose meaning depends on Windows' per-drive current directory.

    ```python
    _reject_drive_relative_target("build")  # accepted: Cargo-cwd relative
    _reject_drive_relative_target("D:/build")  # accepted: absolute on Windows
    _reject_drive_relative_target("D:build")  # raises ValueError
    ```
    """
    windows_path = PureWindowsPath(inherited)
    if windows_path.drive and not windows_path.root:
        message = f"drive-relative Cargo target is ambiguous: {inherited}"
        raise ValueError(message)


def _target_root(project: Path) -> Path:
    """Preserve Cargo-relative inherited roots, otherwise allocate outside /tmp.

    ```python
    # With CARGO_TARGET_DIR=/build/shared in the child environment:
    _target_root(Path("/fixtures/probe"))  # Path("/build/shared")
    ```
    """
    inherited = os.environ.get("CARGO_TARGET_DIR")
    if inherited:
        _reject_drive_relative_target(inherited)
        root = Path(inherited)
        root = root if root.is_absolute() else project / root
    else:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        parent = REPOSITORY / "target" / "pytest-integration" / worker
        parent.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="wrapper-", dir=parent))
    if root.resolve().is_relative_to(
        Path("/tmp").resolve(),  # noqa: S108 - fixed rejection boundary, no allocation.
    ):
        message = f"integration Cargo target must be outside /tmp: {root}"
        raise ValueError(message)
    return root


def _workspace_environment(inherited: dict[str, str], spy: Path) -> dict[str, str]:
    """Instrument the workspace wrapper without replacing outer admission.

    ```python
    child = _workspace_environment(
        {"RUSTC_WRAPPER": "/managed/admission", "RUSTDOC": "/managed/docs"},
        Path("/fixtures/workspace-spy.sh"),
    )
    # RUSTC_WRAPPER and RUSTDOC retain their exact inherited values; only the
    # workspace wrapper becomes the spy, with its previous value saved for delegation.
    ```
    """
    return {
        **inherited,
        "INHERITED_WORKSPACE_WRAPPER": inherited.get("RUSTC_WORKSPACE_WRAPPER", ""),
        "RUSTC_WORKSPACE_WRAPPER": str(spy),
    }


def _build(project: Path, *, scoped: bool) -> int:
    """Count workspace spy calls inside the unchanged inherited outer wrapper.

    ```python
    _build(project, scoped=True)  # positive count after successful Cargo build
    ```
    """
    log = project.parent / "wrapper.log"
    log.write_text("", encoding="utf-8")
    wrapper = project.parent / "wrapper.sh"
    inherited = dict(os.environ)
    environment = {
        **_workspace_environment(inherited, wrapper),
        "WRAPPER_LOG": str(log),
        "CARGO_TARGET_DIR": str(_target_root(project)),
    }
    script = "cargo build -q --offline"
    if scoped:
        # Restore only the workspace setting before the shell scopes its spy.
        if "RUSTC_WORKSPACE_WRAPPER" in inherited:
            environment["RUSTC_WORKSPACE_WRAPPER"] = inherited[
                "RUSTC_WORKSPACE_WRAPPER"
            ]
        else:
            environment.pop("RUSTC_WORKSPACE_WRAPPER", None)
        script = f"RUSTC_WORKSPACE_WRAPPER={shlex.quote(str(wrapper))} {script}"
    completed = subprocess.run(  # noqa: S603,TID251 - exercise real Cargo through inherited admission.
        ["bash", "-c", script],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=project,
        env=environment,
        text=True,
        # Shared Cargo locks and compile admission may outlast a fixed deadline.
        timeout=None,
    )
    assert completed.returncode == 0, completed.stderr
    return len(log.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize(
    "managed",
    [False, True],
    ids=["no-workspace-wrapper", "inherited-workspace-wrapper"],
)
def test_spy_delegates_without_replacing_the_compiler(
    project: Path, *, managed: bool
) -> None:
    """Shell-only doubles prove inherited workspace delegation and compiler argv."""
    compiler = project.parent / "compiler-observer.sh"
    compiler.write_text(
        '#!/usr/bin/env bash\nprintf "compiler:%s\\n" "$*"\n', encoding="utf-8"
    )
    compiler.chmod(0o755)
    manager = project.parent / "manager-observer.sh"
    manager.write_text(
        '#!/usr/bin/env bash\nprintf "manager\\n"\nexec "$@"\n', encoding="utf-8"
    )
    manager.chmod(0o755)
    completed = subprocess.run(  # noqa: S603,TID251 - shell-only forwarding doubles never compile.
        [str(project.parent / "wrapper.sh"), str(compiler), "argument with spaces"],
        env={
            **os.environ,
            "INHERITED_WORKSPACE_WRAPPER": str(manager) if managed else "",
            "WRAPPER_LOG": str(project.parent / "wrapper.log"),
        },
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    expected = "manager\n" if managed else ""
    assert completed.stdout == expected + "compiler:argument with spaces\n"


class TestDecliningAnInheritedWrapper:
    """Observe opt-out syntax without invoking Cargo or a compiler."""

    @pytest.mark.parametrize(
        ("script", "expected"),
        [
            ("observe", "inherited"),
            ("RUSTC_WRAPPER='' observe", ""),
            ("env RUSTC_WRAPPER= bash -c 'printf %s \"$RUSTC_WRAPPER\"'", ""),
            ('RUSTC_WRAPPER=scoped observe; printf "|"; observe', "scoped|inherited"),
        ],
        ids=["inherited", "step-empty", "sudo-env-empty", "command-and-restoration"],
    )
    def test_environment_scope(self, script: str, expected: str) -> None:
        """Bash exposes the documented value and restores command assignments."""
        completed = subprocess.run(  # noqa: S603,TID251 - Bash observer never launches Cargo/rustc.
            ["bash", "-c", 'observe() { printf %s "$RUSTC_WRAPPER"; }; ' + script],  # noqa: S607
            env={**os.environ, "RUSTC_WRAPPER": "inherited"},
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == expected

    @pytest.mark.parametrize(
        "scoped", [False, True], ids=["workspace-env", "workspace-command-scoped"]
    )
    def test_real_cargo_uses_the_delegating_spy(
        self, project: Path, *, scoped: bool
    ) -> None:
        """Workspace spies observe compilation while outer admission stays intact."""
        assert _build(project, scoped=scoped) > 0


class TestTheGuideNamesBothPatterns:
    """The README is where a caller finds them, so it must say them exactly."""

    @pytest.mark.parametrize(
        "text",
        [
            "export-rustc-wrapper: 'false'",
            "RUSTC_WRAPPER: ''",
            "sudo -E env RUSTC_WRAPPER= make test",
            "sccache-path",
        ],
    )
    def test_the_readme_documents(self, text: str) -> None:
        """Each documented form appears verbatim."""
        assert text in README.read_text(encoding="utf-8")


@pytest.mark.parametrize("target", ["D:build", "C:", "z:relative/path"])
def test_drive_relative_target_is_rejected(target: str) -> None:
    """The wrapper fixture rejects Windows per-drive cwd routing on every host."""
    with pytest.raises(ValueError, match="drive-relative Cargo target is ambiguous"):
        _reject_drive_relative_target(target)


@pytest.mark.parametrize("target", ["build", "../build", "D:/build", "/build"])
def test_ordinary_target_shapes_are_preserved(target: str) -> None:
    """Rejecting drive-relative paths leaves ordinary and absolute paths valid."""
    _reject_drive_relative_target(target)


@pytest.mark.parametrize("workspace", [None, "", "/previous/workspace-wrapper"])
def test_workspace_spy_preserves_outer_admission(workspace: str | None) -> None:
    """Only workspace instrumentation changes; manager, docs and cache remain exact."""
    inherited = {
        "RUSTC_WRAPPER": "/managed/build-limits-rustc",
        "RUSTDOC": "/managed/build-limits-rustdoc",
        "CARGO_HOME": "/shared/cargo",
    }
    if workspace is not None:
        inherited["RUSTC_WORKSPACE_WRAPPER"] = workspace
    before = inherited.copy()
    spy = Path("workspace-spy.sh")
    child = _workspace_environment(inherited, spy)
    assert child == {
        **before,
        "RUSTC_WORKSPACE_WRAPPER": str(spy),
        "INHERITED_WORKSPACE_WRAPPER": "" if workspace is None else workspace,
    }
    assert child["RUSTC_WRAPPER"] == before["RUSTC_WRAPPER"]
    assert child["RUSTDOC"] == before["RUSTDOC"]
    assert inherited == before
