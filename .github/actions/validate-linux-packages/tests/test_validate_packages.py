"""Tests for the validate_packages helper module."""

import contextlib
import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_packages.py"


if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers
    from types import ModuleType
else:  # pragma: no cover - runtime fallback
    ModuleType = typ.Any


@pytest.fixture
def validate_packages_module() -> ModuleType:
    """Load the validate_packages module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_packages")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_packages", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_packages module"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DummySandbox:
    """Minimal sandbox session recording exec calls for assertions."""

    def __init__(self, root: Path, calls: list[tuple[str, ...]]) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._calls = calls

    def exec(self, *args: str) -> None:
        """Record sandbox exec calls."""
        self._calls.append(tuple(args))


def test_validate_deb_package_runs_sandbox_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validate_packages_module: ModuleType,
) -> None:
    """Debian validation installs the package and exercises all checks."""
    package = tmp_path / "rust-toy-app_1.2.3-1_amd64.deb"
    package.write_bytes(b"payload")
    metadata = validate_packages_module.DebMetadata(
        name="rust-toy-app",
        version="1.2.3-1",
        architecture="amd64",
        files={"/usr/bin/rust-toy-app", "/usr/share/doc/rust-toy-app"},
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_deb_package",
        lambda *_: metadata,
    )
    calls: list[tuple[str, ...]] = []
    sandbox = DummySandbox(tmp_path / "sandbox", calls)

    validate_packages_module.validate_deb_package(
        dpkg_deb=object(),
        package_path=package,
        expected_name="rust-toy-app",
        expected_version="1.2.3",
        expected_deb_version="1.2.3-1",
        expected_arch="amd64",
        expected_paths=("/usr/bin/rust-toy-app",),
        executable_paths=("/usr/bin/rust-toy-app",),
        verify_command=("/usr/bin/rust-toy-app", "--version"),
        sandbox_factory=lambda: contextlib.nullcontext(sandbox),
    )

    assert (tmp_path / "sandbox" / package.name).exists()
    assert (
        "dpkg",
        "-i",
        package.name,
    ) in calls
    assert ("test", "-e", "/usr/bin/rust-toy-app") in calls
    assert ("test", "-x", "/usr/bin/rust-toy-app") in calls
    assert ("/usr/bin/rust-toy-app", "--version") in calls
    assert ("dpkg", "-r", "rust-toy-app") in calls


def test_validate_rpm_package_rejects_unexpected_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    validate_packages_module: ModuleType,
) -> None:
    """RPM validation fails when the release does not match the expected prefix."""
    package = tmp_path / "rust-toy-app-1.2.3-1.x86_64.rpm"
    package.write_bytes(b"payload")
    metadata = validate_packages_module.RpmMetadata(
        name="rust-toy-app",
        version="1.2.3",
        release="2.el9",
        architecture="x86_64",
        files={"/usr/bin/rust-toy-app"},
    )
    monkeypatch.setattr(
        validate_packages_module,
        "inspect_rpm_package",
        lambda *_: metadata,
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="unexpected rpm release",
    ):
        validate_packages_module.validate_rpm_package(
            rpm_cmd=object(),
            package_path=package,
            expected_name="rust-toy-app",
            expected_version="1.2.3",
            expected_release="1",
            expected_arch="x86_64",
            expected_paths=("/usr/bin/rust-toy-app",),
            executable_paths=("/usr/bin/rust-toy-app",),
            verify_command=(),
            sandbox_factory=lambda: contextlib.nullcontext(None),
        )


def test_ensure_subset_reports_missing_entries(
    validate_packages_module: ModuleType,
) -> None:
    """ensure_subset raises a ValidationError when paths are missing."""
    with pytest.raises(
        validate_packages_module.ValidationError,
        match="missing payload",
    ):
        validate_packages_module.ensure_subset(
            ("/usr/bin/tool",),
            (),
            "payload",
        )


def test_locate_deb_returns_unique_package(
    validate_packages_module: ModuleType, tmp_path: Path
) -> None:
    """locate_deb finds the matching artefact in the directory."""
    package = tmp_path / "tool_1.2.3-1_amd64.deb"
    package.write_bytes(b"")

    result = validate_packages_module.locate_deb(
        tmp_path, "tool", "1.2.3", "1"
    )

    assert result == package


def test_locate_deb_rejects_ambiguous_matches(
    validate_packages_module: ModuleType,
    tmp_path: Path,
) -> None:
    """locate_deb raises when multiple candidates are found."""
    (tmp_path / "tool_1.2.3-1_amd64.deb").write_bytes(b"")
    (tmp_path / "tool_1.2.3-1_arm64.deb").write_bytes(b"")

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="expected exactly one tool deb package",
    ):
        validate_packages_module.locate_deb(tmp_path, "tool", "1.2.3", "1")


def test_locate_rpm_rejects_ambiguous_matches(
    validate_packages_module: ModuleType,
    tmp_path: Path,
) -> None:
    """locate_rpm raises when multiple candidates are found."""
    (tmp_path / "tool-1.2.3-1.aarch64.rpm").write_bytes(b"")
    (tmp_path / "tool-1.2.3-1.x86_64.rpm").write_bytes(b"")

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="expected exactly one tool rpm package",
    ):
        validate_packages_module.locate_rpm(tmp_path, "tool", "1.2.3", "1")


@pytest.mark.parametrize(
    ("arch", "expected"),
    [
        ("amd64", {"amd64", "x86_64"}),
        ("arm64", {"arm64", "aarch64"}),
        ("riscv64", {"riscv64"}),
    ],
)
def test_acceptable_rpm_architectures_cover_aliases(
    arch: str,
    expected: set[str],
    validate_packages_module: ModuleType,
) -> None:
    """acceptable_rpm_architectures returns the canonical alias set."""
    assert validate_packages_module.acceptable_rpm_architectures(arch) == expected


def test_metadata_validator_raise_error_message(
    validate_packages_module: ModuleType,
) -> None:
    """raise_error reports the expected and actual values."""
    with pytest.raises(
        validate_packages_module.ValidationError,
        match="unexpected field: expected 'foo'",
    ):
        validate_packages_module._MetadataValidators.raise_error(
            "unexpected field", "foo", "bar"
        )


def test_metadata_validator_equal_accepts_match(
    validate_packages_module: ModuleType,
) -> None:
    """equal validator returns successfully when the attribute matches."""

    class Meta:
        name = "package"

    validator = validate_packages_module._MetadataValidators.equal(
        "name", "package", "unexpected package name"
    )

    validator(Meta())


def test_metadata_validator_equal_rejects_mismatch(
    validate_packages_module: ModuleType,
) -> None:
    """equal validator raises when the attribute differs."""

    class Meta:
        name = "other"

    validator = validate_packages_module._MetadataValidators.equal(
        "name", "package", "unexpected package name"
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="unexpected package name",
    ):
        validator(Meta())


def test_metadata_validator_in_set_uses_formatter(
    validate_packages_module: ModuleType,
) -> None:
    """in_set validator applies the provided formatter in error messages."""

    class Meta:
        version = "2.0.0"

    validator = validate_packages_module._MetadataValidators.in_set(
        "version",
        {"1.0.0", "1.1.0"},
        "unexpected version",
        fmt_expected=lambda values: " or ".join(sorted(values)),
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="1.0.0 or 1.1.0",
    ):
        validator(Meta())


def test_metadata_validator_prefix_accepts_blank(
    validate_packages_module: ModuleType,
) -> None:
    """prefix validator permits blank values."""

    class Meta:
        release = ""

    validator = validate_packages_module._MetadataValidators.prefix(
        "release", "1", "unexpected release"
    )

    validator(Meta())


def test_metadata_validator_prefix_rejects_non_matching(
    validate_packages_module: ModuleType,
) -> None:
    """prefix validator raises when the attribute lacks the prefix."""

    class Meta:
        release = "2.el9"

    validator = validate_packages_module._MetadataValidators.prefix(
        "release", "1", "unexpected release"
    )

    with pytest.raises(
        validate_packages_module.ValidationError,
        match="starting with '1'",
    ):
        validator(Meta())
