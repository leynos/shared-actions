"""Tests for coverage utility scripts."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

from cmd_utils import run_completed_process

if typ.TYPE_CHECKING:  # pragma: no cover - type hints only
    import subprocess
    from types import ModuleType

    from shellstub import StubManager


def _exit_code(exc: BaseException) -> int | None:
    """Extract an exit code from Typer or SystemExit exceptions."""
    exit_code = getattr(exc, "exit_code", None)
    if exit_code is None:
        exit_code = getattr(exc, "code", None)
    return exit_code


def run_script(
    script: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` via ``uv`` with ``env`` and return the completed process."""
    command = local["uv"]["run", "--script", str(script)]
    if args:
        command = command[list(args)]
    root = Path(__file__).resolve().parents[4]
    merged = {**os.environ, **env}
    current_pp = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = (
        f"{root}{os.pathsep}{current_pp}" if current_pp else str(root)
    )
    merged["PYTHONIOENCODING"] = "utf-8"
    return run_completed_process(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
    )


def _load_module(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> ModuleType:
    """Import ``name`` from the ``scripts`` directory with real dependencies."""
    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    root_dir = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(script_dir)
    monkeypatch.syspath_prepend(root_dir)
    for module_name in (name, "coverage_parsers"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    import importlib as _importlib  # ensure fresh module state for reloads

    _importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(name, script_dir / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_rust_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_rust`` module for testing."""
    return _load_module(monkeypatch, "run_rust")


def _make_fake_cargo(
    stdout: str | typ.TextIO | None,
    stderr: str | typ.TextIO | None,
    *,
    returncode: int = 0,
    track_lifecycle: bool = False,
) -> object:
    """Return a fake ``cargo`` object yielding the given streams."""

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = (
                stdout
                if hasattr(stdout, "readline")
                else None
                if stdout is None
                else io.StringIO(stdout)
            )
            self.stderr = (
                stderr
                if hasattr(stderr, "readline")
                else None
                if stderr is None
                else io.StringIO(stderr)
            )
            self.killed = False
            self.waited = False

        def kill(self) -> None:
            if track_lifecycle:
                self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            if track_lifecycle:
                self.waited = True
            return returncode

    class FakeCargo:
        def __init__(self) -> None:
            self.last_proc: FakeProc | None = None

        def __getitem__(self, _args: list[str]) -> object:
            cargo = self

            class Runner:
                def popen(self, **_kw: object) -> FakeProc:
                    proc = FakeProc()
                    cargo.last_proc = proc
                    return proc

            return Runner()

    return FakeCargo()


def test_run_rust_success(tmp_path: Path, shell_stubs: StubManager) -> None:
    """Happy path for ``run_rust.py``."""
    out = tmp_path / "cov.lcov"
    gh = tmp_path / "gh.txt"

    out.write_text("LF:200\nLH:163\n")
    shell_stubs.register(
        "cargo",
        stdout="Coverage: 81.5%\n",
    )

    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "INPUT_FEATURES": "fast",
        "INPUT_WITH_DEFAULT_FEATURES": "false",
        "GITHUB_OUTPUT": str(gh),
    }

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
    res = run_script(script, env)
    assert res.returncode == 0
    assert "Coverage" in res.stdout

    calls = shell_stubs.calls_of("cargo")
    assert len(calls) == 1
    expected_args = [
        "llvm-cov",
        "--workspace",
        "--summary-only",
        "--no-default-features",
        "--features",
        "fast",
        "--lcov",
        "--output-path",
        str(out),
    ]
    assert calls[0].argv == expected_args

    data = gh.read_text().splitlines()
    assert f"file={out}" in data
    assert "percent=81.50" in data


def test_run_cargo_windows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``_run_cargo`` streams output correctly on Windows."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setattr(mod.os, "name", "nt")

    def fake_echo(line: str, *, err: bool = False, nl: bool = True) -> None:
        print(line, end="\n" if nl else "", file=sys.stderr if err else sys.stdout)

    monkeypatch.setattr(mod.typer, "echo", fake_echo)

    monkeypatch.setattr(mod, "cargo", _make_fake_cargo("out-line\r\n", "err-line\n"))
    res = mod._run_cargo([])
    captured = capsys.readouterr()
    assert "out-line\r\n" in captured.out
    assert "err-line\n" in captured.err
    assert res == "out-line"


def test_run_cargo_windows_closes_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` closes captured streams on success."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    class TrackingStream(io.StringIO):
        def __init__(self, value: str) -> None:
            super().__init__(value)
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            super().close()

    stdout = TrackingStream("out-line\n")
    stderr = TrackingStream("err-line\n")
    fake_cargo = _make_fake_cargo(stdout, stderr)
    monkeypatch.setattr(mod, "cargo", fake_cargo)

    result = mod._run_cargo(["llvm-cov"])

    assert result == "out-line"
    assert stdout.closed
    assert stderr.closed
    assert stdout.close_calls >= 1
    assert stderr.close_calls >= 1


def test_run_cargo_windows_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` raises on non-zero exit code on Windows."""
    import typer as real_typer

    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)
    monkeypatch.setattr(mod.typer, "Exit", real_typer.Exit)

    monkeypatch.setattr(
        mod, "cargo", _make_fake_cargo("out-line\n", "err-line\n", returncode=1)
    )
    with pytest.raises(mod.typer.Exit) as excinfo:
        mod._run_cargo([])
    # click.exceptions.Exit exposes ``exit_code``; SystemExit uses ``code``.
    assert _exit_code(excinfo.value) == 1


def test_run_cargo_windows_pump_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` re-raises exceptions from pump threads on Windows."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    class BoomIO(io.StringIO):
        def readline(self) -> str:
            message = "boom in pump"
            raise RuntimeError(message)

    fake_cargo = _make_fake_cargo(BoomIO(), io.StringIO(""), track_lifecycle=True)
    monkeypatch.setattr(mod, "cargo", fake_cargo)
    with pytest.raises(RuntimeError, match="boom in pump"):
        mod._run_cargo([])
    proc = fake_cargo.last_proc
    assert proc is not None
    assert proc.killed
    assert proc.waited


def test_run_cargo_windows_none_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` fails when stdout is missing on Windows."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    fake_cargo = _make_fake_cargo(None, "err-line\n")
    monkeypatch.setattr(mod, "cargo", fake_cargo)
    with pytest.raises(mod.typer.Exit):
        mod._run_cargo([])
    proc = fake_cargo.last_proc
    assert proc is not None
    assert proc.stderr is not None
    assert proc.stderr.closed


def test_run_cargo_windows_none_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` fails when stderr is missing on Windows."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    fake_cargo = _make_fake_cargo("out-line\n", None)
    monkeypatch.setattr(mod, "cargo", fake_cargo)
    with pytest.raises(mod.typer.Exit):
        mod._run_cargo([])
    proc = fake_cargo.last_proc
    assert proc is not None
    assert proc.stdout is not None
    assert proc.stdout.closed


def test_run_cargo_stream_close_error_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors closing streams are suppressed during cleanup."""
    mod = _load_module(monkeypatch, "run_rust")
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    class ExplodingStream(io.StringIO):
        def __init__(self, value: str) -> None:
            super().__init__(value)
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            super().close()
            message = "close failure"
            raise RuntimeError(message)

    stdout = ExplodingStream("out-line\n")
    stderr = io.StringIO("err-line\n")
    fake_cargo = _make_fake_cargo(stdout, stderr)
    monkeypatch.setattr(mod, "cargo", fake_cargo)

    result = mod._run_cargo(["llvm-cov"])

    assert result == "out-line"
    assert stdout.close_calls >= 1
    assert stdout.closed
    assert stderr.closed


def test_run_rust_with_cucumber(tmp_path: Path, shell_stubs: StubManager) -> None:
    """``run_rust.py`` runs cucumber scenarios when requested."""
    out = tmp_path / "cov.lcov"
    gh = tmp_path / "gh.txt"

    cuc_file = out.with_name(f"{out.stem}.cucumber{out.suffix}")
    out.write_text("TN:test\nend_of_record\n")
    cuc_file.write_text("TN:cuke\nend_of_record\n")

    shell_stubs.register("cargo", stdout="Coverage: 100%\n")

    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "INPUT_FEATURES": "",
        "INPUT_WITH_DEFAULT_FEATURES": "true",
        "INPUT_WITH_CUCUMBER_RS": "true",
        "INPUT_CUCUMBER_RS_FEATURES": "tests/features",
        "INPUT_CUCUMBER_RS_ARGS": "--tag fast",
        "GITHUB_OUTPUT": str(gh),
    }

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
    res = run_script(script, env)
    assert res.returncode == 0

    calls = shell_stubs.calls_of("cargo")
    assert len(calls) == 2
    cuc_file = out.with_name(f"{out.stem}.cucumber{out.suffix}")
    expected_second = [
        "llvm-cov",
        "--workspace",
        "--summary-only",
        "--lcov",
        "--output-path",
        str(cuc_file),
        "--",
        "--test",
        "cucumber",
        "--",
        "cucumber",
        "--features",
        "tests/features",
        "--tag",
        "fast",
    ]
    assert calls[1].argv == expected_second
    assert out.read_text() == "TN:test\nend_of_record\nTN:cuke\nend_of_record\n"
    assert not cuc_file.exists()


def test_run_rust_with_cucumber_cobertura(
    tmp_path: Path, shell_stubs: StubManager
) -> None:
    """Cobertura format merges cucumber coverage using ``merge-cobertura``."""
    out = tmp_path / "cov.xml"
    gh = tmp_path / "gh.txt"

    cuc_file = out.with_name(f"{out.stem}.cucumber{out.suffix}")
    out.write_text("<cov/>")
    cuc_file.write_text("<cuke/>")

    shell_stubs.register("cargo", stdout="Coverage: 100%\n")
    shell_stubs.register(
        "uvx",
        variants=[
            {
                "match": ["merge-cobertura", str(out), str(cuc_file)],
                "stdout": "<coverage lines-covered='1' lines-valid='1'/>",
            }
        ],
    )

    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "cobertura",
        "INPUT_FEATURES": "",
        "INPUT_WITH_DEFAULT_FEATURES": "true",
        "INPUT_WITH_CUCUMBER_RS": "true",
        "INPUT_CUCUMBER_RS_FEATURES": "tests/features",
        "INPUT_CUCUMBER_RS_ARGS": "",
        "GITHUB_OUTPUT": str(gh),
    }

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
    res = run_script(script, env)
    assert res.returncode == 0

    uvx_calls = shell_stubs.calls_of("uvx")
    assert uvx_calls
    assert uvx_calls[0].argv == ["merge-cobertura", str(out), str(cuc_file)]
    assert out.read_text() == "<coverage lines-covered='1' lines-valid='1'/>"
    assert not cuc_file.exists()


def test_run_rust_with_cucumber_cobertura_merge_failure(
    tmp_path: Path, shell_stubs: StubManager
) -> None:
    """Failures from ``merge-cobertura`` exit with its code."""
    out = tmp_path / "cov.xml"
    gh = tmp_path / "gh.txt"

    cuc_file = out.with_name(f"{out.stem}.cucumber{out.suffix}")
    out.write_text("<cov/>")
    cuc_file.write_text("<cuke/>")

    shell_stubs.register("cargo", stdout="Coverage: 100%\n")
    shell_stubs.register(
        "uvx",
        variants=[
            {
                "match": ["merge-cobertura", str(out), str(cuc_file)],
                "stderr": "oops",
                "exit_code": 3,
            }
        ],
    )

    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "cobertura",
        "INPUT_FEATURES": "",
        "INPUT_WITH_DEFAULT_FEATURES": "true",
        "INPUT_WITH_CUCUMBER_RS": "true",
        "INPUT_CUCUMBER_RS_FEATURES": "tests/features",
        "INPUT_CUCUMBER_RS_ARGS": "",
        "GITHUB_OUTPUT": str(gh),
    }

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
    res = run_script(script, env)
    assert res.returncode == 3
    assert "merge-cobertura failed" in res.stderr
    assert cuc_file.exists()


def test_run_rust_failure(tmp_path: Path, shell_stubs: StubManager) -> None:
    """``run_rust.py`` propagates cargo failures."""
    shell_stubs.register(
        "cargo",
        stderr="boom",
        exit_code=2,
    )
    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(tmp_path / "out"),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "GITHUB_OUTPUT": str(tmp_path / "gh.txt"),
    }
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
    res = run_script(script, env)
    assert res.returncode == 2
    assert "cargo llvm-cov" in res.stderr
    assert "failed with code 2" in res.stderr


def test_merge_cobertura(tmp_path: Path, shell_stubs: StubManager) -> None:
    """``merge_cobertura.py`` merges two files and removes them."""
    rust = tmp_path / "r.xml"
    py = tmp_path / "p.xml"
    rust.write_text("<r/>")
    py.write_text("<p/>")
    out = tmp_path / "merged.xml"

    shell_stubs.register(
        "uvx",
        variants=[
            {
                "match": ["merge-cobertura", str(rust), str(py)],
                "stdout": "<merged/>",
            }
        ],
    )

    env = {
        **shell_stubs.env,
        "RUST_FILE": str(rust),
        "PYTHON_FILE": str(py),
        "OUTPUT_PATH": str(out),
    }
    script = Path(__file__).resolve().parents[1] / "scripts" / "merge_cobertura.py"
    res = run_script(script, env)
    assert res.returncode == 0
    assert out.read_text() == "<merged/>"
    assert not rust.exists()
    assert not py.exists()
    calls = shell_stubs.calls_of("uvx")
    assert calls
    assert calls[0].argv[:1] == ["merge-cobertura"]


def test_lcov_zero_lines_found(tmp_path: Path, run_rust_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_lcov`` returns 0.00 when no lines are found."""
    lcov = tmp_path / "zero.lcov"
    lcov.write_text("LF:0\nLH:0\n")
    assert run_rust_module.get_line_coverage_percent_from_lcov(lcov) == "0.00"


def test_lcov_empty_file(tmp_path: Path, run_rust_module: ModuleType) -> None:
    """Empty lcov files report zero coverage."""
    lcov = tmp_path / "empty.lcov"
    lcov.write_text("")
    assert run_rust_module.get_line_coverage_percent_from_lcov(lcov) == "0.00"


def test_lcov_missing_lh_tag(tmp_path: Path, run_rust_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_lcov`` handles files missing ``LH`` tags."""
    lcov = tmp_path / "missing.lcov"
    lcov.write_text("LF:100\n")
    assert run_rust_module.get_line_coverage_percent_from_lcov(lcov) == "0.00"


def test_lcov_malformed_file(tmp_path: Path, run_rust_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_lcov`` returns 0.00 for malformed files."""
    lcov = tmp_path / "bad.lcov"
    lcov.write_text("LF:abc\nLH:xyz\n")
    assert run_rust_module.get_line_coverage_percent_from_lcov(lcov) == "0.00"


def test_lcov_file_missing(tmp_path: Path, run_rust_module: ModuleType) -> None:
    """Non-existent file triggers ``SystemExit``."""
    with pytest.raises(run_rust_module.typer.Exit) as excinfo:
        run_rust_module.get_line_coverage_percent_from_lcov(tmp_path / "nope.lcov")
    assert _exit_code(excinfo.value) == 1


def test_lcov_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_rust_module: ModuleType,
) -> None:
    """Unreadable file triggers ``SystemExit``."""
    lcov = tmp_path / "deny.lcov"
    lcov.write_text("LF:1\nLH:1\n")

    def bad_read_text(*_: object, **__: object) -> str:
        message = "nope"
        raise PermissionError(message)

    monkeypatch.setattr(Path, "read_text", bad_read_text, raising=False)
    with pytest.raises(run_rust_module.typer.Exit) as excinfo:
        run_rust_module.get_line_coverage_percent_from_lcov(lcov)
    assert _exit_code(excinfo.value) == 1


@pytest.fixture
def run_python_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return a freshly loaded ``run_python`` module for testing."""
    return _load_module(monkeypatch, "run_python")


def test_cobertura_detail(tmp_path: Path, run_python_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_cobertura`` handles per-line detail."""
    xml = tmp_path / "cov.xml"
    xml.write_text(
        """
<coverage>
  <packages>
    <package>
      <classes>
        <class>
          <lines>
            <line hits='1'/>
            <line hits='0'/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
        """
    )
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "50.00"


def test_cobertura_root_totals(tmp_path: Path, run_python_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_cobertura`` falls back to root totals."""
    xml = tmp_path / "root.xml"
    xml.write_text("<coverage lines-covered='81' lines-valid='100' />")
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "81.00"


def test_cobertura_zero_lines(tmp_path: Path, run_python_module: ModuleType) -> None:
    """``get_line_coverage_percent_from_cobertura`` handles zero totals."""
    xml = tmp_path / "zero.xml"
    xml.write_text("<coverage lines-covered='0' lines-valid='0' />")
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "0.00"


def test_cobertura_malformed_xml(tmp_path: Path, run_python_module: ModuleType) -> None:
    """Malformed XML raises ``typer.Exit``."""
    xml = tmp_path / "bad.xml"
    xml.write_text("<coverage>")
    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert _exit_code(excinfo.value) == 1


def test_run_python_coveragepy_empty_xml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    run_python_module: ModuleType,
) -> None:
    """Coverage.py format handles empty XML output and moves the data file."""
    output = tmp_path / "coveragepy.dat"
    github_output = tmp_path / "gh.txt"
    coverage_file = tmp_path / ".coverage"
    coverage_file.write_text("payload", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_run_cmd(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)

    @contextlib.contextmanager
    def fake_tmp_coveragepy_xml(out: Path) -> typ.Iterator[Path]:
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text(
            "<coverage lines-covered='0' lines-valid='0' />",
            encoding="utf-8",
        )
        try:
            yield xml_path
        finally:
            xml_path.unlink(missing_ok=True)

    monkeypatch.setattr(
        run_python_module, "tmp_coveragepy_xml", fake_tmp_coveragepy_xml
    )

    run_python_module.main(output, "python", "coveragepy", github_output, None)

    captured = capsys.readouterr()
    assert "Current coverage: 0.00%" in captured.out

    assert output.read_text(encoding="utf-8") == "payload"
    assert not coverage_file.exists()

    data = github_output.read_text(encoding="utf-8").splitlines()
    assert f"file={output}" in data
    assert "percent=0.00" in data


def test_run_python_coveragepy_malformed_xml_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_python_module: ModuleType,
) -> None:
    """Malformed coverage.py XML propagates Typer exits."""
    output = tmp_path / "coveragepy.dat"
    github_output = tmp_path / "gh.txt"
    coverage_file = tmp_path / ".coverage"
    coverage_file.write_text("payload", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_run_cmd(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(run_python_module, "run_cmd", fake_run_cmd)

    @contextlib.contextmanager
    def fake_tmp_coveragepy_xml(out: Path) -> typ.Iterator[Path]:
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text("<coverage>", encoding="utf-8")
        try:
            yield xml_path
        finally:
            xml_path.unlink(missing_ok=True)

    monkeypatch.setattr(
        run_python_module, "tmp_coveragepy_xml", fake_tmp_coveragepy_xml
    )

    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.main(output, "python", "coveragepy", github_output, None)

    assert _exit_code(excinfo.value) == 1
    assert coverage_file.exists()
    assert not github_output.exists()


def test_cobertura_missing_file(tmp_path: Path, run_python_module: ModuleType) -> None:
    """Missing Cobertura files raise ``typer.Exit``."""
    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.get_line_coverage_percent_from_cobertura(
            tmp_path / "absent.xml"
        )
    assert _exit_code(excinfo.value) == 1


def test_cobertura_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_python_module: ModuleType,
) -> None:
    """Permission errors when reading Cobertura files raise ``typer.Exit``."""
    xml = tmp_path / "nope.xml"
    xml.write_text("<coverage/>")

    def raise_permission_error(*_: object, **__: object) -> object:
        message = "denied"
        raise PermissionError(message)

    import coverage_parsers

    monkeypatch.setattr(coverage_parsers.etree, "parse", raise_permission_error)

    with pytest.raises(run_python_module.typer.Exit) as excinfo:
        run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert _exit_code(excinfo.value) == 1
