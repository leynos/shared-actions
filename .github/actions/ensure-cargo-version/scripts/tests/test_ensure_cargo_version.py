import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "ensure_cargo_version.py"
SCRIPT_DIR = MODULE_PATH.parent
SCRIPT_DIR_STR = str(SCRIPT_DIR)
if SCRIPT_DIR_STR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR_STR)

spec = importlib.util.spec_from_file_location("ensure_cargo_version_module", MODULE_PATH)
if spec is None or spec.loader is None:  # pragma: no cover - defensive import guard
    raise RuntimeError("Unable to load ensure_cargo_version module for testing")
module = importlib.util.module_from_spec(spec)
if not isinstance(module, ModuleType):  # pragma: no cover - importlib contract
    raise TypeError("module_from_spec did not return a ModuleType")
sys.modules[spec.name] = module
spec.loader.exec_module(module)  # type: ignore[misc]
ensure = module


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("off", False),
        ("", False),
    ],
)
def test_coerce_bool_accepts_expected_inputs(value: bool | str, expected: bool) -> None:
    assert ensure._coerce_bool(value, parameter="check-tag") is expected


def test_coerce_bool_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        ensure._coerce_bool("not-a-boolean", parameter="check-tag")


def _write_manifest(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """[package]\nname = \"demo\"\nversion = \"{version}\"\n""".format(version=version),
        encoding="utf-8",
    )


def test_main_skips_tag_comparison_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path
    manifest_path = workspace / "Cargo.toml"
    _write_manifest(manifest_path, "1.2.4")

    output_file = workspace / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("GITHUB_REF_NAME", "v9.9.9")

    ensure.main(manifests=[Path("Cargo.toml")], check_tag="false")

    contents = output_file.read_text(encoding="utf-8").splitlines()
    assert "crate-version=1.2.4" in contents
    assert "version=9.9.9" in contents

    captured = capsys.readouterr()
    assert "Tag comparison disabled" in captured.out


def test_main_with_disabled_tag_check_does_not_require_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path
    manifest_path = workspace / "Cargo.toml"
    _write_manifest(manifest_path, "7.8.9")

    output_file = workspace / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)

    ensure.main(manifests=[Path("Cargo.toml")], check_tag="false")

    contents = output_file.read_text(encoding="utf-8").splitlines()
    assert "crate-version=7.8.9" in contents
    assert not any(line.startswith("version=") for line in contents)

    captured = capsys.readouterr()
    assert "Tag comparison disabled" in captured.out


def test_main_records_first_manifest_version_in_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path
    first_manifest = workspace / "Cargo.toml"
    second_manifest = workspace / "crates" / "other" / "Cargo.toml"

    _write_manifest(first_manifest, "3.4.5")
    _write_manifest(second_manifest, "9.9.9")

    output_file = workspace / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("GITHUB_REF_NAME", "v3.4.5")

    ensure.main(
        manifests=[Path("Cargo.toml"), Path("crates/other/Cargo.toml")],
        check_tag="false",
    )

    contents = output_file.read_text(encoding="utf-8").splitlines()
    assert "crate-version=3.4.5" in contents
    assert "version=3.4.5" in contents

    captured = capsys.readouterr()
    assert "Tag comparison disabled" in captured.out
