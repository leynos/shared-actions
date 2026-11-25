"""The preflight document's examples are loaded from the shipped text.

The documentation style guide makes a published example a contract: each fence
carries a stable identifier, and a test exercises the text the document
actually ships rather than a copied fixture. These tests load
``docs/mutation-preflight.md`` through the shared loader and hold its
declarations, its opt-in step and its manual invocation to the helper they
describe.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml

from workflow_scripts.mutationpreflight.cli import Options
from workflow_scripts.mutationpreflight.declaration import load_declaration
from workflow_scripts.mutationpreflight.repository import DeclarationDocuments
from workflow_scripts.tests.markdown_examples import (
    Example,
    ExampleError,
    load_examples,
)

if typ.TYPE_CHECKING:
    from workflow_scripts.mutationpreflight.models import Declaration

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = _REPOSITORY_ROOT / "docs" / "mutation-preflight.md"

#: The declaration examples, one per documented table.
_DECLARATIONS = ("sandbox-paths", "require-commands", "native", "checks")

_IDENTIFIERS = ("opt-in", *_DECLARATIONS, "manual-run")

_INPUT_VARIABLE = re.compile(r"\bINPUT_[A-Z_]+\b")
_SCRIPT_REFERENCE = re.compile(r"workflow_scripts/[\w./-]+\.py")


def _written(tmp_path: Path, text: str) -> Path:
    """Write *text* as a Markdown document and return its path."""
    path = tmp_path / "document.md"
    path.write_text(text, encoding="utf-8")
    return path


def _example(identifier: str) -> Example:
    """Return the example the published document names *identifier*."""
    return load_examples(DOCUMENT)[identifier]


def _declaration(identifier: str, root: Path) -> Declaration:
    """Return the declaration the published *identifier* example produces."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f"{_example(identifier).body}\n", encoding="utf-8"
    )
    loaded = load_declaration(DeclarationDocuments(), root, "mutmut")
    if loaded.declaration is None:
        message = f"the {identifier!r} example was refused: {loaded.findings}"
        raise ExampleError(message)
    return loaded.declaration


def _helper_variables() -> set[str]:
    """Return the INPUT_* variable names the helper's options read."""
    declared: set[str] = set()
    for hint in typ.get_type_hints(Options, include_extras=True).values():
        for metadata in typ.get_args(hint)[1:]:
            declared.update(getattr(metadata, "env_var", ()))
    return declared


class TestLoader:
    """A fence without a stable identifier is not an example."""

    def test_a_marked_fence_is_loaded(self, tmp_path: Path) -> None:
        """The marker names the fence, and the body is returned without it."""
        path = _written(
            tmp_path, "<!-- tested-example: first -->\n```toml\na = 1\n```\n"
        )

        example = load_examples(path)["first"]

        assert example.language == "toml"
        assert example.body == "a = 1"

    def test_an_unmarked_fence_is_refused(self, tmp_path: Path) -> None:
        """An unmarked example would never be exercised by any test."""
        path = _written(tmp_path, "```toml\na = 1\n```\n")

        with pytest.raises(ExampleError, match="no tested-example marker"):
            load_examples(path)

    def test_a_fence_inside_a_blockquote_is_refused(self, tmp_path: Path) -> None:
        """A quoted fence would otherwise pass for prose and go unread."""
        path = _written(tmp_path, "> ~~~toml\n> a = 1\n> ~~~\n")

        with pytest.raises(ExampleError, match="inside a blockquote"):
            load_examples(path)

    def test_a_marker_before_no_fence_is_refused(self, tmp_path: Path) -> None:
        """A marker whose example was removed must not silently disappear."""
        path = _written(tmp_path, "<!-- tested-example: stray -->\nprose\n")

        with pytest.raises(ExampleError, match="precedes no fence"):
            load_examples(path)

    def test_a_duplicate_identifier_is_refused(self, tmp_path: Path) -> None:
        """Two examples under one name would leave one of them untested."""
        marked = "<!-- tested-example: same -->\n```toml\na = 1\n```\n"
        path = _written(tmp_path, marked + "\n" + marked)

        with pytest.raises(ExampleError, match="duplicate tested-example"):
            load_examples(path)

    @pytest.mark.parametrize(
        "gap",
        ["\nProse here.\n\n", "\n"],
        ids=["prose", "blank-line"],
    )
    def test_a_gap_between_marker_and_fence_is_refused(
        self, tmp_path: Path, gap: str
    ) -> None:
        """The marker must be the line above its fence; any gap separates them."""
        path = _written(
            tmp_path, f"<!-- tested-example: first -->\n{gap}```toml\na = 1\n```\n"
        )

        with pytest.raises(ExampleError, match="prose intervenes"):
            load_examples(path)

    def test_an_unterminated_fence_is_refused(self, tmp_path: Path) -> None:
        """An unclosed fence would swallow the rest of the document."""
        path = _written(tmp_path, "<!-- tested-example: first -->\n```toml\na = 1\n")

        with pytest.raises(ExampleError, match="never closed"):
            load_examples(path)


class TestPublishedExamples:
    """Every example in the shipped document is exercised as it is published."""

    def test_every_fence_carries_a_stable_identifier(self) -> None:
        """The document's examples are exactly the ones the tests know."""
        assert tuple(load_examples(DOCUMENT)) == _IDENTIFIERS

    def test_each_example_declares_the_key_its_section_documents(
        self, tmp_path: Path
    ) -> None:
        """An example that no longer demonstrates its section is drift."""
        declarations = {
            identifier: _declaration(identifier, tmp_path / identifier)
            for identifier in _DECLARATIONS
        }

        assert declarations["sandbox-paths"].sandbox_paths
        assert len(declarations["require-commands"].commands) == 2
        assert declarations["native"].native is not None
        assert declarations["checks"].checks

    def test_the_checks_example_writes_its_path_relative_to_its_cwd(
        self, tmp_path: Path
    ) -> None:
        """A checkout-relative path would be re-rooted under cwd when it runs."""
        check = _declaration("checks", tmp_path).checks[0]

        repeated = [
            argument
            for argument in check.command
            if argument.startswith(f"{check.cwd}/")
        ]
        assert repeated == [], (
            "the check runs from cwd, so a path in its command must not repeat "
            f"that prefix: {repeated}"
        )

    def test_the_opt_in_example_names_a_workflow_that_takes_preflight(self) -> None:
        """The documented opt-in must match the input the workflow declares."""
        parsed_jobs = typ.cast(
            "dict[str, object]", yaml.safe_load(_example("opt-in").body)
        )
        jobs = typ.cast("dict[str, object]", parsed_jobs["jobs"])
        used = typ.cast("dict[str, object]", jobs["mutation"])
        target = str(used["uses"]).split("@", 1)[0]
        workflow = _REPOSITORY_ROOT / target.removeprefix("leynos/shared-actions/")
        assert workflow.is_file(), f"the example names {target}, which is absent"

        document = typ.cast(
            "dict[object, object]", yaml.safe_load(workflow.read_text(encoding="utf-8"))
        )
        # PyYAML reads the unquoted ``on`` key as the boolean it resembles.
        call = typ.cast("dict[str, object]", document.get("on") or document.get(True))
        callable_ = typ.cast("dict[str, object]", call["workflow_call"])
        inputs = typ.cast("dict[str, object]", callable_["inputs"])
        preflight = typ.cast("dict[str, object]", inputs["preflight"])

        assert preflight["default"] is False
        assert typ.cast("dict[str, object]", used["with"])["preflight"] is True

    def test_the_manual_run_example_only_sets_variables_the_helper_reads(self) -> None:
        """An INPUT_* name the helper does not read is silently ignored."""
        body = _example("manual-run").body
        named = set(_INPUT_VARIABLE.findall(body))

        assert named
        assert named <= _helper_variables()
        for script in _SCRIPT_REFERENCE.findall(body):
            assert (_REPOSITORY_ROOT / script).is_file(), f"{script} is absent"

        # The example's own body is part of the document, so leaving it in
        # would make this assertion unable to fail.
        prose = DOCUMENT.read_text(encoding="utf-8").replace(body, "", 1)
        documented = set(_INPUT_VARIABLE.findall(prose))
        assert named <= documented, "the section must list every variable it uses"


MIGRATION_DOCUMENT = _REPOSITORY_ROOT / "docs" / "v1-3-0-migration-guide.md"

#: The migration guide's examples, one per documented step.
_MIGRATION_IDENTIFIERS = (
    "mutmut-caller-before",
    "mutmut-caller-after",
    "manual-run",
)


def _migration_example(identifier: str) -> Example:
    """Return the example the migration guide publishes under *identifier*."""
    return load_examples(MIGRATION_DOCUMENT)[identifier]


def _mutation_job(identifier: str) -> dict[str, object]:
    """Return the ``mutation`` job the *identifier* caller example defines."""
    parsed = typ.cast(
        "dict[str, object]", yaml.safe_load(_migration_example(identifier).body)
    )
    jobs = typ.cast("dict[str, object]", parsed["jobs"])
    return typ.cast("dict[str, object]", jobs["mutation"])


def _workflow_inputs(target: str) -> dict[str, object]:
    """Return the ``workflow_call`` inputs of the workflow *target* names."""
    workflow = _REPOSITORY_ROOT / target.removeprefix("leynos/shared-actions/")
    assert workflow.is_file(), f"the example names {target}, which is absent"
    document = typ.cast(
        "dict[object, object]", yaml.safe_load(workflow.read_text(encoding="utf-8"))
    )
    # PyYAML reads the unquoted ``on`` key as the boolean it resembles.
    call = typ.cast("dict[str, object]", document.get("on") or document.get(True))
    return typ.cast(
        "dict[str, object]",
        typ.cast("dict[str, object]", call["workflow_call"])["inputs"],
    )


class TestMigrationGuideExamples:
    """The v1.3.0 guide's examples are exercised as the document ships them."""

    def test_every_fence_carries_a_stable_identifier(self) -> None:
        """The guide's examples are exactly the ones the tests know."""
        assert tuple(load_examples(MIGRATION_DOCUMENT)) == _MIGRATION_IDENTIFIERS

    def test_the_caller_examples_name_the_same_workflow(self) -> None:
        """Before and after pin one workflow; only the after example opts in."""
        before = _mutation_job("mutmut-caller-before")
        after = _mutation_job("mutmut-caller-after")

        target = str(after["uses"]).split("@", 1)[0]
        assert str(before["uses"]).split("@", 1)[0] == target, (
            "both examples must migrate the same caller"
        )
        assert "with" not in before, (
            "the before example shows the caller unchanged, so it sets no input"
        )
        inputs = _workflow_inputs(target)
        assert typ.cast("dict[str, object]", inputs["preflight"])["default"] is False
        assert typ.cast("dict[str, object]", after["with"])["preflight"] is True

    def test_the_manual_run_example_only_sets_variables_the_helper_reads(self) -> None:
        """An INPUT_* name the helper does not read is silently ignored."""
        body = _migration_example("manual-run").body
        named = set(_INPUT_VARIABLE.findall(body))

        assert named
        assert named <= _helper_variables()
        for script in _SCRIPT_REFERENCE.findall(body):
            assert (_REPOSITORY_ROOT / script).is_file(), f"{script} is absent"

        # The example's own body is part of the document, so leaving it in
        # would make this assertion unable to fail.
        prose = MIGRATION_DOCUMENT.read_text(encoding="utf-8").replace(body, "", 1)
        documented = set(_INPUT_VARIABLE.findall(prose))
        assert named <= documented, "the section must list every variable it uses"
