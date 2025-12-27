"""Tests for the ``ensure_cargo_version`` helper script."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from syspath_hack import prepend_to_syspath

MODULE_PATH = Path(__file__).resolve().parent.parent / "ensure_cargo_version.py"
SCRIPT_DIR = MODULE_PATH.parent
prepend_to_syspath(SCRIPT_DIR)

spec = importlib.util.spec_from_file_location(
    "ensure_cargo_version_module", MODULE_PATH
)
if spec is None or spec.loader is None:  # pragma: no cover - defensive import guard
    message = "Unable to load ensure_cargo_version module for testing"
    raise RuntimeError(message)
module = importlib.util.module_from_spec(spec)
if not isinstance(module, ModuleType):  # pragma: no cover - importlib contract
    message = "module_from_spec did not return a ModuleType"
    raise TypeError(message)
sys.modules[spec.name] = module
spec.loader.exec_module(module)  # type: ignore[misc]
ensure = module


def _write_manifest(path: Path, version: str, *, name: str = "demo") -> None:
    """Write a simple manifest declaring ``name`` and ``version`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""[package]\nname = \"{name}\"\nversion = \"{version}\"\n""",
        encoding="utf-8",
    )


def _write_raw_manifest(path: Path, contents: str) -> None:
    """Write a manifest with ``contents`` directly to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


@pytest.mark.parametrize(
    "manifest_contents",
    [
        '[package]\nversion = "1.2.3"\n',
        '[package]\nname = ""\nversion = "1.2.3"\n',
        '[package]\nname = "   "\nversion = "1.2.3"\n',
    ],
)
def test_read_manifest_version_rejects_invalid_names(
    tmp_path: Path, manifest_contents: str
) -> None:
    """A manifest must declare a non-empty ``package.name``."""
    manifest_path = tmp_path / "Cargo.toml"
    _write_raw_manifest(manifest_path, manifest_contents)

    with pytest.raises(ensure.ManifestError, match=r"package\.name"):
        ensure._read_manifest_version(manifest_path)


def test_read_manifest_version_trims_whitespace_from_name(tmp_path: Path) -> None:
    """Whitespace around the crate name should be ignored."""
    manifest_path = tmp_path / "Cargo.toml"
    _write_raw_manifest(
        manifest_path,
        '[package]\nname = " demo-crate "\nversion = "1.2.3"\n',
    )

    manifest_version = ensure._read_manifest_version(manifest_path)

    assert manifest_version.name == "demo-crate"


def test_main_skips_tag_comparison_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tag comparison is skipped but outputs remain populated."""
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
    assert "crate-name=demo" in contents
    assert "version=9.9.9" in contents

    captured = capsys.readouterr()
    assert "Tag comparison disabled" in captured.out


def test_main_with_disabled_tag_check_does_not_require_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When tags are optional the script tolerates missing refs."""
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
    assert "crate-name=demo" in contents
    assert not any(line.startswith("version=") for line in contents)

    captured = capsys.readouterr()
    assert "Tag comparison disabled" in captured.out


def test_main_rejects_invalid_check_tag_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Invalid ``check_tag`` inputs abort the run with an error."""
    workspace = tmp_path
    manifest_path = workspace / "Cargo.toml"
    _write_manifest(manifest_path, "0.1.0")

    output_file = workspace / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))

    captured_errors: list[tuple[str, str]] = []

    def record_error(title: str, message: str, *, path: Path | None = None) -> None:
        captured_errors.append((title, message))

    monkeypatch.setattr(ensure, "_emit_error", record_error)

    with pytest.raises(SystemExit) as exit_info:
        ensure.main(manifests=[Path("Cargo.toml")], check_tag="definitely-not-bool")

    assert exit_info.value.code == 1
    assert captured_errors
    first_title, first_message = captured_errors[0]
    assert first_title == "Invalid input"
    assert "definitely-not-bool" in first_message


def test_main_records_first_manifest_version_in_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The crate-version output reflects the first manifest."""
    workspace = tmp_path
    first_manifest = workspace / "Cargo.toml"
    second_manifest = workspace / "crates" / "other" / "Cargo.toml"

    _write_manifest(first_manifest, "3.4.5", name="primary")
    _write_manifest(second_manifest, "9.9.9", name="secondary")

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
    assert "crate-name=primary" in contents
    assert "version=3.4.5" in contents

    captured = capsys.readouterr()
    assert "Tag comparison disabled" in captured.out


def test_main_emits_crate_version_when_checking_tag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even when comparing tags the crate-version output remains available."""
    workspace = tmp_path
    manifest_path = workspace / "Cargo.toml"
    _write_manifest(manifest_path, "4.5.6")

    output_file = workspace / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("GITHUB_REF_NAME", "v4.5.6")

    ensure.main(manifests=[Path("Cargo.toml")])

    contents = output_file.read_text(encoding="utf-8").splitlines()
    assert "crate-version=4.5.6" in contents
    assert "crate-name=demo" in contents
    assert "version=4.5.6" in contents

    captured = capsys.readouterr()
    assert "Release tag 4.5.6 matches" in captured.out


@pytest.mark.parametrize(
    "manifest_contents",
    [
        '[package]\nversion = "1.2.3"\n',
        '[package]\nname = ""\nversion = "1.2.3"\n',
        '[package]\nname = "   "\nversion = "1.2.3"\n',
    ],
)
def test_main_aborts_when_crate_name_missing_or_blank(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manifest_contents: str,
) -> None:
    """Invalid crate names should terminate the run with an error."""
    workspace = tmp_path
    manifest_path = workspace / "Cargo.toml"
    _write_raw_manifest(manifest_path, manifest_contents)

    output_file = workspace / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))

    recorded_errors: list[tuple[str, str, Path | None]] = []

    def record_error(title: str, message: str, *, path: Path | None = None) -> None:
        recorded_errors.append((title, message, path))

    monkeypatch.setattr(ensure, "_emit_error", record_error)

    with pytest.raises(SystemExit) as excinfo:
        ensure.main(manifests=[Path("Cargo.toml")], check_tag="false")

    assert excinfo.value.code == 1
    assert recorded_errors
    error_titles = {title for title, _, _ in recorded_errors}
    assert "Cargo.toml parse failure" in error_titles
    assert any("package.name" in message for _, message, _ in recorded_errors)
