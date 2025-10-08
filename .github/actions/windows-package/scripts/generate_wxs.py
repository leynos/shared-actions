"""Render default WiX authoring for the windows-package action."""

from __future__ import annotations

import sys
import typing as typ
from pathlib import Path

import cyclopts
from cyclopts import App, Parameter

if __package__ in {None, ""}:
    _MODULE_DIR = Path(__file__).resolve().parent
    _MODULE_DIR_STR = str(_MODULE_DIR)
    if _MODULE_DIR_STR not in sys.path:
        sys.path.insert(0, _MODULE_DIR_STR)

from windows_installer import (
    FileSpecification,
    TemplateError,
    parse_file_specification,
    prepare_template_options,
    render_default_wxs,
)

app = App(config=cyclopts.config.Env("INPUT_", command=False))  # type: ignore[unknown-argument]


def _parse_additional_files(values: list[str] | None) -> list[FileSpecification]:
    if not values:
        return []
    files: list[FileSpecification] = []
    for spec in values:
        spec = spec.strip()
        if not spec:
            continue
        files.append(parse_file_specification(spec))
    return files


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
    output_path = output if output.is_absolute() else Path.cwd() / output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    app_spec = parse_file_specification(application)
    extras = _parse_additional_files(additional_file)

    try:
        options = prepare_template_options(
            version=version,
            architecture=architecture,
            application=app_spec,
            product_name=product_name,
            manufacturer=manufacturer,
            install_dir_name=install_dir_name,
            description=description,
            upgrade_code=upgrade_code,
            additional_files=extras,
            license_path=license_path,
        )
    except TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    authoring = render_default_wxs(options)
    output_path.write_text(authoring, encoding="utf-8", newline="\n")
    print(str(output_path))


if __name__ == "__main__":
    app()
