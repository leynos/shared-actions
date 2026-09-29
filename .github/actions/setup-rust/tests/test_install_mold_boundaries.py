"""Boundary tests for setup-rust's mold installer.

The process and network adapters are replaced by stand-ins, and the tool
cache and runner files are made unwritable, so each way the installer's
surroundings can fail is shown to produce one bounded failure category, one
``failed`` metric and no ``PATH`` entry, rather than an unhandled exception.
"""

from __future__ import annotations

import contextlib
import http.client
import subprocess
import typing as typ

import pytest
from mold_test_support import VERSION, install_mold, serve

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


def _runner(
    *, raises: BaseException | None = None, status: int = 0, stdout: str = ""
) -> cabc.Callable[[cabc.Sequence[str], float], subprocess.CompletedProcess[str]]:
    """Return a stand-in binary runner that fails or answers as told."""

    def run(
        argv: cabc.Sequence[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(list(argv), status, stdout, "")

    return run


@pytest.mark.parametrize(
    ("runner", "failure"),
    [
        pytest.param(_runner(raises=PermissionError("denied")), "unrunnable", id="os"),
        pytest.param(
            _runner(raises=subprocess.TimeoutExpired("mold", 60)),
            "timeout",
            id="timeout",
        ),
        pytest.param(_runner(status=1), "exit-status", id="status"),
        pytest.param(_runner(stdout="GNU ld 2.42"), "not-mold", id="banner"),
    ],
)
def test_a_probe_failure_keeps_its_kind(
    tmp_path: Path, runner: object, failure: str
) -> None:
    """A probe that cannot read a version says why, instead of returning nothing."""
    result = install_mold.probe(tmp_path / "mold", runner)

    assert result == install_mold.Probe(failure=failure)


def test_a_probe_reads_the_version_from_the_banner(tmp_path: Path) -> None:
    """A working binary's banner yields its version and no failure."""
    runner = _runner(stdout="mold 2.41.0 (compatible with GNU ld)\n")

    assert install_mold.probe(tmp_path / "mold", runner) == install_mold.Probe(
        version="2.41.0"
    )


def test_a_response_cut_short_is_a_download_failure(tmp_path: Path) -> None:
    """``IncompleteRead`` is not an ``OSError``, and must not escape the installer."""

    @contextlib.contextmanager
    def truncated(url: str, timeout: float) -> cabc.Iterator[typ.BinaryIO]:
        del url, timeout
        received = b"partial"
        raise http.client.IncompleteRead(received, 100)
        yield  # pragma: no cover - the generator shape the context manager needs.

    with pytest.raises(install_mold.DownloadError):
        install_mold.download("https://example.invalid/a", tmp_path / "a", truncated)


def test_an_unwritable_scratch_space_is_a_filesystem_failure(tmp_path: Path) -> None:
    """A temp directory that cannot be created fails with a category, not a trace."""
    served = serve(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where a directory must go", encoding="utf-8")

    with pytest.raises(install_mold.CacheFilesystemError):
        install_mold.install(
            served.release,
            tool_cache=tmp_path / "tool-cache",
            temp_dir=blocked / "temp",
            base_url=served.base_url,
        )


def _main(tmp_path: Path, served: object, *extra: str) -> cabc.Callable[[], int]:
    """Return a call of ``main`` for *served* with any *extra* arguments."""
    ticks = iter((100.0, 101.5))

    def call() -> int:
        return install_mold.main(
            [
                "--mold-version",
                VERSION,
                "--runner-arch",
                "X64",
                "--tool-cache",
                str(tmp_path / "tool-cache"),
                "--temp-dir",
                str(tmp_path / "temp"),
                "--release-base-url",
                served.base_url,
                *extra,
            ],
            clock=lambda: next(ticks),
        )

    return call


def test_main_reports_cache_state_and_elapsed_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fresh install reports a cache miss and a bounded elapsed-time bucket."""
    served = serve(tmp_path)
    monkeypatch.setattr(
        install_mold, "MOLD_DIGESTS", {(VERSION, "x86_64"): served.release.digest}
    )

    status = _main(tmp_path, served)()

    metrics = [
        line for line in capsys.readouterr().out.splitlines() if "metric" in line
    ]
    assert status == 0
    assert metrics == [
        "metric setup-rust.mold=installed",
        "metric setup-rust.mold.cache=miss",
        "metric setup-rust.mold.seconds=lt5s",
    ]


def test_unwritable_runner_files_fail_with_one_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ``GITHUB_PATH`` that cannot be written is reported, not raised."""
    served = serve(tmp_path)
    monkeypatch.setattr(
        install_mold, "MOLD_DIGESTS", {(VERSION, "x86_64"): served.release.digest}
    )
    unwritable = tmp_path / "github-path"
    unwritable.mkdir()

    status = _main(tmp_path, served, "--github-path", str(unwritable))()

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out.count("metric setup-rust.mold=failed") == 1
    assert "metric setup-rust.mold.failure=runner-files" in captured.out
    assert "::error title=setup-rust mold::" in captured.err


@pytest.mark.parametrize(
    ("seconds", "bucket"),
    [
        (0.0, "lt5s"),
        (4.99, "lt5s"),
        (5.0, "lt30s"),
        (119.9, "lt120s"),
        (120.0, "ge120s"),
    ],
)
def test_elapsed_time_falls_in_a_bounded_bucket(seconds: float, bucket: str) -> None:
    """The elapsed-time metric takes one of four values, whatever the duration."""
    assert install_mold.elapsed_bucket(seconds) == bucket
