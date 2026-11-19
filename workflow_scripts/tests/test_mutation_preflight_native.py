"""Native support: the package a declaration names and the boundary it supports.

The declaration is matched to the lock entry it canonicalises to, and support
is established only from the fields that record it: a name, a revision, a
range. A range that was never judged, or support that was never declared, stays
unknown rather than reading as compatible.
"""

from __future__ import annotations

import typing as typ

from workflow_scripts.tests.mutation_preflight_support import (
    RULE_AVAILABLE,
    Checkout,
    Outcome,
    locked_checkout,
    outcome_for,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_REVISION_SOURCE = 'git = "https://example.invalid/tei.git?rev=abc123"'


def _revision_checkout(
    revision: str = "abc123",
    package: str = "tei-rapporteur",
    source: str = _REVISION_SOURCE,
) -> Checkout:
    """Return a checkout pinning *package* at *revision* under a supported range."""
    return locked_checkout(
        (
            "[native]\n"
            f'package = "{package}"\n'
            f'revision = "{revision}"\n'
            'supported_python = ">=3.12"\n'
        ),
        source,
    )


class TestNativeSupport:
    """A locked native dependency is named with its support boundary."""

    def _pinned_run(
        self,
        runner: typ.Callable[..., Outcome],
        root: Path,
        **declaration: str,
    ) -> Outcome:
        """Return what the run reported for a revision-pinned declaration."""
        return outcome_for(
            runner, root, _revision_checkout(**declaration), project_python="3.13"
        )

    def _unknown_outcome(
        self,
        runner: typ.Callable[..., Outcome],
        root: Path,
        manifest: str,
        **inputs: str,
    ) -> Outcome:
        """Return the run's outcome, having asserted support stayed unknown."""
        outcome = outcome_for(runner, root, locked_checkout(manifest), **inputs)
        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["native"]["status"] == "unknown"
        return outcome

    @RULE_AVAILABLE
    def test_an_unsupported_tuple_names_the_package_and_boundary(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The pinned package and the range it supports are both reported."""
        manifest = '[native]\npackage = "tei-rapporteur"\nsupported_python = ">=3.14"\n'

        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            locked_checkout(manifest, _REVISION_SOURCE),
            project_python="3.13",
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "tei-rapporteur's supported-python" in found
        assert ">=3.14" in found

    @RULE_AVAILABLE
    def test_a_declared_name_finds_the_lock_entry_it_canonicalises_to(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """An underscore declaration must find the lock's hyphenated entry."""
        outcome = self._pinned_run(
            run_preflight, tmp_path / "repo", package="tei_rapporteur"
        )

        assert outcome.exit_code == 0, outcome.stdout
        detail = outcome.detail("native")
        assert detail["locked_version"] == "0.3.1", (
            "the locked record must be read through the canonical name"
        )
        assert "not in uv.lock" not in outcome.findings("native"), (
            "a name the lock canonicalises to must not be reported as absent"
        )

    @RULE_AVAILABLE
    def test_a_supported_range_with_a_matching_pin_passes(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Supported support is reported as a pass, not merely as not-unknown."""
        outcome = self._pinned_run(run_preflight, tmp_path / "repo")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["native"]["status"] == "pass", (
            "a supported range and a matching pin must pass the native phase"
        )

    def test_unknown_support_stays_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """No declared range is reported as unknown, never as compatible."""
        outcome = self._unknown_outcome(
            run_preflight,
            tmp_path / "repo",
            '[native]\npackage = "tei-rapporteur"\n',
            project_python="3.13",
        )

        assert "unknown rather than established" in outcome.findings("native")

    def test_a_declared_range_is_unknown_without_a_project_interpreter(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A range that was never judged must not read as a range that holds."""
        outcome = self._unknown_outcome(
            run_preflight,
            tmp_path / "repo",
            '[native]\npackage = "tei-rapporteur"\nsupported_python = ">=3.12"\n',
            project_python="",
            helper_python="3.13",
        )

        assert "no project interpreter was supplied" in outcome.findings("native")

    def test_a_revision_that_the_lock_does_not_pin_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Dependency identity includes the revision, not just the name."""
        outcome = self._pinned_run(run_preflight, tmp_path / "repo", revision="def456")

        assert outcome.exit_code == 1
        assert "not the declared revision 'def456'" in outcome.findings("native")

    def test_a_revision_in_the_repository_url_is_not_a_pinned_revision(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The revision is read from the fields that record it, not searched for."""
        outcome = self._pinned_run(
            run_preflight,
            tmp_path / "repo",
            source='git = "https://example.invalid/abc123/tei.git"',
        )

        assert outcome.exit_code == 1
        found = outcome.findings("native")
        assert "records no pinned revision" in found
        assert "not the declared revision" not in found
