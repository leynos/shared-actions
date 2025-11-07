"""Tests for the ``set_outputs`` script."""

from __future__ import annotations

import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

import pytest
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:  # pragma: no cover - runtime import not required
    from types import ModuleType
else:  # pragma: no cover - annotate without importing at runtime
    ModuleType = object  # type: ignore[assignment]


def run_script(script: Path, env: dict[str, str]) -> object:
    """Run ``script`` using uv with ``env`` and return the plumbum result."""
    command = local["uv"]["run", "--script", str(script)]
    root = Path(__file__).resolve().parents[4]
    merged = {**os.environ, **env}
    current_pp = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = (
        f"{root}{os.pathsep}{current_pp}" if current_pp else str(root)
    )
    merged["PYTHONIOENCODING"] = "utf-8"
    return run_plumbum_command(command, method="run", env=merged)


@pytest.fixture
def set_outputs_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load and return the ``set_outputs`` module for direct testing."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    root_dir = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(scripts_dir))
    monkeypatch.syspath_prepend(str(root_dir))

    spec = importlib.util.spec_from_file_location(
        "set_outputs", scripts_dir / "set_outputs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_artefact_name_normalises_components(
    set_outputs_module: ModuleType,
) -> None:
    """The artefact name should incorporate normalised workflow metadata."""
    module = set_outputs_module

    components = module.ArtefactNameComponents(
        fmt="Cobertura",
        job="Coverage / Linux",
        job_index="3",
        runner_os="Ubuntu",
        runner_arch="X86_64",
        extra_suffix="Nightly Build",
    )

    result = module.build_artefact_name(components)

    assert result == "cobertura-coverage-linux-3-ubuntu-x86_64-nightly-build"


def test_detect_runner_labels_fallbacks(  # noqa: D103 - docstring via fixture name
    monkeypatch: pytest.MonkeyPatch, set_outputs_module: ModuleType
) -> None:
    module = set_outputs_module

    def raise_process_error(_command: object) -> str:
        raise ProcessExecutionError(("python",), 1, "", "")

    monkeypatch.setattr(module, "run_cmd", raise_process_error)

    os_label, arch_label = module._detect_runner_labels("Linux", "AMD64")

    assert os_label == "linux"
    assert arch_label == "amd64"


def test_set_outputs_e2e(tmp_path: Path, set_outputs_module: ModuleType) -> None:
    """Running the script via ``uv`` should emit the expected GitHub outputs."""
    module = set_outputs_module
    expected_os, expected_arch = module._detect_runner_labels(None, None)

    gh_file = tmp_path / "gh.txt"
    cov_file = tmp_path / "coverage.xml"

    env = {
        "INPUT_OUTPUT_PATH": str(cov_file),
        "DETECTED_FMT": "cobertura",
        "INPUT_ARTEFACT_NAME_SUFFIX": "Nightly",
        "GITHUB_OUTPUT": str(gh_file),
        "GITHUB_JOB": "coverage-linux",
        "STRATEGY_JOB_INDEX": "5",
        "RUNNER_OS": "FallbackOS",
        "RUNNER_ARCH": "FallbackArch",
    }

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "set_outputs.py"
    returncode, stdout, stderr = run_script(script_path, env)

    assert returncode == 0, stdout + stderr
    contents = gh_file.read_text().splitlines()
    assert f"file={cov_file}" in contents
    assert "format=cobertura" in contents

    artefact_line = next(line for line in contents if line.startswith("artefact_name="))
    expected_name = f"cobertura-coverage-linux-5-{expected_os}-{expected_arch}-nightly"
    assert artefact_line == f"artefact_name={expected_name}"
