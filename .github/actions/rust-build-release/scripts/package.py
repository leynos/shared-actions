#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "typer>=0.12",
#   "plumbum>=1.8",
# ]
# ///
"""
Minimal nFPM packager for Rust binaries, with manpage support.

Examples:
  uv run package.py --name rust-toy-app --bin-name rust-toy-app \
    --target x86_64-unknown-linux-gnu --version 1.2.3 \
    --formats deb,rpm \
    --man doc/rust-toy-app.1 --man doc/rust-toy-app-subcmd.1

Assumes the binary already exists at:
  target/<target>/release/<bin-name>
"""

from __future__ import annotations

import gzip
import re
import textwrap
from pathlib import Path
from string import Template
from typing import List

import typer
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from script_utils import ensure_directory, ensure_exists, run_cmd

app = typer.Typer(add_completion=False, no_args_is_help=True)

NFPM_TEMPLATE = Template(
    textwrap.dedent("""\
    name: ${name}
    arch: ${arch}
    platform: linux
    version: ${version}
    release: ${release}
    section: ${section}
    priority: optional
    maintainer: ${maintainer}
    homepage: ${homepage}
    license: ${license}
    description: |
      ${description}
    contents:
      - src: ${binary_path}
        dst: /usr/bin/${bin_name}
        file_info:
          mode: 0755${license_block}${man_block}
    overrides:
      deb:
        depends: [${deb_depends}]
      rpm:
        depends: [${rpm_depends}]
    """)
)

SECTION_RE = re.compile(
    r"\.(\d[\w-]*)($|\.gz$)"
)  # captures e.g. ".1", ".1p", ".8r", with/without .gz


def map_target_to_arch(target: str) -> str:
    """Map a Rust target triple to nFPM/GOARCH arch strings."""
    t = target.lower()
    if t.startswith(("x86_64-", "x86_64_")):
        return "amd64"
    if t.startswith(("aarch64-", "arm64-")):
        return "arm64"
    if t.startswith(("i686-", "i586-", "i386-")):
        return "386"
    if t.startswith(("armv7-", "armv6-", "arm-")):
        return "arm"  # GOARM nuance out of scope here
    return "amd64"


def comma_join(items: List[str] | None) -> str:
    return ", ".join(items) if items else ""


def infer_section(path: Path, default: str) -> str:
    m = SECTION_RE.search(path.name)
    return m.group(1) if m else default


def stem_without_section(filename: str) -> str:
    """Drop trailing .<section>(.gz)? once; keep the rest intact."""
    name = filename
    if name.endswith(".gz"):
        name = name[:-3]
    return SECTION_RE.sub("", name)


def ensure_gz(src: Path, dst_dir: Path) -> Path:
    """Return a .gz file path; if src already .gz, copy path through; else gzip into dst_dir."""
    if src.suffix == ".gz":
        return src
    ensure_directory(dst_dir)
    gz_path = dst_dir / (src.name + ".gz")
    with (
        open(src, "rb") as fin,
        gzip.GzipFile(filename=gz_path.as_posix(), mode="wb", mtime=0) as fout,
    ):
        fout.write(fin.read())
    return gz_path


def render_man_block(
    man_sources: List[Path],
    package_name: str,
    default_section: str,
    out_staging: Path,
) -> str:
    """
    Convert user-provided manpaths into YAML entries under 'contents:'.
    Returns a string that is already indented to align under the template.
    """
    if not man_sources:
        return ""

    lines: List[str] = []
    for src in man_sources:
        ensure_exists(src, "manpage not found")
        section = infer_section(src, default_section)
        # Example: src "doc/rust-toy-app.1" -> name "rust-toy-app", section "1"
        base_no_section = stem_without_section(src.name)
        # Destination filename should be "<name>.<section>.gz"
        dest_filename = f"{base_no_section}.{section}.gz"
        dest_dir = f"/usr/share/man/man{section}"
        # Ensure gzipped file exists (in a predictable staging dir near the config)
        gz = ensure_gz(src, out_staging)
        # YAML chunk for this manpage
        lines.append(
            textwrap.dedent(f"""\
              - src: {gz.as_posix()}
                dst: {dest_dir}/{dest_filename}
                file_info:
                  mode: 0644""")
        )

    # Two-space indent to sit under "contents:" list (which already has 6 spaces in the template).
    block = "".join(
        "  " + line if line.strip() else line
        for line in "".join(lines).splitlines(True)
    )
    return (
        "\n" + block
    )  # leading newline to append cleanly after license or binary entry


@app.command()
def main(
    name: str = typer.Option(..., "--name", help="Package name."),
    bin_name: str = typer.Option(..., "--bin-name", help="Installed binary name."),
    target: str = typer.Option(
        "x86_64-unknown-linux-gnu",
        "--target",
        help="Rust target triple used for the build.",
    ),
    version: str = typer.Option(
        ..., "--version", help="Version (Debian-friendly: starts with a digit)."
    ),
    release: str = typer.Option("1", "--release", help="Package release/revision."),
    arch: str | None = typer.Option(
        None, "--arch", help="Override nFPM/GOARCH arch (e.g. amd64, arm64)."
    ),
    formats: str = typer.Option(
        "deb,rpm",
        "--formats",
        help="Comma-separated list: deb,rpm,apk,archlinux,ipk,srpm",
    ),
    outdir: Path = typer.Option(
        Path("dist"), "--outdir", help="Where to place packages."
    ),
    maintainer: str = typer.Option("Your Name <you@example.com>", "--maintainer"),
    homepage: str = typer.Option("https://example.com", "--homepage"),
    license_: str = typer.Option("MIT", "--license"),
    section: str = typer.Option("utils", "--section"),
    description: str = typer.Option("A fast toy app written in Rust.", "--description"),
    deb_depends: List[str] = typer.Option(
        None, "--deb-depends", help="Repeatable. Debian runtime deps."
    ),
    rpm_depends: List[str] = typer.Option(
        None, "--rpm-depends", help="Repeatable. RPM runtime deps."
    ),
    binary_dir: Path = typer.Option(
        Path("target"), "--binary-dir", help="Root of Cargo target dir."
    ),
    config_out: Path = typer.Option(
        Path("dist/nfpm.yaml"),
        "--config-out",
        help="Path to write generated nfpm.yaml.",
    ),
    # Manpage bits:
    man: List[Path] = typer.Option(
        None,
        "--man",
        help="Repeatable. Paths to manpages (e.g. doc/app.1 or app.1.gz).",
    ),
    man_section: str = typer.Option(
        "1", "--man-section", help="Default man section if the filename lacks one."
    ),
    man_stage: Path = typer.Option(
        Path("dist/.man"), "--man-stage", help="Where to stage gzipped manpages."
    ),
) -> None:
    # Normalise/derive fields.
    ver = version.lstrip("v")  # Debian wants a digit first.
    arch_val = arch or map_target_to_arch(target)
    bin_path = binary_dir / target / "release" / bin_name
    ensure_exists(bin_path, "built binary not found; build first")
    ensure_directory(outdir)
    ensure_directory(config_out.parent)

    # Optional LICENSE content mapping (if present).
    license_file = Path("LICENSE")
    if license_file.exists():
        license_entry = textwrap.dedent(f"""\
          - src: {license_file.as_posix()}
            dst: /usr/share/doc/{name}/copyright
            file_info:
              mode: 0644
        """)
        # Indent to align under 'contents:'
        license_block = "".join(
            "  " + line if line.strip() else line
            for line in license_entry.splitlines(True)
        )
        license_block = (
            "\n" + license_block
        )  # leading newline to come after the binary entry
    else:
        license_block = ""

    # Manpage contents entries (already indented and newline-prefixed)
    man_block = render_man_block(
        man or [], name, man_section, ensure_directory(man_stage)
    )

    # Render nfpm.yaml from template.
    yaml_text = NFPM_TEMPLATE.substitute(
        name=name,
        arch=arch_val,
        version=ver,
        release=release,
        section=section,
        maintainer=maintainer,
        homepage=homepage,
        license=license_,
        description=description,
        binary_path=bin_path.as_posix(),
        bin_name=bin_name,
        license_block=license_block,
        man_block=man_block,
        deb_depends=comma_join(deb_depends),
        rpm_depends=comma_join(rpm_depends or deb_depends),
    )
    config_out.write_text(yaml_text, encoding="utf-8")
    typer.secho(f"Wrote {config_out}", fg=typer.colors.GREEN)

    # Ensure nfpm is available.
    try:
        nfpm = local["nfpm"]
    except Exception as e:  # noqa: BLE001
        typer.secho(
            "error: nfpm not found in PATH. Install it first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(127) from e

    # Run nfpm for each requested format.
    rc_any = 0
    for fmt in [s.strip() for s in formats.split(",") if s.strip()]:
        typer.echo(f"→ nfpm package -p {fmt} -f {config_out} -t {outdir}/")
        try:
            run_cmd(
                nfpm[
                    "package",
                    "-p",
                    fmt,
                    "-f",
                    str(config_out),
                    "-t",
                    str(outdir),
                ]
            )
        except ProcessExecutionError as pe:
            rc_any = rc_any or pe.retcode
            typer.secho(
                f"nfpm failed for format '{fmt}' (exit {pe.retcode})",
                fg=typer.colors.RED,
                err=True,
            )
        else:
            typer.secho(f"✓ built {fmt} packages in {outdir}", fg=typer.colors.GREEN)

    raise typer.Exit(rc_any)


if __name__ == "__main__":
    app()
