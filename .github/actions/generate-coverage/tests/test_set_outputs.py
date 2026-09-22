"""Tests for the ``set_outputs`` script."""

from __future__ import annotations

import importlib.util
import os
import sys
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

import pytest
from plumbum import local

from test_support.plumbum_helpers import run_plumbum_command

if typ.TYPE_CHECKING:  # pragma: no cover - runtime import not required
    from types import ModuleType
else:  # pragma: no cover - annotate without importing at runtime
    ModuleType = object  # type: ignore[assignment]


def run_script(
    script: Path,
    env: dict[str, str],
    extra_args: cabc.Sequence[str] = (),
) -> tuple[int, str, str]:
    """Run ``script`` via uv with ``env`` and return ``(code, stdout, stderr)``.

    ``extra_args`` are appended after ``--script <path>``, mirroring how the
    composite action invokes the script: the hyphenated inputs travel as
    command-line arguments rather than environment variables.
    """
    command = local["uv"]["run", "--script", str(script)][list(extra_args)]
    root = Path(__file__).resolve().parents[4]
    merged = {**os.environ, **env}
    current_pp = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = (
        f"{root}{os.pathsep}{current_pp}" if current_pp else str(root)
    )
    merged["PYTHONIOENCODING"] = "utf-8"
    outcome = run_plumbum_command(command, method="run", env=merged)
    return (
        int(outcome.returncode),
        str(outcome.stdout),
        str(outcome.stderr),
    )


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


def test_build_artefact_name_normalizes_components(
    set_outputs_module: ModuleType,
) -> None:
    """The artefact name should incorporate normalized workflow metadata."""
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

    def raise_oserror() -> str:
        message = "platform unavailable"
        raise OSError(message)

    monkeypatch.setattr(module.platform, "system", raise_oserror)
    monkeypatch.setattr(module.platform, "machine", raise_oserror)

    os_label, arch_label = module._detect_runner_labels("Linux", "AMD64")

    assert os_label == "linux"
    assert arch_label == "amd64"


@pytest.mark.parametrize(
    ("default_os", "default_arch"),
    [(None, None), ("", "")],
    ids=["none_defaults", "empty_string_defaults"],
)
def test_detect_runner_labels_defaults_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
    set_outputs_module: ModuleType,
    default_os: str | None,
    default_arch: str | None,
) -> None:
    """Empty platform responses fall back to unknown identifiers."""
    module = set_outputs_module
    monkeypatch.setattr(module.platform, "system", lambda: "")
    monkeypatch.setattr(module.platform, "machine", lambda: "")

    os_label, arch_label = module._detect_runner_labels(default_os, default_arch)

    assert os_label == "unknown-os"
    assert arch_label == "unknown-arch"


def test_set_outputs_e2e(tmp_path: Path, set_outputs_module: ModuleType) -> None:
    """Running the script via ``uv`` should emit the expected GitHub outputs."""
    module = set_outputs_module
    expected_os, expected_arch = module._detect_runner_labels(None, None)

    gh_file = tmp_path / "gh.txt"
    cov_file = tmp_path / "coverage.xml"

    env = {
        "DETECTED_FMT": "cobertura",
        "GITHUB_OUTPUT": str(gh_file),
        "GITHUB_JOB": "coverage-linux",
        "STRATEGY_JOB_INDEX": "5",
        "RUNNER_OS": "FallbackOS",
        "RUNNER_ARCH": "FallbackArch",
    }

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "set_outputs.py"
    returncode, stdout, stderr = run_script(
        script_path,
        env,
        ["--output-path", str(cov_file), "--artefact-name-suffix", "Nightly"],
    )

    assert returncode == 0, stdout + stderr
    contents = gh_file.read_text().splitlines()
    assert f"file={cov_file}" in contents
    assert "format=cobertura" in contents

    artefact_line = next(line for line in contents if line.startswith("artefact_name="))
    expected_name = f"cobertura-coverage-linux-5-{expected_os}-{expected_arch}-nightly"
    assert artefact_line == f"artefact_name={expected_name}"


# Sentinel carried by every ``INPUT_ARTEFACT*`` key in the act-style
# environment below. It must never reach an output: if the script reads the
# suffix from the environment at all, this value appears in the artefact name
# (or the duplicate lookup aborts the run before any output is written).
_POISONED_SUFFIX = "Poisoned"


def _act_style_env(*, cov_file: Path, gh_file: Path) -> dict[str, str]:
    """Return the duplicate ``INPUT_*`` environment nektos/act produces.

    act exports each composite input to the step environment under its dashed
    name (``INPUT_ARTEFACT-NAME-SUFFIX``), while the step's own ``env:`` block
    supplied the underscored form (``INPUT_ARTEFACT_NAME_SUFFIX``). Cyclopts
    normalizes both spellings onto one parameter, so a binding that resolves
    from the environment at all finds the same parameter twice. The pair for
    ``output-path`` is injected too, because act treats every declared input
    alike and only one of them colliding would understate the repro.
    """
    return {
        "INPUT_OUTPUT-PATH": str(cov_file),
        "INPUT_OUTPUT_PATH": str(cov_file),
        "INPUT_ARTEFACT-NAME-SUFFIX": _POISONED_SUFFIX,
        "INPUT_ARTEFACT_NAME_SUFFIX": _POISONED_SUFFIX,
        "DETECTED_FMT": "cobertura",
        "GITHUB_OUTPUT": str(gh_file),
        "GITHUB_JOB": "build-test",
        "STRATEGY_JOB_INDEX": "0",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
    }


@pytest.mark.parametrize(
    ("cli_args", "expected_tail"),
    [
        pytest.param([], "", id="omitted_suffix"),
        pytest.param(["--artefact-name-suffix", "Nightly"], "-nightly", id="non_empty"),
    ],
)
def test_set_outputs_survives_act_duplicate_input_env(
    tmp_path: Path,
    set_outputs_module: ModuleType,
    cli_args: list[str],
    expected_tail: str,
) -> None:
    """Duplicate dashed and underscored ``INPUT_*`` keys must not abort the run.

    Regression for the act-only failure "Parameter INPUT_ARTEFACT_NAME_SUFFIX
    specified multiple times": act exports inputs under their dashed names, so
    a lookup reaching the environment finds each hyphenated input twice.

    The omitted case deliberately leaves ``--artefact-name-suffix`` off the
    command line while the environment carries the sentinel. Passing the flag
    here would satisfy Cyclopts from the command line and mask a reintroduced
    environment binding, so the discriminating assertion is the artefact name:
    it must carry no suffix segment. Together the two cases prove the
    hyphenated inputs come only from arguments, and that omitting one still
    yields the same name shape as omitting the input itself.
    """
    module = set_outputs_module
    expected_os, expected_arch = module._detect_runner_labels(None, None)

    gh_file = tmp_path / "gh.txt"
    gh_file.touch()
    cov_file = tmp_path / "coverage.xml"

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "set_outputs.py"
    returncode, stdout, stderr = run_script(
        script_path,
        _act_style_env(cov_file=cov_file, gh_file=gh_file),
        ["--output-path", str(cov_file), *cli_args],
    )

    combined = stdout + stderr
    assert returncode == 0, combined
    # Asserted on the message itself, not merely on a zero exit: the failure
    # this guards against is a duplicate-parameter abort, and a future binding
    # could reintroduce it while some other path kept the exit status clean.
    assert "specified multiple times" not in combined, combined

    contents = gh_file.read_text().splitlines()
    assert f"file={cov_file}" in contents
    assert "format=cobertura" in contents

    artefact_line = next(line for line in contents if line.startswith("artefact_name="))
    expected_name = (
        f"cobertura-build-test-0-{expected_os}-{expected_arch}{expected_tail}"
    )
    assert artefact_line == f"artefact_name={expected_name}"
    assert _POISONED_SUFFIX.lower() not in artefact_line, (
        "the artefact name picked up a suffix from the environment; the "
        "hyphenated inputs must be read from arguments only"
    )


def test_main_requires_github_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, set_outputs_module: ModuleType
) -> None:
    """Calling ``main`` without ``GITHUB_OUTPUT`` fails with a clear error message."""
    module = set_outputs_module
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    with pytest.raises(RuntimeError, match="GITHUB_OUTPUT"):
        module.main(output_path=tmp_path / "cov.xml", fmt="cobertura")
