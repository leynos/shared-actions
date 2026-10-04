"""What the start step publishes to the next step, and when.

`export-rustc-wrapper: false` leaves the wrapper to the caller, who needs the
sccache binary and a running server; the `sccache-path` output is the binary,
published only once the server has started. The start step's wider contract
lives in `test_sccache_server_start.py`; these cases hold the new input's side
of it.
"""

from __future__ import annotations

import typing as typ

import test_sccache_server_start as start
from test_sccache_server_start import Scenario, _reported, _run_server

if typ.TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from pathlib import Path

#: The stub-sccache fixture of the start step's own tests, shared by name.
fake_sccache = start.fake_sccache


class TestPublishedPath:
    """The `sccache-path` output and the not-exported start."""

    def test_the_path_is_published_after_a_successful_start(
        self, fake_sccache: Path
    ) -> None:
        """A caller scoping the wrapper reads the binary from this step."""
        workdir = fake_sccache.parent
        _run_server(Scenario(workdir=workdir, sccache_path=str(fake_sccache)))

        outputs = (workdir / "github_output").read_text(encoding="utf-8").splitlines()
        assert f"path={fake_sccache}" in outputs

    def test_no_path_is_published_after_a_fallback(self, fake_sccache: Path) -> None:
        """Wrapping with it would restart the dead server and hang or fail."""
        workdir = fake_sccache.parent
        _run_server(
            Scenario(workdir=workdir, sccache_path=str(fake_sccache), start_exit=1)
        )

        outputs = (workdir / "github_output").read_text(encoding="utf-8").splitlines()
        assert not any(line.startswith("path=") for line in outputs)
        assert "status=fallback" in outputs

    def test_no_path_is_published_when_the_caller_owns_the_wrapper(
        self, fake_sccache: Path
    ) -> None:
        """A caller-owned wrapper means the action started nothing."""
        workdir = fake_sccache.parent
        _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                wrapper_state="caller-set",
            )
        )

        assert not (workdir / "github_output").exists() or "path=" not in (
            workdir / "github_output"
        ).read_text(encoding="utf-8")

    def test_starts_a_server_when_the_wrapper_is_left_to_the_caller(
        self, fake_sccache: Path
    ) -> None:
        """`export-rustc-wrapper: false` still gets its server.

        A caller that scopes the wrapper to its own commands finds the server
        running, so the start must not treat `not-exported` as a deferral.
        """
        workdir = fake_sccache.parent
        completed = _run_server(
            Scenario(
                workdir=workdir,
                sccache_path=str(fake_sccache),
                wrapper_state="not-exported",
            )
        )

        assert completed.returncode == 0, completed.stderr
        assert "--start-server" in (
            (workdir / "args.log").read_text(encoding="utf-8").split()
        )
        assert _reported(completed) == "started"
        outputs = (workdir / "github_output").read_text(encoding="utf-8").splitlines()
        assert f"path={fake_sccache}" in outputs, (
            "a caller that declined the wrapper reads the binary from this output"
        )
