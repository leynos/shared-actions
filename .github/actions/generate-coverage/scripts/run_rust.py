#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer", "lxml"]
# ///
"""Run Rust coverage using ``cargo llvm-cov`` and optional ``cargo nextest``."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import selectors
import shlex
import subprocess
import sys
import threading
import time
import typing as typ
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import _cargo_runner
import typer
from _cargo_runner import _run_cargo
from _cranelift import _CARGO_COVERAGE_ENV_UNSETS, get_cargo_coverage_env
from cmd_utils_loader import run_cmd
from coverage_parsers import get_line_coverage_percent_from_lcov
from plumbum.cmd import cargo
from plumbum.commands.processes import ProcessExecutionError
from shared_utils import read_previous_coverage

logger = logging.getLogger(__name__)
_cargo_runner_run_cargo = _run_cargo

try:  # runtime import for graceful fallback
    from lxml import etree
except ImportError as exc:  # pragma: no cover - fail fast if dependency missing
    typer.echo(
        "lxml is required for Cobertura parsing. Install with 'pip install lxml'.",
        err=True,
    )
    raise typer.Exit(1) from exc

if os.name == "nt":
    debug = os.getenv("RUN_RUST_DEBUG")
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
                continue
            except (
                AttributeError,
                ValueError,
                io.UnsupportedOperation,
                OSError,
            ) as exc:  # pragma: no cover - emit debug info when requested
                if debug:
                    logger.debug("Failed to reconfigure %s: %s", name, exc)
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            try:
                wrapped = io.TextIOWrapper(
                    buf,
                    encoding="utf-8",
                    errors="replace",
                    write_through=True,
                )
                setattr(sys, name, wrapped)
            except (ValueError, OSError) as exc:  # pragma: no cover
                if debug:
                    logger.debug("Failed to wrap %s: %s", name, exc)
        elif debug:
            logger.debug("%s has no buffer; leaving as-is", name)

OUTPUT_PATH_OPT = typer.Option(..., envvar="INPUT_OUTPUT_PATH")
FEATURES_OPT = typer.Option("", envvar="INPUT_FEATURES")
WITH_DEFAULT_OPT = typer.Option(default=True, envvar="INPUT_WITH_DEFAULT_FEATURES")
USE_NEXTEST_OPT = typer.Option(default=True, envvar="INPUT_USE_CARGO_NEXTEST")
LANG_OPT = typer.Option(..., envvar="DETECTED_LANG")
FMT_OPT = typer.Option(..., envvar="DETECTED_FMT")
MANIFEST_PATH_OPT = typer.Option(Path("Cargo.toml"), envvar="DETECTED_CARGO_MANIFEST")
GITHUB_OUTPUT_OPT = typer.Option(..., envvar="GITHUB_OUTPUT")
CUCUMBER_RS_FEATURES_OPT = typer.Option("", envvar="INPUT_CUCUMBER_RS_FEATURES")
CUCUMBER_RS_ARGS_OPT = typer.Option("", envvar="INPUT_CUCUMBER_RS_ARGS")
WITH_CUCUMBER_RS_OPT = typer.Option(default=False, envvar="INPUT_WITH_CUCUMBER_RS")
BASELINE_OPT = typer.Option(None, envvar="BASELINE_RUST_FILE")

NEXTEST_CONFIG_PATH = Path(".config/nextest.toml")
NEXTEST_DEFAULT_CONFIG = """[profile.default]
# Default slow timeout is 180s; nextest sends SIGTERM after the period and
# SIGKILL after the grace-period. The settings integration test binary
# (tests/settings.rs) is capped at 30s.
slow-timeout = { period = "180s", terminate-after = 1, grace-period = "5s" }

# Put a hard ceiling on the whole run.
global-timeout = "10m"
"""


def _run_cargo(
    args: list[str],
    *,
    env_overrides: typ.Mapping[str, str] | None = None,
    env_unsets: typ.Iterable[str] = (),
) -> str:
    """Run ``cargo`` with ``args`` streaming output and return ``stdout``.

    Builds a subprocess environment by copying ``os.environ``, removing every
    key in ``env_unsets``, and — when ``env_overrides`` is not ``None`` —
    merging its entries into that copy (overrides take precedence). The
    resulting environment is passed to the spawned ``cargo`` process, whose
    stdout and stderr are streamed to the current process.

    Parameters
    ----------
    args : list[str]
        Arguments forwarded verbatim to ``cargo``.
    env_overrides : Mapping[str, str] | None, optional
        Extra or replacement environment variables. When ``None`` (the
        default), the environment is inherited unchanged except for any
        ``env_unsets`` removals.
    env_unsets : Iterable[str], optional
        Variable names to remove from the inherited environment before
        ``env_overrides`` are applied. Missing keys are silently ignored.
        Unsets are performed before overrides, so ``env_overrides`` can
        unconditionally set a variable that may or may not have been
        inherited.

    Returns
    -------
    str
        Captured stdout from the ``cargo`` invocation.
    """
    _cargo_runner.cargo = cargo
    _cargo_runner.selectors = selectors
    _cargo_runner.threading = threading
    _cargo_runner.time = time
    return _cargo_runner_run_cargo(
        args,
        env_overrides=env_overrides,
        env_unsets=env_unsets,
    )


def get_cargo_coverage_cmd(
    fmt: str,
    out: Path,
    features: str,
    *,
    manifest_path: Path,
    with_default: bool,
    use_nextest: bool,
) -> list[str]:
    """Return the cargo llvm-cov command arguments."""
    args = ["llvm-cov"]
    if use_nextest:
        args.append("nextest")
    args += ["--manifest-path", str(manifest_path), "--workspace", "--summary-only"]
    if not with_default:
        args.append("--no-default-features")
    if features:
        args += ["--features", features]
    args += [f"--{fmt}", "--output-path", str(out)]
    return args


def extract_percent(output: str) -> str:
    """Return the coverage percentage extracted from ``output``."""
    match = re.search(
        r"(?:coverage|Coverage).*?([0-9]+(?:\.[0-9]+)?)%",
        output,
        re.MULTILINE | re.IGNORECASE,
    )
    if not match:
        typer.echo("Could not parse coverage percent", err=True)
        raise typer.Exit(1)
    return match[1]


def _format_percent(covered: int, total: int) -> str:
    pct = Decimal(covered) * Decimal(100) / Decimal(total)
    return str(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_line_coverage_percent_from_cobertura(xml_file: Path) -> str:
    """Return overall line coverage % from a Cobertura XML file."""
    try:
        root = etree.parse(str(xml_file)).getroot()
    except (FileNotFoundError, PermissionError) as exc:
        typer.echo(f"Could not read {xml_file}: {exc}", err=True)
        raise typer.Exit(1) from exc
    except etree.XMLSyntaxError as exc:
        typer.echo(f"Invalid XML in {xml_file}: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        total = int(typ.cast("float", root.xpath("count(//class/lines/line)")))
        covered = int(
            typ.cast("float", root.xpath("count(//class/lines/line[@hits>0])"))
        )
    except etree.XPathError as exc:
        typer.echo(f"Malformed Cobertura data: {exc}", err=True)
        raise typer.Exit(1) from exc

    if total == 0:
        try:
            covered = int(
                typ.cast("float", root.xpath("number(/coverage/@lines-covered)"))
            )
            total = int(typ.cast("float", root.xpath("number(/coverage/@lines-valid)")))
        except etree.XPathError as exc:
            typer.echo(f"Cobertura summary missing: {exc}", err=True)
            raise typer.Exit(1) from exc

    if total == 0:
        return "0.00"

    return _format_percent(covered, total)


def _merge_lcov(base: Path, extra: Path) -> None:
    """Merge two lcov files ensuring they end with ``end_of_record``."""
    try:
        base_text = base.read_text()
        extra_text = extra.read_text()
    except (FileNotFoundError, PermissionError) as exc:
        typer.echo(f"Could not read coverage file: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not base_text.rstrip().endswith("end_of_record"):
        typer.echo(f"Malformed lcov data in {base}", err=True)
        raise typer.Exit(1)
    if not extra_text.rstrip().endswith("end_of_record"):
        typer.echo(f"Malformed lcov data in {extra}", err=True)
        raise typer.Exit(1)

    if not base_text.endswith("\n"):
        base_text += "\n"
    if not extra_text.endswith("\n"):
        extra_text += "\n"

    base.write_text(base_text + extra_text)


def run_cucumber_rs_coverage(
    out: Path,
    fmt: str,
    features: str,
    *,
    manifest_path: Path,
    cargo_env: typ.Mapping[str, str],
    with_default: bool,
    use_nextest: bool,
    cucumber_rs_features: str,
    cucumber_rs_args: str,
) -> None:
    """Run cucumber.rs coverage and merge results into ``out``."""
    cucumber_file = out.with_name(f"{out.stem}.cucumber{out.suffix}")
    c_args = get_cargo_coverage_cmd(
        fmt,
        cucumber_file,
        features,
        manifest_path=manifest_path,
        with_default=with_default,
        use_nextest=use_nextest,
    )
    c_args += [
        "--",
        "--test",
        "cucumber",
        "--",
        "cucumber",
        "--features",
        cucumber_rs_features,
    ]
    if cucumber_rs_args:
        c_args += shlex.split(cucumber_rs_args)

    _run_cargo(
        c_args,
        env_overrides=cargo_env,
        env_unsets=_CARGO_COVERAGE_ENV_UNSETS,
    )

    if fmt == "cobertura":
        from plumbum.cmd import uvx

        try:
            cmd = uvx["merge-cobertura", str(out), str(cucumber_file)]
            merged = run_cmd(cmd)
        except (ProcessExecutionError, subprocess.CalledProcessError) as exc:
            retcode = getattr(exc, "retcode", getattr(exc, "returncode", None))
            stderr = getattr(exc, "stderr", "")
            typer.echo(
                f"merge-cobertura failed with code {retcode}: {stderr}",
                err=True,
            )
            raise typer.Exit(code=retcode or 1) from exc
        out.write_text(merged)
    else:
        _merge_lcov(out, cucumber_file)

    cucumber_file.unlink()


@contextlib.contextmanager
def ensure_nextest_config() -> typ.Iterator[Path]:
    """Ensure a temporary nextest config exists when none is present."""
    config_path = _resolve_nextest_config_path()
    if config_path.is_file():
        yield config_path
        return

    created_dir = False
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        created_dir = True

    config_path.write_text(NEXTEST_DEFAULT_CONFIG, encoding="utf-8")
    try:
        yield config_path
    finally:
        with contextlib.suppress(FileNotFoundError):
            config_path.unlink()
        if created_dir:
            with contextlib.suppress(OSError):
                config_path.parent.rmdir()


def _resolve_nextest_config_path() -> Path:
    """Resolve config via NEXTEST_CONFIG, XDG/home, then CWD fallback.

    Order: NEXTEST_CONFIG, XDG_CONFIG_HOME/nextest/config.toml,
    ~/.config/nextest/config.toml, then CWD-relative NEXTEST_CONFIG_PATH
    (.config/nextest.toml).
    """
    env_path = os.getenv("NEXTEST_CONFIG")
    if env_path:
        return Path(env_path).expanduser()

    xdg_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_home:
        candidate = Path(xdg_home).expanduser() / "nextest" / "config.toml"
        if candidate.is_file():
            return candidate

    home_candidate = Path.home() / ".config" / "nextest" / "config.toml"
    if home_candidate.is_file():
        return home_candidate

    return NEXTEST_CONFIG_PATH


def main(
    output_path: Path = OUTPUT_PATH_OPT,
    features: str = FEATURES_OPT,
    *,
    with_default: bool = WITH_DEFAULT_OPT,
    use_nextest: bool = USE_NEXTEST_OPT,
    lang: str = LANG_OPT,
    fmt: str = FMT_OPT,
    manifest_path: Path = MANIFEST_PATH_OPT,
    github_output: Path = GITHUB_OUTPUT_OPT,
    cucumber_rs_features: str = CUCUMBER_RS_FEATURES_OPT,
    cucumber_rs_args: str = CUCUMBER_RS_ARGS_OPT,
    with_cucumber_rs: bool = WITH_CUCUMBER_RS_OPT,
    baseline_file: Path | None = BASELINE_OPT,
) -> None:
    """Run cargo llvm-cov and write the output file path to ``GITHUB_OUTPUT``."""
    out = output_path
    if lang == "mixed":
        out = output_path.with_name(f"{output_path.stem}.rust{output_path.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)

    args = get_cargo_coverage_cmd(
        fmt,
        out,
        features,
        manifest_path=manifest_path,
        with_default=with_default,
        use_nextest=use_nextest,
    )
    config_context = (
        ensure_nextest_config() if use_nextest else contextlib.nullcontext()
    )
    cargo_env = get_cargo_coverage_env(manifest_path)
    with config_context:
        stdout = _run_cargo(
            args,
            env_overrides=cargo_env,
            env_unsets=_CARGO_COVERAGE_ENV_UNSETS,
        )

        if with_cucumber_rs and cucumber_rs_features:
            run_cucumber_rs_coverage(
                out,
                fmt,
                features,
                manifest_path=manifest_path,
                cargo_env=cargo_env,
                with_default=with_default,
                use_nextest=use_nextest,
                cucumber_rs_features=cucumber_rs_features,
                cucumber_rs_args=cucumber_rs_args,
            )
    if fmt == "lcov":
        percent = get_line_coverage_percent_from_lcov(out)
    elif fmt == "cobertura":
        percent = get_line_coverage_percent_from_cobertura(out)
    else:
        percent = extract_percent(stdout)

    typer.echo(f"Current coverage: {percent}%")
    previous = read_previous_coverage(baseline_file)
    if previous is not None:
        typer.echo(f"Previous coverage: {previous}%")

    with github_output.open("a") as fh:
        fh.write(f"file={out}\n")
        fh.write(f"percent={percent}\n")


if __name__ == "__main__":
    typer.run(main)
