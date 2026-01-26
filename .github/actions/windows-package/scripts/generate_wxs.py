"""Render default WiX authoring for the windows-package action."""

from __future__ import annotations

import dataclasses
import os
import sys
import types
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter
from syspath_hack import SysPathMode, ensure_module_dir

try:
    from cyclopts.exceptions import UsageError  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - compatibility with older cyclopts
    from cyclopts.exceptions import CycloptsError as UsageError

if __package__ in {None, ""}:
    _MODULE_DIR = ensure_module_dir(__file__, mode=SysPathMode.PREPEND)

from windows_installer import (
    FileSpecification,
    TemplateError,
    parse_file_specification,
    prepare_template_options,
    render_default_wxs,
)

_SELF_MODULE: types.ModuleType
if __name__ not in sys.modules:
    _SELF_MODULE = types.ModuleType(__name__)
    sys.modules[__name__] = _SELF_MODULE
else:
    _SELF_MODULE = sys.modules[__name__]

app = App(
    config=cyclopts.config.Env("INPUT_", command=False),
)


@dataclasses.dataclass(frozen=True)
class WxsConfiguration:
    """Configuration required to generate WiX authoring."""

    version: str
    architecture: str
    application: str


@dataclasses.dataclass(frozen=True)
class WxsMetadata:
    """Optional metadata applied when rendering WiX authoring."""

    product_name: str | None = None
    manufacturer: str | None = None
    install_dir_name: str | None = None
    description: str | None = None
    upgrade_code: str | None = None
    license_path: str | None = None
    additional_files: list[str] | None = None


_SELF_MODULE.__dict__.update(globals())


def _parse_additional_files(values: list[str] | None) -> list[FileSpecification]:
    return [
        parse_file_specification(stripped)
        for spec in (values or [])
        if (stripped := spec.strip())
    ]


def _generate_wxs_file(
    *,
    output: Path,
    config: WxsConfiguration,
    metadata: WxsMetadata | None = None,
) -> Path:
    """Generate WiX authoring and return the absolute output path."""
    output_path = output if output.is_absolute() else Path.cwd() / output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    meta = metadata or WxsMetadata()
    app_spec = parse_file_specification(config.application)
    extras = _parse_additional_files(meta.additional_files)

    options = prepare_template_options(
        version=config.version,
        architecture=config.architecture,
        application=app_spec,
        product_name=meta.product_name,
        manufacturer=meta.manufacturer,
        install_dir_name=meta.install_dir_name,
        description=meta.description,
        upgrade_code=meta.upgrade_code,
        additional_files=extras,
        license_path=meta.license_path,
    )

    authoring = render_default_wxs(options)
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(authoring)
    return output_path


@app.default
def main(
    *,
    output: typ.Annotated[Path, Parameter(required=True)],
    version: typ.Annotated[str, Parameter(required=True)],
    architecture: typ.Annotated[str, Parameter(required=True)],
    application: typ.Annotated[str, Parameter(required=True)],
    product_name: str | None = None,
    manufacturer: str | None = None,
    install_dir_name: str | None = None,
    description: str | None = None,
    upgrade_code: str | None = None,
    license_path: str | None = None,
    additional_file: list[str] | None = None,
) -> None:
    """Generate WiX authoring for ``application`` and supporting files."""
    try:
        config = WxsConfiguration(
            version=version,
            architecture=architecture,
            application=application,
        )
        metadata = WxsMetadata(
            product_name=product_name,
            manufacturer=manufacturer,
            install_dir_name=install_dir_name,
            description=description,
            upgrade_code=upgrade_code,
            license_path=license_path,
            additional_files=additional_file,
        )
        output_path = _generate_wxs_file(
            output=output,
            config=config,
            metadata=metadata,
        )
    except TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(str(output_path))


if __name__ == "__main__":
    argv = sys.argv[1:]

    def _extract_version_from_key_value(values: list[str]) -> str | None:
        """Extract version from --version=VALUE format."""
        for value in values:
            if value.startswith("--version="):
                candidate = value.split("=", 1)[1]
                return candidate if candidate else None
        return None

    def _extract_version_from_flag_arg(values: list[str]) -> str | None:
        """Extract version from --version VALUE format."""
        for index, value in enumerate(values):
            if value == "--version" and index + 1 < len(values):
                candidate = values[index + 1]
                return candidate if candidate else None
        return None

    def _extract_version_argument(values: list[str]) -> str | None:
        """Extract version argument from command-line values."""
        version = _extract_version_from_key_value(values)
        if version:
            return version
        return _extract_version_from_flag_arg(values)

    env_version = os.environ.get("INPUT_VERSION")
    if not (env_version and env_version.strip()):
        provided_version = _extract_version_argument(argv)
        if not provided_version:
            print(
                "A version must be provided via the INPUT_VERSION environment "
                "variable or the --version flag (e.g. --version 1.2.3)."
            )
            raise SystemExit(2)

    try:
        app()
    except UsageError as exc:  # pragma: no cover - defensive
        message = str(exc)
        safe_message = (
            message.replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2018", "'")
            .replace("\u2019", "'")
        )
        stream = sys.stdout if getattr(exc, "use_stdout", False) else sys.stderr
        print(safe_message, file=stream)
        raise SystemExit(getattr(exc, "exit_code", getattr(exc, "code", 2))) from exc
