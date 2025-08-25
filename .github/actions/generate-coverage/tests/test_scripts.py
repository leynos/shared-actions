"""Tests for coverage utility scripts."""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import types
import typing as t
from pathlib import Path

import pytest

if t.TYPE_CHECKING:  # pragma: no cover - type hints only
    from shellstub import StubManager


def run_script(
    script: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` via ``uv`` with ``env`` and return the completed process."""
    cmd = ["uv", "run", "--script", str(script), *args]
    root = Path(__file__).resolve().parents[4]
    env = {**os.environ, **env, "PYTHONPATH": str(root)}
    return subprocess.run(cmd, capture_output=True, text=True, env=env)  # noqa: S603


def _load_module(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    cmds: dict[str, t.Any],
) -> types.ModuleType:
    """Import ``name`` from the ``scripts`` directory with stubbed deps."""
    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(script_dir)
    monkeypatch.syspath_prepend(Path(__file__).resolve().parents[4])
    monkeypatch.delitem(sys.modules, "coverage_parsers", raising=False)
    spec = importlib.util.spec_from_file_location(name, script_dir / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None

    dummy_exc = type("DummyError", (Exception,), {})
    fake_proc = types.SimpleNamespace(ProcessExecutionError=dummy_exc)
    fake_plumbum = types.SimpleNamespace(
        cmd=types.SimpleNamespace(**dict.fromkeys(cmds, None)),
        commands=types.SimpleNamespace(processes=fake_proc),
    )
    if "FG" in cmds:
        fake_plumbum.FG = None
    monkeypatch.setitem(sys.modules, "plumbum", fake_plumbum)
    monkeypatch.setitem(sys.modules, "plumbum.cmd", fake_plumbum.cmd)
    monkeypatch.setitem(sys.modules, "plumbum.commands.processes", fake_proc)

    import xml.etree.ElementTree as ETree

    class FakeRoot:
        def __init__(self, elem: ETree.Element) -> None:
            self._elem = elem

        def xpath(self, expr: str) -> float:
            if expr.startswith("number(") and expr.endswith(")"):
                attr = expr[len("number(") : -1]
                if attr.startswith("/coverage/@"):
                    return float(self._elem.get(attr.split("@")[1]) or float("nan"))
            if expr.startswith("count(") and expr.endswith(")"):
                inner = expr[len("count(") : -1]
                if inner == "//class/lines/line":
                    return float(len(self._elem.findall(".//class/lines/line")))
                if inner == "//class/lines/line[number(@hits) > 0]":
                    total = 0
                    for line in self._elem.findall(".//class/lines/line"):
                        try:
                            hits = float(line.get("hits", "0"))
                        except ValueError:
                            hits = 0
                        if hits > 0:
                            total += 1
                    return float(total)
            raise NotImplementedError(expr)

    class FakeTree:
        def __init__(self, elem: ETree.Element) -> None:
            self._elem = elem

        def getroot(self) -> FakeRoot:
            return FakeRoot(self._elem)

    def fake_parse(path: str) -> FakeTree:
        return FakeTree(ETree.parse(path).getroot())  # noqa: S314 - test data

    class FakeEtree:
        class LxmlError(Exception):
            pass

        parse = staticmethod(fake_parse)

    fake_lxml = types.SimpleNamespace(etree=FakeEtree)
    monkeypatch.setitem(sys.modules, "lxml", fake_lxml)
    monkeypatch.setitem(sys.modules, "lxml.etree", FakeEtree)

    fake_typer = types.SimpleNamespace(
        Option=lambda default=None, **_: default,
        echo=lambda *a, **k: None,
        Exit=SystemExit,
        run=lambda func: func(),
    )
    monkeypatch.setitem(sys.modules, "typer", fake_typer)

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def run_rust_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Return the ``run_rust`` module with dependencies stubbed."""
    return _load_module(monkeypatch, "run_rust", {"cargo": None})


def _make_fake_cargo(
    stdout: str | t.TextIO | None,
    stderr: str | t.TextIO | None,
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

        def wait(self) -> int:
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
    mod = _load_module(monkeypatch, "run_rust", {"cargo": None})
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


def test_run_cargo_windows_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` raises on non-zero exit code on Windows."""
    import typer as real_typer

    mod = _load_module(monkeypatch, "run_rust", {"cargo": None})
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)
    monkeypatch.setattr(mod.typer, "Exit", real_typer.Exit)

    monkeypatch.setattr(
        mod, "cargo", _make_fake_cargo("out-line\n", "err-line\n", returncode=1)
    )
    with pytest.raises(mod.typer.Exit) as excinfo:
        mod._run_cargo([])
    # click.exceptions.Exit exposes ``exit_code``; SystemExit uses ``code``.
    assert (
        getattr(excinfo.value, "exit_code", None)
        or getattr(excinfo.value, "code", None)
    ) == 1


def test_run_cargo_windows_pump_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` re-raises exceptions from pump threads on Windows."""
    mod = _load_module(monkeypatch, "run_rust", {"cargo": None})
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    class BoomIO(io.StringIO):
        def readline(self) -> str:
            raise RuntimeError("boom in pump")  # noqa: TRY003

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
    mod = _load_module(monkeypatch, "run_rust", {"cargo": None})
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    monkeypatch.setattr(mod, "cargo", _make_fake_cargo(None, "err-line\n"))
    with pytest.raises(RuntimeError):
        mod._run_cargo([])


def test_run_cargo_windows_none_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_cargo`` fails when stderr is missing on Windows."""
    mod = _load_module(monkeypatch, "run_rust", {"cargo": None})
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.typer, "echo", lambda *a, **k: None)

    monkeypatch.setattr(mod, "cargo", _make_fake_cargo("out-line\n", None))
    with pytest.raises(RuntimeError):
        mod._run_cargo([])


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


def test_lcov_zero_lines_found(
    tmp_path: Path, run_rust_module: types.ModuleType
) -> None:
    """``get_line_coverage_percent_from_lcov`` returns 0.00 when no lines are found."""
    lcov = tmp_path / "zero.lcov"
    lcov.write_text("LF:0\nLH:0\n")
    assert run_rust_module.get_line_coverage_percent_from_lcov(lcov) == "0.00"


def test_lcov_missing_lh_tag(tmp_path: Path, run_rust_module: types.ModuleType) -> None:
    """``get_line_coverage_percent_from_lcov`` handles files missing ``LH`` tags."""
    lcov = tmp_path / "missing.lcov"
    lcov.write_text("LF:100\n")
    assert run_rust_module.get_line_coverage_percent_from_lcov(lcov) == "0.00"


def test_lcov_malformed_file(tmp_path: Path, run_rust_module: types.ModuleType) -> None:
    """``get_line_coverage_percent_from_lcov`` returns 0.00 for malformed files."""
    lcov = tmp_path / "bad.lcov"
    lcov.write_text("LF:abc\nLH:xyz\n")
    assert run_rust_module.get_line_coverage_percent_from_lcov(lcov) == "0.00"


def test_lcov_file_missing(tmp_path: Path, run_rust_module: types.ModuleType) -> None:
    """Non-existent file triggers ``SystemExit``."""
    with pytest.raises(SystemExit) as excinfo:
        run_rust_module.get_line_coverage_percent_from_lcov(tmp_path / "nope.lcov")
    assert excinfo.value.code == 1


def test_lcov_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_rust_module: types.ModuleType,
) -> None:
    """Unreadable file triggers ``SystemExit``."""
    lcov = tmp_path / "deny.lcov"
    lcov.write_text("LF:1\nLH:1\n")

    def bad_read_text(*_: object, **__: object) -> str:
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "read_text", bad_read_text, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        run_rust_module.get_line_coverage_percent_from_lcov(lcov)
    assert excinfo.value.code == 1


@pytest.fixture
def run_python_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Return the ``run_python`` module with dependencies stubbed."""
    return _load_module(monkeypatch, "run_python", {"python": None, "FG": None})


def test_cobertura_detail(tmp_path: Path, run_python_module: types.ModuleType) -> None:
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


def test_cobertura_root_totals(
    tmp_path: Path, run_python_module: types.ModuleType
) -> None:
    """``get_line_coverage_percent_from_cobertura`` falls back to root totals."""
    xml = tmp_path / "root.xml"
    xml.write_text("<coverage lines-covered='81' lines-valid='100' />")
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "81.00"


def test_cobertura_zero_lines(
    tmp_path: Path, run_python_module: types.ModuleType
) -> None:
    """``get_line_coverage_percent_from_cobertura`` handles zero totals."""
    xml = tmp_path / "zero.xml"
    xml.write_text("<coverage lines-covered='0' lines-valid='0' />")
    pct = run_python_module.get_line_coverage_percent_from_cobertura(xml)
    assert pct == "0.00"
