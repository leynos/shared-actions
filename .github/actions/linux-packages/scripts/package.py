#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=2.9,<3.0",
#   "plumbum>=1.8,<2.0",
#   "pyyaml>=6.0,<7.0",
#   "typer>=0.9,<1.0",
# ]
# ///
"""
Minimal nFPM packager for Rust binaries, with manpage support.

Examples
--------
  uv run package.py --bin-name rust-toy-app \
    --target x86_64-unknown-linux-gnu --version 1.2.3 \
    --formats deb rpm \
    --man-paths doc/rust-toy-app.1 doc/rust-toy-app-subcmd.1

Assumes the binary already exists at:
  target/<target>/release/<bin-name>
"""

from __future__ import annotations

import gzip
import os
import re
import shlex
import stat
import sys
import typing as typ
from pathlib import Path

import cyclopts
import yaml
from cyclopts import App, Parameter
from plumbum.commands.processes import ProcessExecutionError

if typ.TYPE_CHECKING:
    from .architectures import UnsupportedTargetError, nfpm_arch_for_target
    from .script_utils import (
        ensure_directory,
        ensure_exists,
        get_command,
        run_cmd,
    )
else:  # pragma: no cover - runtime fallback when executed as a script
    try:
        from .architectures import UnsupportedTargetError, nfpm_arch_for_target
        from .script_utils import (
            ensure_directory,
            ensure_exists,
            get_command,
            run_cmd,
        )
    except ImportError:  # pragma: no cover - fallback for direct execution
        from script_utils import load_script_helpers

        helpers = load_script_helpers()
        ensure_directory = helpers.ensure_directory
        ensure_exists = helpers.ensure_exists
        get_command = helpers.get_command
        run_cmd = helpers.run_cmd
        arch_helpers = typ.cast("typ.Any", __import__("architectures"))
        nfpm_arch_for_target = arch_helpers.nfpm_arch_for_target
        UnsupportedTargetError = arch_helpers.UnsupportedTargetError


class PackagingError(RuntimeError):
    """Raised when packaging inputs cannot be processed."""

    @classmethod
    def unsupported_target(cls, target: str) -> PackagingError:
        """Return an error describing an unsupported target triple."""
        return cls(f"unsupported target triple: {target}")

    @classmethod
    def invalid_mode(cls, mode: str, target_desc: str) -> PackagingError:
        """Return an error describing an invalid file mode entry."""
        return cls(f"invalid file mode '{mode}' for {target_desc}")

    @classmethod
    def missing_bin(cls) -> PackagingError:
        """Return an error indicating the binary name input was omitted."""
        return cls("bin-name input is required")

    @classmethod
    def missing_version(cls) -> PackagingError:
        """Return an error indicating the version input was omitted."""
        return cls("version input is required")

    @classmethod
    def missing_formats(cls) -> PackagingError:
        """Return an error describing a missing packaging format list."""
        return cls("no packaging formats provided")


SECTION_RE = re.compile(r"\.(\d[\w-]*)($|\.gz$)")

app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
existing_config = getattr(app, "config", ()) or ()
app.config = (*tuple(existing_config), _env_config)


def _fail(message: str, *, code: int = 2) -> typ.NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


class OctalInt(int):
    """Integer subclass that renders as a zero-padded octal literal."""

    def __new__(cls, value: int, *, width: int = 4) -> OctalInt:
        """Initialise the integer and remember the desired octal width."""
        obj = super().__new__(cls, value)
        obj._octal_width = width
        return obj


def _ensure_executable_permissions(path: Path) -> int | None:
    """Ensure ``path`` is executable on POSIX systems without clobbering other bits.

    Returns the resulting numeric mode when it can be determined, or ``None`` when
    execution permissions are not adjusted (e.g. on Windows) or when the current
    file mode cannot be read.
    """
    if os.name == "nt":  # pragma: no cover - exercised on Windows runners only
        return None

    try:
        current_mode = path.stat().st_mode
    except OSError:  # pragma: no cover - defensive guard for transient IO errors
        return None

    exec_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    desired_mode = current_mode | exec_bits
    if desired_mode == current_mode:
        return current_mode

    path.chmod(desired_mode)
    return desired_mode


def _represent_octal_int(dumper: yaml.Dumper, data: OctalInt) -> yaml.ScalarNode:
    width = getattr(data, "_octal_width", 4)
    return dumper.represent_scalar(
        "tag:yaml.org,2002:int", format(int(data), f"0{width}o")
    )


yaml.SafeDumper.add_representer(OctalInt, _represent_octal_int)


def infer_section(path: Path, default: str) -> str:
    """Extract the manpage section from ``path`` or fall back to ``default``."""
    m = SECTION_RE.search(path.name)
    return m.group(1) if m else default


def stem_without_section(filename: str) -> str:
    """Drop trailing .<section>(.gz)? once; keep the rest intact."""
    name = filename.removesuffix(".gz")
    return SECTION_RE.sub("", name)


def ensure_gz(src: Path, dst_dir: Path) -> Path:
    """Return a gzipped file path in ``dst_dir`` for ``src``."""
    if src.suffix == ".gz":
        return src
    ensure_directory(dst_dir)
    gz_path = dst_dir / f"{src.name}.gz"
    with (
        src.open("rb") as fin,
        gz_path.open("wb") as fout,
        gzip.GzipFile(
            filename="",
            fileobj=fout,
            mode="wb",
            mtime=0,
            compresslevel=9,
        ) as gz,
    ):
        gz.write(fin.read())
    return gz_path


def normalise_file_modes(entries: list[dict[str, typ.Any]]) -> list[dict[str, typ.Any]]:
    """Convert string ``mode`` values to octal-preserving integers."""
    normalised: list[dict[str, typ.Any]] = []
    for entry in entries:
        new_entry = dict(entry)
        file_info = entry.get("file_info")
        if file_info:
            new_info = dict(file_info)
            mode = new_info.get("mode")
            if isinstance(mode, str):
                cleaned = mode.strip()
                try:
                    value = int(cleaned, 8)
                except ValueError as exc:
                    target_desc = new_entry.get("dst") or new_entry.get("src", "entry")
                    raise PackagingError.invalid_mode(mode, target_desc) from exc
                new_info["mode"] = OctalInt(value, width=len(cleaned))
            new_entry["file_info"] = new_info
        normalised.append(new_entry)
    return normalised


def build_man_entries(
    man_sources: list[Path],
    default_section: str,
    stage_dir: Path,
) -> list[dict[str, typ.Any]]:
    """Return nFPM ``contents`` entries for the provided man pages."""
    if not man_sources:
        return []

    entries: list[dict[str, typ.Any]] = []
    stage = ensure_directory(stage_dir)
    for src in man_sources:
        ensure_exists(src, "manpage not found")
        section = infer_section(src, default_section)
        base_no_section = stem_without_section(src.name)
        dest_filename = f"{base_no_section}.{section}.gz"
        dest_path = f"/usr/share/man/man{section}/{dest_filename}"
        gz = ensure_gz(src, stage)
        entries.append(
            {
                "src": gz.as_posix(),
                "dst": dest_path,
                "file_info": {"mode": "0644"},
            }
        )
    return entries


def _normalise_list(values: list[str] | None, *, default: list[str]) -> list[str]:
    entries: list[str] = []
    source = values if values is not None else default
    for item in source:
        for token in re.split(r"[\s,]+", item.strip()):
            if not token:
                continue
            if token not in entries:
                entries.append(token)
    return entries


def _coerce_optional_path(
    value: Path | None, env_var: str, *, fallback: Path | None = None
) -> Path | None:
    """Return a cleaned path, honouring blank environment overrides."""
    raw = os.environ.get(env_var)
    if raw is not None and not raw.strip():
        return fallback
    if value is None:
        return fallback
    text = str(value).strip()
    return fallback if not text else Path(text)


def _coerce_path_list(values: list[Path] | None, env_var: str) -> list[Path]:
    """Return cleaned path list honouring blank env defaults."""
    raw = os.environ.get(env_var)
    if raw is not None and not raw.strip():
        return []
    cleaned: list[Path] = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        cleaned.append(Path(text))
    return cleaned


@app.default
def main(
    *,
    package_name: str | None = None,
    bin_name: typ.Annotated[str, Parameter(required=True)],
    target: str = "x86_64-unknown-linux-gnu",
    version: typ.Annotated[str, Parameter(required=True)],
    formats: list[str] | None = None,
    release: str | None = None,
    arch: str | None = None,
    maintainer: str | None = None,
    homepage: str | None = None,
    license_: typ.Annotated[str | None, Parameter(env_var="INPUT_LICENSE")] = None,
    section: str | None = None,
    description: str | None = None,
    man_paths: typ.Annotated[
        list[Path] | None, Parameter(env_var="INPUT_MAN_PATHS")
    ] = None,
    man_section: str | None = None,
    man_stage: Path | None = None,
    outdir: Path | None = None,
    binary_dir: Path | None = None,
    config_out: typ.Annotated[
        Path | None, Parameter(env_var="INPUT_CONFIG_PATH")
    ] = None,
    deb_depends: list[str] | None = None,
    rpm_depends: list[str] | None = None,
) -> None:
    """Build packages for a Rust binary using nFPM configuration derived from inputs."""
    bin_value = bin_name.strip()
    if not bin_value:
        raise PackagingError.missing_bin()
    package_value = (package_name or bin_value).strip() or bin_value
    version_value = version.strip().lstrip("v")
    if not version_value:
        raise PackagingError.missing_version()

    target_value = target.strip() or "x86_64-unknown-linux-gnu"
    release_value = (release or "1").strip() or "1"
    try:
        arch_value = (arch or nfpm_arch_for_target(target_value)).strip()
    except UnsupportedTargetError as exc:
        raise PackagingError.unsupported_target(target_value) from exc

    binary_root = _coerce_optional_path(
        binary_dir,
        "INPUT_BINARY_DIR",
        fallback=Path("target"),
    ) or Path("target")
    outdir_path = _coerce_optional_path(
        outdir,
        "INPUT_OUTDIR",
        fallback=Path("dist"),
    ) or Path("dist")
    config_out_path = _coerce_optional_path(
        config_out,
        "INPUT_CONFIG_PATH",
        fallback=Path("dist/nfpm.yaml"),
    ) or Path("dist/nfpm.yaml")
    man_section_value = (man_section or "1").strip() or "1"
    man_stage_path = _coerce_optional_path(
        man_stage,
        "INPUT_MAN_STAGE",
        fallback=Path("dist/.man"),
    ) or Path("dist/.man")

    bin_path = binary_root / target_value / "release" / bin_value
    ensure_exists(bin_path, "built binary not found; build first")
    source_mode = _ensure_executable_permissions(bin_path)
    if source_mode is None:
        source_mode = bin_path.stat().st_mode
    source_mode = stat.S_IMODE(source_mode)
    print(
        f"DEBUG: Source binary permissions: {oct(source_mode)}",
        file=sys.stderr,
    )
    ensure_directory(outdir_path)
    ensure_directory(config_out_path.parent)

    license_file = Path("LICENSE")
    contents: list[dict[str, typ.Any]] = [
        {
            "src": bin_path.as_posix(),
            "dst": f"/usr/bin/{bin_value}",
            "file_info": {"mode": "0755"},
        }
    ]
    if license_file.exists():
        contents.append(
            {
                "src": license_file.as_posix(),
                "dst": f"/usr/share/doc/{package_value}/copyright",
                "file_info": {"mode": "0644"},
            }
        )

    man_sources = _coerce_path_list(man_paths, "INPUT_MAN_PATHS")
    man_entries = build_man_entries(man_sources, man_section_value, man_stage_path)
    contents.extend(man_entries)

    deb_requires = _normalise_list(deb_depends, default=[])
    rpm_requires = (
        _normalise_list(rpm_depends, default=[])
        if rpm_depends is not None
        else list(deb_requires)
    )

    config: dict[str, typ.Any] = {
        "name": package_value,
        "arch": arch_value,
        "platform": "linux",
        "version": version_value,
        "release": release_value,
        "section": (section or "").strip(),
        "priority": "optional",
        "maintainer": (maintainer or "").strip(),
        "homepage": (homepage or "").strip(),
        "license": (license_ or "").strip(),
        "description": (description or "").strip(),
        "contents": normalise_file_modes(contents),
        "overrides": {
            "deb": {"depends": deb_requires},
            "rpm": {"depends": rpm_requires},
        },
    }

    config_out_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {config_out_path}")

    nfpm = get_command("nfpm")

    resolved_formats = _normalise_list(formats, default=["deb"])
    if not resolved_formats:
        raise PackagingError.missing_formats()

    failures: list[tuple[str, int]] = []
    single_format = len(resolved_formats) == 1
    for fmt in resolved_formats:
        cmd = nfpm[
            "package",
            "--packager",
            fmt,
            "-f",
            str(config_out_path),
            "-t",
            str(outdir_path),
        ]
        print(f"→ {shlex.join(cmd.formulate())}")
        try:
            run_cmd(cmd)
        except ProcessExecutionError as pe:
            retcode = int(pe.retcode or 1)
            print(
                f"error: nfpm failed for format '{fmt}' (exit {retcode})",
                file=sys.stderr,
            )
            if single_format:
                raise SystemExit(retcode) from pe
            failures.append((fmt, retcode))
        else:
            print(f"✓ built {fmt} packages in {outdir_path}")

    if failures:
        for fmt, retcode in failures:
            print(
                f"format '{fmt}' failed with exit code {retcode}",
                file=sys.stderr,
            )
        raise SystemExit(failures[0][1])

    # Verify packaged binary permissions
    if "deb" in resolved_formats:
        deb_files = list(outdir_path.glob("*.deb"))
        if deb_files:
            try:
                dpkg_deb = get_command("dpkg-deb")
                for deb_path in deb_files:
                    print(
                        f"DEBUG: Inspecting {deb_path.name}",
                        file=sys.stderr,
                    )
                    _, stdout, _ = dpkg_deb["--contents", str(deb_path)].run()
                    for line in stdout.splitlines():
                        if bin_value in line:
                            print(f"DEBUG: {line}", file=sys.stderr)
            except Exception as e:  # noqa: BLE001  # pragma: no cover - debug output only
                print(
                    f"DEBUG: Package inspection failed: {e}",
                    file=sys.stderr,
                )


def run() -> None:
    """Execute the CLI, mapping packaging errors to user-friendly exits."""
    try:
        app()
    except PackagingError as exc:
        _fail(str(exc))


if __name__ == "__main__":
    run()
