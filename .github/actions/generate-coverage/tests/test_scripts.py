from __future__ import annotations

import subprocess
from pathlib import Path


def run_script(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    cmd = ["uv", "run", "--script", str(script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)

def test_run_rust_success(tmp_path, shell_stubs):
    out = tmp_path / "cov.lcov"
    gh = tmp_path / "gh.txt"

    shell_stubs.register(
        "cargo",
        stdout="Coverage: 81.5%\n",
    )

    env = {
        **shell_stubs.env,
        "INPUT_OUTPUT_PATH": str(out),
        "DETECTED_LANG": "rust",
        "DETECTED_FMT": "lcov",
        "GITHUB_OUTPUT": str(gh),
    }

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_rust.py"
    res = run_script(script, env)
    assert res.returncode == 0
    assert "Coverage" in res.stdout

    calls = shell_stubs.calls_of("cargo")
    assert len(calls) == 1
    assert "--output-path" in calls[0].argv

    data = gh.read_text().splitlines()
    assert f"file={out}" in data
    assert "percent=81.5" in data


def test_run_rust_failure(tmp_path, shell_stubs):
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
    assert "cargo llvm-cov failed" in res.stderr



def test_merge_cobertura(tmp_path, shell_stubs):
    rust = tmp_path / "r.xml"
    py = tmp_path / "p.xml"
    rust.write_text("<r/>")
    py.write_text("<p/>")
    out = tmp_path / "merged.xml"

    shell_stubs.register(
        "uvx",
        variants=[dict(match=["merge-cobertura", str(rust), str(py)], stdout="<merged/>")]
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
    assert not rust.exists() and not py.exists()
    calls = shell_stubs.calls_of("uvx")
    assert calls and calls[0].argv[:1] == ["merge-cobertura"]

