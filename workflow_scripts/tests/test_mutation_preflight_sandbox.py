"""Declared sandbox paths, checked against what mutmut actually copies.

A path that exists in the checkout is not necessarily a path the mutation run
receives. Each case here pairs a declaration with the copy entry that
materializes it, or shows the finding that names the entry still needed.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ
from pathlib import Path

import pytest

from workflow_scripts.tests.mutation_preflight_support import (
    POSIX_SYMLINKS_ONLY,
    Checkout,
    Outcome,
    outcome_for,
    repository,
)


@dc.dataclass(frozen=True)
class _Delivered:
    """One declaration, the copy entries that deliver it, and why they do."""

    declared: str
    also_copy: tuple[str, ...]
    extra: dict[str, str]
    reason: str


def _materializable(declared: str) -> bool:
    """Return whether *declared* names a path inside the fixture checkout."""
    path = Path(declared)
    return not path.is_absolute() and ".." not in path.parts


class TestSandbox:
    """Declared paths are checked against what mutmut actually copies."""

    def test_a_missing_readme_is_named(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The README is outside mutmut's copy set and absent, so it is named."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(manifest='sandbox_paths = ["README.md"]\n'),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert "sandbox path 'README.md' is not in the checkout" in outcome.findings(
            "sandbox"
        ), "the finding must name the missing path"

    @pytest.mark.parametrize(
        "case",
        [
            _Delivered(
                "README.md",
                ("conftest.py", "README.md"),
                {"README.md": "# example\n"},
                "an also_copy file entry materializes the path it names",
            ),
            _Delivered(
                ".github/workflows/ci.yml",
                ("conftest.py", ".github"),
                {".github/workflows/ci.yml": "name: ci\n"},
                "an also_copy directory covers every file beneath it",
            ),
            _Delivered(
                "workflow_scripts/example.py",
                (),
                {},
                "a file under a source_paths entry is already copied",
            ),
            _Delivered(
                "test_example.py",
                (),
                {"test_example.py": ""},
                "the release's root test glob is part of the copy set",
            ),
        ],
        ids=["also-copy-file", "also-copy-directory", "source-paths", "test-glob"],
    )
    def test_a_path_the_copy_set_delivers_passes(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        case: _Delivered,
    ) -> None:
        """A declared path the sandbox receives is reported as copied."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                also_copy=case.also_copy,
                manifest=f'sandbox_paths = ["{case.declared}"]\n',
                extra=case.extra,
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "pass", (
            case.reason,
            outcome.stdout,
        )
        assert outcome.detail("sandbox")["not_copied"] == [], (
            f"{case.reason}, so nothing is left uncopied"
        )

    def test_a_hidden_workflow_tree_is_named_not_copied(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A suite that reads .github/workflows needs that tree in mutants/."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest='sandbox_paths = [".github/workflows/ci.yml"]\n',
                extra={".github/workflows/ci.yml": "jobs: {}\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert ".github/workflows/ci.yml" in outcome.findings("sandbox"), (
            "the finding must name the hidden workflow file"
        )
        assert "also_copy" in outcome.findings("sandbox"), (
            "the finding must name also_copy as the remedy"
        )

    def test_a_generator_sibling_is_named_not_copied(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A template outside source_paths is not copied just by existing."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest='sandbox_paths = ["templates/report.j2"]\n',
                extra={"templates/report.j2": "{}\n"},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert "templates/report.j2" in outcome.findings("sandbox"), (
            "the finding must name the generator sibling"
        )
        assert outcome.detail("sandbox")["not_copied"] == ["templates/report.j2"], (
            "the uncopied list must hold the template"
        )

    @pytest.mark.parametrize(
        ("declared", "contents"),
        [
            ("rust-toolchain.toml", '[toolchain]\nchannel = "stable"\n'),
            ("fixtures/nested/data.json", "{}\n"),
        ],
        ids=["toolchain-file", "nested-fixture"],
    )
    def test_a_file_outside_the_copy_set_is_named(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        declared: str,
        contents: str,
    ) -> None:
        """A toolchain file and a nested fixture are requirements like any other.

        Neither sits under ``source_paths``, so existing is not enough: the
        declaration has to be materialized by ``also_copy``.
        """
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                manifest=f'sandbox_paths = ["{declared}"]\n',
                extra={declared: contents},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert declared in outcome.findings("sandbox"), (
            "the finding must name the declared path"
        )
        assert outcome.detail("sandbox")["not_copied"] == [declared], (
            "the uncopied list must hold the declared path"
        )

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("/etc/passwd", "is absolute"),
            ("../outside", "escapes the checkout"),
            ("target/debug/example", "build output"),
            (".venv/lib/x.py", "tool cache"),
        ],
    )
    def test_an_unusable_path_is_refused(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        declared: str,
        expected: str,
    ) -> None:
        """Absolute, escaping, build-output and cache paths are all refused."""
        outcome = outcome_for(
            run_preflight,
            tmp_path / "repo",
            Checkout(
                also_copy=("conftest.py", declared),
                manifest=f'sandbox_paths = ["{declared}"]\n',
                extra={declared: "x\n"} if _materializable(declared) else {},
            ),
            project_python="3.13",
        )

        assert outcome.exit_code == 1, outcome.stdout
        assert expected in outcome.findings("sandbox"), (
            "the finding must name why the path is refused"
        )

    def test_a_repository_without_mutmut_configuration_reports_unknown(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Without a copy set the comparison cannot be made, and says so."""
        root = repository(
            tmp_path / "repo", Checkout(manifest='sandbox_paths = ["conftest.py"]\n')
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "unknown", (
            "the sandbox phase must report unknown",
            outcome.stdout,
        )
        assert "cannot be established" in outcome.findings("sandbox"), (
            "the finding must say the copy set cannot be established"
        )

    def test_a_setup_cfg_without_a_mutmut_section_is_unknown_not_a_crash(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """An absent section is unknown configuration, not a lookup failure."""
        root = repository(
            tmp_path / "repo", Checkout(manifest='sandbox_paths = ["conftest.py"]\n')
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )
        (root / "setup.cfg").write_text(
            "[metadata]\nname = example\n", encoding="utf-8"
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "unknown", (
            "the sandbox phase must report unknown",
            outcome.stdout,
        )
        assert "neither pyproject.toml's [tool.mutmut]" in outcome.findings(
            "sandbox"
        ), "the finding must name the configuration it looked for"

    @pytest.mark.parametrize(
        "contents",
        ["[mutmut]\nalso_copy = data/100%.json\n", "this is not an ini file\n"],
        ids=["interpolation", "syntax"],
    )
    def test_an_unreadable_setup_cfg_is_not_a_missing_section(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        contents: str,
    ) -> None:
        """A setup.cfg that will not parse is unreadable, not without a section."""
        root = repository(
            tmp_path / "repo", Checkout(manifest='sandbox_paths = ["conftest.py"]\n')
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )
        (root / "setup.cfg").write_text(contents, encoding="utf-8")

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "unknown", (
            "the sandbox phase must report unknown",
            outcome.stdout,
        )
        assert "setup.cfg could not be read" in outcome.findings("sandbox"), (
            "the finding must say setup.cfg could not be read"
        )
        assert "is present, so what mutmut copies" not in outcome.findings("sandbox"), (
            "an unreadable file must not be reported as a missing section"
        )

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("../outside", "escapes the checkout"),
            ("README.md", "is not in the checkout"),
        ],
    )
    def test_a_path_is_decided_though_the_copy_set_is_unknown(
        self,
        tmp_path: Path,
        run_preflight: typ.Callable[..., Outcome],
        declared: str,
        expected: str,
    ) -> None:
        """Neither question depends on knowing what mutmut would copy."""
        root = repository(
            tmp_path / "repo", Checkout(manifest=f'sandbox_paths = ["{declared}"]\n')
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert expected in outcome.findings("sandbox"), (
            "the finding must name why the path is refused"
        )

    def test_a_prepared_checkout_is_read_as_the_test_wrote_it(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """Passing no checkout must not overwrite the one the test prepared."""
        root = repository(
            tmp_path / "repo", Checkout(manifest='sandbox_paths = ["README.md"]\n')
        )
        with (root / "pyproject.toml").open("a", encoding="utf-8") as handle:
            handle.write('\n[tool.mutation-preflight]\nsandbox_paths = ["README.md"]\n')

        outcome = outcome_for(run_preflight, root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert "keep one" in outcome.findings("manifest"), (
            "the finding must name the duplicate declarations"
        )

    def test_the_deprecated_paths_to_mutate_still_names_the_source_tree(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """The deprecated key still names the tree, so the copy set is knowable."""
        root = repository(
            tmp_path / "repo",
            Checkout(manifest='sandbox_paths = ["workflow_scripts/example.py"]\n'),
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "example"\n\n'
            "[tool.mutmut]\n"
            'paths_to_mutate = ["workflow_scripts/"]\n',
            encoding="utf-8",
        )

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 0, outcome.stdout
        assert outcome.phases["sandbox"]["status"] == "pass", (
            "the sandbox phase must report pass",
            outcome.stdout,
        )

    @POSIX_SYMLINKS_ONLY
    def test_a_sandbox_path_resolving_outside_the_checkout_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A link would carry something the declaration did not name."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        root = repository(
            tmp_path / "repo",
            Checkout(
                also_copy=("conftest.py", "fixtures"),
                manifest='sandbox_paths = ["fixtures"]\n',
            ),
        )
        (root / "fixtures").symlink_to(outside, target_is_directory=True)

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert "outside the checkout" in outcome.findings("sandbox"), (
            "the finding must say the link resolves outside the checkout"
        )

    @POSIX_SYMLINKS_ONLY
    def test_a_sandbox_path_resolving_into_a_cache_is_refused(
        self, tmp_path: Path, run_preflight: typ.Callable[..., Outcome]
    ) -> None:
        """A link into .venv would copy the cache the rule exists to exclude."""
        root = repository(
            tmp_path / "repo",
            Checkout(
                also_copy=("conftest.py", "fixtures"),
                manifest='sandbox_paths = ["fixtures"]\n',
            ),
        )
        (root / ".venv").mkdir()
        (root / "fixtures").symlink_to(root / ".venv", target_is_directory=True)

        outcome = run_preflight(root, project_python="3.13")

        assert outcome.exit_code == 1, outcome.stdout
        assert "tool cache" in outcome.findings("sandbox"), (
            "the finding must name the cache the link reaches"
        )
