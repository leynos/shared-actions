#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = ["cyclopts>=2.9"]
# ///

"""Render the Distribution XML and copy licence assets for productbuild."""

from __future__ import annotations

import shutil
import typing as typ
from pathlib import Path
from textwrap import dedent
from xml.sax.saxutils import escape

from _utils import (
    Parameter,
    action_work_dir,
    configure_app,
    ensure_regular_file,
    run_app,
)

app = configure_app()


@app.default
def main(
    *,
    name: typ.Annotated[str, Parameter(required=True)],
    identifier: typ.Annotated[str, Parameter(required=True)],
    version: typ.Annotated[str, Parameter(required=True)],
    license_file: typ.Annotated[str, Parameter(required=True)],
) -> None:
    """Populate `Resources/` and `dist.xml` for the license panel."""
    work_dir = action_work_dir()
    resources_dir = work_dir / "Resources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    license_path = ensure_regular_file(Path(license_file), "License file")

    dest_license = resources_dir / "License.txt"
    shutil.copy2(license_path, dest_license)
    dest_license.chmod(0o644)

    escaped_name = escape(name, {'"': "&quot;", "'": "&apos;"})
    escaped_id = escape(identifier, {'"': "&quot;", "'": "&apos;"})
    distribution = dedent(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <installer-gui-script minSpecVersion="2">
          <title>{escaped_name}</title>
          <options customize="never" allow-external-scripts="no"/>
          <license file="License.txt"/>
          <choices-outline>
            <line choice="default"/>
          </choices-outline>
          <choice id="default" visible="false" title="{escaped_name}">
            <pkg-ref id="{escaped_id}"/>
          </choice>
          <pkg-ref id="{escaped_id}">build/{name}-{version}-component.pkg</pkg-ref>
        </installer-gui-script>
        """
    ).strip()

    (work_dir / "dist.xml").write_text(distribution + "\n", encoding="utf-8")
    print("Prepared Distribution XML and license resources")


if __name__ == "__main__":
    run_app(app)
