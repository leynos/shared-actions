"""Tests for the validate-linux-packages Cyclopts CLI orchestration."""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import typing as typ
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS_DIR / "validate_cli.py"


@pytest.fixture
def validate_cli_module() -> object:
    """Load the validate_cli module under test."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.append(str(SCRIPTS_DIR))

    module = sys.modules.get("validate_cli")
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location("validate_cli", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        message = "unable to load validate_cli module"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - defensive
        message = f"failed to execute validate_cli module: {exc}"
        raise RuntimeError(message) from exc
    return module


def _write_package(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"archive")


def _prepare_polythene_stub(
    module: object,
    tmp_path: Path,
) -> tuple[list[tuple[str, str]], list[tuple[str, ...]]]:
    calls: list[tuple[str, str]] = []
    exec_calls: list[tuple[str, ...]] = []

    script_path = tmp_path / "polythene.py"
    script_path.write_text("#!/usr/bin/env python\n")

    def _default_path() -> Path:
        return script_path

    def _polythene_stub(
        polythene: Path,
        image: str,
        store: Path,
        *,
        timeout: int | None = None,
    ) -> typ.Iterable[object]:
        calls.append((image, store.as_posix()))
        store.mkdir(parents=True, exist_ok=True)
        root = store / "rootfs"
        root.mkdir(parents=True, exist_ok=True)

        @contextlib.contextmanager
        def _context() -> typ.Iterable[object]:
            class _Session:
                def __init__(self) -> None:
                    self.root = root

                def exec(self, *args: str, _timeout: int | None = None) -> None:
                    exec_calls.append(tuple(args))

            yield _Session()

        return _context()

    module.default_polythene_path = _default_path
    module.polythene_rootfs = _polythene_stub
    return calls, exec_calls


def test_build_config_splits_verify_command(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Normalise verify command strings into tokenised argv entries."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)
    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    inputs = module.ValidationInputs(
        project_dir=project_dir,
        bin_name="tool",
        version="1.0.0",
        formats=["deb"],
        verify_command=["/usr/bin/foo --version", "--flag"],
        polythene_path=polythene_path,
    )

    config = module._build_config(inputs)

    assert config.verify_command == ("/usr/bin/foo", "--version", "--flag")


def test_main_invokes_deb_validation(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke Debian validation when deb format requested."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    package_dir = project_dir / "dist"
    package_path = package_dir / "rust-toy-app_1.2.3-1_amd64.deb"
    _write_package(package_path)

    calls, exec_calls = _prepare_polythene_stub(module, tmp_path)

    monkeypatch.setattr(module, "get_command", lambda _name: object())

    recorded: dict[str, object] = {}

    def _validate_deb_package(
        _command: object,
        candidate_path: Path,
        *,
        expected_name: str,
        expected_version: str,
        expected_deb_version: str,
        expected_arch: str,
        expected_paths: typ.Iterable[str],
        executable_paths: typ.Iterable[str],
        verify_command: tuple[str, ...],
        sandbox_factory: object,
    ) -> None:
        recorded.update(
            {
                "path": candidate_path,
                "name": expected_name,
                "version": expected_version,
                "deb_version": expected_deb_version,
                "arch": expected_arch,
                "paths": tuple(expected_paths),
                "executables": tuple(executable_paths),
                "verify": verify_command,
            }
        )
        with contextlib.ExitStack() as stack:
            ctx = stack.enter_context(sandbox_factory())
            ctx.exec("test", "-e", "/usr/bin/rust-toy-app")

    monkeypatch.setattr(module, "validate_deb_package", _validate_deb_package)

    module.main(
        project_dir=project_dir,
        bin_name="rust-toy-app",
        version="v1.2.3",
        formats=["deb"],
        expected_paths=["/usr/share/man/man1/rust-toy-app.1.gz"],
    )

    out = capsys.readouterr().out
    assert "✓ validated Debian package" in out

    assert recorded["path"] == package_path
    assert recorded["name"] == "rust-toy-app"
    assert recorded["version"] == "1.2.3"
    assert recorded["deb_version"] == "1.2.3-1"
    assert recorded["arch"] == "amd64"
    assert recorded["paths"][0] == "/usr/bin/rust-toy-app"
    assert "/usr/share/man/man1/rust-toy-app.1.gz" in recorded["paths"]
    assert recorded["executables"] == ("/usr/bin/rust-toy-app",)
    assert recorded["verify"] == ()

    assert calls
    assert calls[0][0] == "docker.io/library/debian:bookworm"
    assert exec_calls
    assert exec_calls[0] == ("test", "-e", "/usr/bin/rust-toy-app")


def test_main_invokes_rpm_validation(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Invoke RPM validation when the rpm format is requested."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    package_dir = project_dir / "dist"
    package_path = package_dir / "rust-toy-app-1.2.3-2.x86_64.rpm"
    _write_package(package_path)

    calls, exec_calls = _prepare_polythene_stub(module, tmp_path)

    monkeypatch.setattr(module, "get_command", lambda _name: object())

    recorded: dict[str, object] = {}

    def _validate_rpm_package(
        _command: object,
        candidate_path: Path,
        *,
        expected_name: str,
        expected_version: str,
        expected_release: str,
        expected_arch: str,
        expected_paths: typ.Iterable[str],
        executable_paths: typ.Iterable[str],
        verify_command: tuple[str, ...],
        sandbox_factory: object,
    ) -> None:
        recorded.update(
            {
                "path": candidate_path,
                "name": expected_name,
                "version": expected_version,
                "release": expected_release,
                "arch": expected_arch,
                "paths": tuple(expected_paths),
                "executables": tuple(executable_paths),
                "verify": verify_command,
            }
        )
        with contextlib.ExitStack() as stack:
            ctx = stack.enter_context(sandbox_factory())
            ctx.exec("/usr/bin/rust-toy-app", "--version")

    monkeypatch.setattr(module, "validate_rpm_package", _validate_rpm_package)

    module.main(
        project_dir=project_dir,
        bin_name="rust-toy-app",
        version="1.2.3",
        release="2",
        formats=["rpm"],
        verify_command=["/usr/bin/rust-toy-app", "--version"],
        executable_paths=["/usr/bin/rust-toy-app"],
    )

    assert recorded["path"] == package_path
    assert recorded["name"] == "rust-toy-app"
    assert recorded["version"] == "1.2.3"
    assert recorded["release"] == "2"
    assert recorded["arch"] == "x86_64"
    assert recorded["paths"][0] == "/usr/bin/rust-toy-app"
    assert recorded["executables"] == ("/usr/bin/rust-toy-app",)
    assert recorded["verify"] == ("/usr/bin/rust-toy-app", "--version")

    assert calls
    assert calls[0][0] == "docker.io/library/rockylinux:9"
    assert exec_calls
    assert exec_calls[0] == ("/usr/bin/rust-toy-app", "--version")


def test_handle_deb_invokes_validator(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_handle_deb forwards configuration to validate_deb_package."""
    module = validate_cli_module
    package = tmp_path / "pkg.deb"
    cfg = module.ValidationConfig(
        packages_dir=tmp_path,
        package_value="tool",
        version="1.0.0",
        release="1",
        arch="amd64",
        deb_arch="amd64",
        formats=("deb",),
        expected_paths=("/usr/bin/tool",),
        executable_paths=("/usr/bin/tool",),
        verify_command=(),
        polythene_script=tmp_path / "polythene.py",
        timeout=10,
        base_images={"deb": "debian"},
    )

    recorded: dict[str, object] = {}

    def _capture(
        command: object,
        candidate_path: Path,
        *,
        expected_name: str,
        expected_version: str,
        expected_deb_version: str,
        expected_arch: str,
        expected_paths: typ.Iterable[str],
        executable_paths: typ.Iterable[str],
        verify_command: tuple[str, ...],
        sandbox_factory: object,
    ) -> None:
        recorded.update(
            {
                "command": command,
                "path": candidate_path,
                "name": expected_name,
                "version": expected_version,
                "deb_version": expected_deb_version,
                "arch": expected_arch,
                "paths": tuple(expected_paths),
                "executables": tuple(executable_paths),
                "verify": verify_command,
                "sandbox": sandbox_factory,
            }
        )

    monkeypatch.setattr(module, "validate_deb_package", _capture)

    command_obj = object()

    module._handle_deb(
        command_obj,
        package,
        cfg,
        lambda: contextlib.nullcontext(object()),
    )

    out = capsys.readouterr().out
    assert "✓ validated Debian package" in out
    assert recorded["command"] is command_obj
    assert recorded["path"] == package
    assert recorded["name"] == "tool"
    assert recorded["version"] == "1.0.0"
    assert recorded["deb_version"] == "1.0.0-1"
    assert recorded["arch"] == "amd64"
    assert recorded["paths"] == ("/usr/bin/tool",)
    assert recorded["executables"] == ("/usr/bin/tool",)
    assert recorded["verify"] == ()


def test_handle_rpm_invokes_validator(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_handle_rpm forwards configuration to validate_rpm_package."""
    module = validate_cli_module
    package = tmp_path / "pkg.rpm"
    cfg = module.ValidationConfig(
        packages_dir=tmp_path,
        package_value="tool",
        version="1.0.0",
        release="2",
        arch="amd64",
        deb_arch="amd64",
        formats=("rpm",),
        expected_paths=("/usr/bin/tool",),
        executable_paths=("/usr/bin/tool",),
        verify_command=("tool", "--version"),
        polythene_script=tmp_path / "polythene.py",
        timeout=None,
        base_images={"rpm": "rocky"},
    )

    recorded: dict[str, object] = {}

    def _capture(
        command: object,
        candidate_path: Path,
        *,
        expected_name: str,
        expected_version: str,
        expected_release: str,
        expected_arch: str,
        expected_paths: typ.Iterable[str],
        executable_paths: typ.Iterable[str],
        verify_command: tuple[str, ...],
        sandbox_factory: object,
    ) -> None:
        recorded.update(
            {
                "command": command,
                "path": candidate_path,
                "name": expected_name,
                "version": expected_version,
                "release": expected_release,
                "arch": expected_arch,
                "paths": tuple(expected_paths),
                "executables": tuple(executable_paths),
                "verify": verify_command,
                "sandbox": sandbox_factory,
            }
        )

    monkeypatch.setattr(module, "validate_rpm_package", _capture)

    command_obj = object()

    module._handle_rpm(
        command_obj,
        package,
        cfg,
        lambda: contextlib.nullcontext(object()),
    )

    out = capsys.readouterr().out
    assert "✓ validated RPM package" in out
    assert recorded["command"] is command_obj
    assert recorded["path"] == package
    assert recorded["name"] == "tool"
    assert recorded["version"] == "1.0.0"
    assert recorded["release"] == "2"
    assert recorded["arch"] == "x86_64"
    assert recorded["paths"] == ("/usr/bin/tool",)
    assert recorded["executables"] == ("/usr/bin/tool",)
    assert recorded["verify"] == ("tool", "--version")


def test_format_handlers_register_expected_entries(
    validate_cli_module: object,
) -> None:
    """_FORMAT_HANDLERS map formats onto their handlers and locators."""
    module = validate_cli_module
    deb_handler, deb_locate, deb_command = module._FORMAT_HANDLERS["deb"]
    rpm_handler, rpm_locate, rpm_command = module._FORMAT_HANDLERS["rpm"]

    assert deb_handler is module._handle_deb
    assert deb_locate is module.locate_deb
    assert deb_command == "dpkg-deb"
    assert rpm_handler is module._handle_rpm
    assert rpm_locate is module.locate_rpm
    assert rpm_command == "rpm"


def test_validate_format_dispatches_to_handler(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_validate_format uses the handler table for dispatch."""
    module = validate_cli_module
    called: dict[str, object] = {}
    package_path = tmp_path / "tool.deb"

    def _fake_handler(
        command: object,
        path: Path,
        cfg: object,
        sandbox_factory: typ.Callable[[], typ.ContextManager[object]],
    ) -> None:
        called["command"] = command
        called["path"] = path
        called["cfg"] = cfg
        with sandbox_factory():
            called["sandbox"] = True

    def _fake_locate(package_dir: Path, name: str, version: str, release: str) -> Path:
        called["locate_args"] = (package_dir, name, version, release)
        return package_path

    monkeypatch.setitem(
        module._FORMAT_HANDLERS,
        "deb",
        (_fake_handler, _fake_locate, "custom-cmd"),
    )

    command_obj = object()
    monkeypatch.setattr(module, "get_command", lambda name: command_obj)

    @contextlib.contextmanager
    def _fake_rootfs(
        script: Path,
        image: str,
        store_dir: Path,
        *,
        timeout: int | None = None,
    ) -> typ.Iterator[object]:
        called["rootfs_args"] = (script, image, store_dir, timeout)
        yield object()

    monkeypatch.setattr(module, "polythene_rootfs", _fake_rootfs)

    config = module.ValidationConfig(
        packages_dir=tmp_path,
        package_value="tool",
        version="1.0.0",
        release="1",
        arch="amd64",
        deb_arch="amd64",
        formats=("deb",),
        expected_paths=("/usr/bin/tool",),
        executable_paths=("/usr/bin/tool",),
        verify_command=(),
        polythene_script=tmp_path / "polythene.py",
        timeout=42,
        base_images={"deb": "docker.io/library/debian:bookworm"},
    )

    store_dir = tmp_path / "store"
    module._validate_format("deb", config, store_dir)

    assert called["command"] is command_obj
    assert called["path"] == package_path
    assert called["locate_args"] == (
        config.packages_dir,
        config.package_value,
        config.version,
        config.release,
    )
    assert called["rootfs_args"] == (
        config.polythene_script,
        config.base_images["deb"],
        store_dir,
        config.timeout,
    )
    assert called["cfg"] is config
    assert called["sandbox"] is True


def test_validate_format_rejects_unknown_format(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """_validate_format raises ValidationError for unsupported formats."""
    module = validate_cli_module
    config = module.ValidationConfig(
        packages_dir=tmp_path,
        package_value="tool",
        version="1.0.0",
        release="1",
        arch="amd64",
        deb_arch="amd64",
        formats=("apk",),
        expected_paths=(),
        executable_paths=(),
        verify_command=(),
        polythene_script=tmp_path / "polythene.py",
        timeout=None,
        base_images={},
    )

    with pytest.raises(module.ValidationError, match="unsupported package format"):
        module._validate_format("apk", config, tmp_path / "store")


def test_validate_format_requires_base_image(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """_validate_format raises when the base image is missing."""
    module = validate_cli_module
    config = module.ValidationConfig(
        packages_dir=tmp_path,
        package_value="tool",
        version="1.0.0",
        release="1",
        arch="amd64",
        deb_arch="amd64",
        formats=("deb",),
        expected_paths=(),
        executable_paths=(),
        verify_command=(),
        polythene_script=tmp_path / "polythene.py",
        timeout=None,
        base_images={},
    )

    with pytest.raises(module.ValidationError, match="unsupported package format"):
        module._validate_format("deb", config, tmp_path / "store")


def test_main_invokes_each_requested_format(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """All requested formats are dispatched during validation."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)
    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    recorded: list[tuple[str, Path]] = []

    def _fake_validate(fmt: str, config: object, store_dir: Path) -> None:
        recorded.append((fmt, store_dir))
        assert isinstance(config, module.ValidationConfig)
        assert config.polythene_script == polythene_path

    monkeypatch.setattr(module, "_validate_format", _fake_validate)

    module.main(
        project_dir=project_dir,
        bin_name="tool",
        version="1.0.0",
        formats=["deb", "rpm"],
        polythene_path=polythene_path,
    )

    assert [fmt for fmt, _ in recorded] == ["deb", "rpm"]
    for fmt, store_dir in recorded:
        assert store_dir.name == fmt
        assert store_dir.parent.name.startswith("polythene-validate-")


def test_main_respects_explicit_polythene_store(
    validate_cli_module: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit polythene store directory is reused across formats."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)
    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")
    store_base = tmp_path / "store"

    dispatched: list[Path] = []

    def _capture(fmt: str, config: object, store_dir: Path) -> None:
        dispatched.append(store_dir)
        assert store_dir.is_dir()

    monkeypatch.setattr(module, "_validate_format", _capture)

    module.main(
        project_dir=project_dir,
        bin_name="tool",
        version="1.2.3",
        formats=["deb", "rpm"],
        polythene_path=polythene_path,
        polythene_store=store_base,
    )

    assert dispatched[0].parent == store_base
    assert dispatched[1].parent == store_base
    assert {path.name for path in dispatched} == {"deb", "rpm"}


def test_main_raises_for_missing_package_dir(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Fail when the derived package directory does not exist."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    with pytest.raises(module.ValidationError, match="package directory not found"):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            formats=["deb"],
            polythene_path=polythene_path,
        )


def test_main_raises_for_missing_bin_name(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Reject blank bin-name values."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    with pytest.raises(module.ValidationError, match="bin-name input is required"):
        module.main(
            project_dir=project_dir,
            bin_name="   ",
            version="1.0.0",
            formats=["deb"],
            polythene_path=polythene_path,
        )


def test_main_raises_for_missing_version(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Reject blank version inputs."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    with pytest.raises(module.ValidationError, match="version input is required"):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="   ",
            formats=["deb"],
            polythene_path=polythene_path,
        )


def test_main_raises_for_invalid_sandbox_timeout(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Fail when sandbox-timeout cannot be coerced to an integer."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    with pytest.raises(
        module.ValidationError,
        match="sandbox_timeout must be an integer",
    ):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            formats=["deb"],
            polythene_path=polythene_path,
            sandbox_timeout="abc",
        )


def test_main_raises_for_unsupported_target(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Fail when the target triple is not recognised."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    with pytest.raises(
        module.ValidationError,
        match="unsupported target triple",
    ):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            target="mips-unknown-linux-gnu",
            formats=["deb"],
            polythene_path=polythene_path,
        )


def test_main_raises_for_unsupported_format(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Reject package formats without configured sandbox images."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    package_path = packages_dir / "tool-1.0.0-1.x86_64.rpm"
    _write_package(package_path)

    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    with pytest.raises(
        module.ValidationError,
        match="unsupported package format",
    ):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            formats=["apk"],
            polythene_path=polythene_path,
        )


def test_main_raises_for_missing_polythene_script(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Fail when the polythene helper cannot be located."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(
        module.ValidationError,
        match="polythene script not found",
    ):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            formats=["deb"],
            polythene_path=tmp_path / "missing-polythene.py",
        )


def test_main_raises_when_polythene_unreadable(
    validate_cli_module: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail when the polythene helper cannot be read."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)
    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    original_open = module.Path.open

    def _deny(self: Path, *args: object, **kwargs: object) -> object:
        if self == polythene_path:
            message = "denied"
            raise PermissionError(message)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(module.Path, "open", _deny)

    with pytest.raises(
        module.ValidationError,
        match="polythene script is not readable",
    ):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            formats=["deb"],
            polythene_path=polythene_path,
        )


def test_main_raises_when_polythene_read_fails(
    validate_cli_module: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic OSErrors when reading polythene produce ValidationError."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)
    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    def _broken_open(*_args: object, **_kwargs: object) -> object:
        message = "io error"
        raise OSError(message)

    monkeypatch.setattr(module.Path, "open", _broken_open)

    with pytest.raises(
        module.ValidationError,
        match="polythene script could not be read",
    ):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            formats=["deb"],
            polythene_path=polythene_path,
        )


def test_main_uses_default_polythene_for_blank_input(
    validate_cli_module: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty polythene-path input falls back to the default helper."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    default_polythene = tmp_path / "default-polythene.py"
    default_polythene.write_text("#!/usr/bin/env python\n")

    monkeypatch.setattr(module, "default_polythene_path", lambda: default_polythene)

    captured: list[Path] = []

    def _capture(fmt: str, config: object, store_dir: Path) -> None:
        assert isinstance(config, module.ValidationConfig)
        captured.append(config.polythene_script)

    monkeypatch.setattr(module, "_validate_format", _capture)

    module.main(
        project_dir=project_dir,
        bin_name="tool",
        version="1.0.0",
        formats=["deb"],
        polythene_path=Path(),
    )

    assert captured == [default_polythene]


def test_main_raises_for_empty_formats(
    validate_cli_module: object,
    tmp_path: Path,
) -> None:
    """Fail when no package formats remain after normalisation."""
    module = validate_cli_module
    project_dir = tmp_path / "proj"
    packages_dir = project_dir / "dist"
    packages_dir.mkdir(parents=True, exist_ok=True)

    polythene_path = tmp_path / "polythene.py"
    polythene_path.write_text("#!/usr/bin/env python\n")

    with pytest.raises(module.ValidationError, match="no package formats provided"):
        module.main(
            project_dir=project_dir,
            bin_name="tool",
            version="1.0.0",
            formats=["  \n\t"],
            polythene_path=polythene_path,
        )
