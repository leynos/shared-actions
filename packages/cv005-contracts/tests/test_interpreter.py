"""Cases for the interpreter clause beyond the plain `UV_PYTHON` pin.

The four shapes claude-q's contract distinguishes, each with a narrow case
that must pass: a patch-zero spelling, an empty step value masking an outer
pin, a guarded repeat of `setup-python`, and a generate-coverage action from
another repository at the same path.
"""

from __future__ import annotations

import pytest
from contract_fixtures import PIN, SHARED, mutate, tree
from cv005_contracts.interpreter import interpreter_violations
from cv005_contracts.lanes import pull_request_lane_violations
from cv005_contracts.loading import Document, load_workflow
from cv005_contracts.parity import publisher_lane_violations, python_key
from cv005_contracts.publisher import find_publisher

PIN_LINE = "UV_PYTHON: '3.13'"
STEP_ENV = "        env:\n          UV_PYTHON: '3.13'\n"
JOB_HEAD = "    runs-on: ubuntu-latest\n"


def _documents(texts: dict[str, str]) -> dict[str, Document]:
    """Parse a tree of texts."""
    return {name: load_workflow(text) for name, text in texts.items()}


def _judge(texts: dict[str, str], interpreter: str = "3.13") -> list[str]:
    """Return the interpreter findings for the publisher and every lane."""
    documents = _documents(texts)
    _, publisher = find_publisher(documents)
    return interpreter_violations(
        publisher, interpreter, {"ci.yml": documents["ci.yml"]}
    )


def _setup(version: str, extra: str = "") -> str:
    """Return a `setup-python` step block, indented as the fixture's steps."""
    return (
        "      - uses: actions/setup-python@v6\n"
        f"        with:\n          python-version: '{version}'\n{extra}"
    )


def _with_setups(*setups: str) -> dict[str, str]:
    """Return the tree with the setups placed before the publisher's generator."""
    generator = "      - name: Generate coverage\n"
    return mutate("coverage-main.yml", generator, "".join(setups) + generator)


def test_the_compliant_tree_has_no_interpreter_finding() -> None:
    """Every case below changes one thing in a tree this accepts."""
    assert _judge(tree()) == []


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.13", "3.13"),
        ("3.13.0", "3.13"),
        (" 3.13.0 ", "3.13"),
        ("3.13.1", "3.13.1"),
        ("3.130", "3.130"),
        ("3.1.30", "3.1.30"),
    ],
)
def test_only_a_trailing_patch_zero_is_folded(version: str, expected: str) -> None:
    """`3.13.0` is `3.13`; a real patch, or a longer minor, is not."""
    assert python_key(version) == expected


@pytest.mark.parametrize(
    ("configured", "pinned"),
    [("3.13", "3.13.0"), ("3.13.0", "3.13"), ("3.13.0", "3.13.0"), ("3.13", " 3.13 ")],
)
def test_a_patch_zero_spelling_is_the_same_pin(configured: str, pinned: str) -> None:
    """Either spelling of the same version satisfies the pin, in both directions."""
    texts = mutate("coverage-main.yml", PIN_LINE, f"UV_PYTHON: '{pinned}'")
    assert _judge(texts, configured) == []


@pytest.mark.parametrize("pinned", ["3.13.1", "3.130", "3.14.0"])
def test_a_different_patch_or_minor_is_another_pin(pinned: str) -> None:
    """A real patch pin measures under an interpreter the lanes may not share."""
    texts = mutate("coverage-main.yml", PIN_LINE, f"UV_PYTHON: '{pinned}'")
    assert _judge(texts), pinned


def test_a_lane_spelling_the_pin_with_patch_zero_agrees_with_the_publisher() -> None:
    """Parity compares versions, not spellings, so `3.13.0` pairs with `3.13`."""
    texts = mutate("ci.yml", PIN_LINE, "UV_PYTHON: '3.13.0'")
    documents = _documents(texts)
    closure = {"ci.yml": documents["ci.yml"]}
    assert publisher_lane_violations(documents["coverage-main.yml"], closure) == []


def test_a_lane_pinning_another_patch_differs_from_the_publisher() -> None:
    """The fold stops at `.0`: `3.13.1` is not the publisher's selection."""
    texts = mutate("ci.yml", PIN_LINE, "UV_PYTHON: '3.13.1'")
    documents = _documents(texts)
    closure = {"ci.yml": documents["ci.yml"]}
    found = publisher_lane_violations(documents["coverage-main.yml"], closure)
    assert found == ["ci.yml: coverage selection differs from the publisher's"], found


def _hoisted(name: str, step_env: str) -> dict[str, str]:
    """Return the tree with a file's pin at workflow scope, `step_env` on its step."""
    texts = tree()
    text = texts[name].replace(STEP_ENV, step_env)
    texts[name] = text.replace("jobs:\n", "env:\n  UV_PYTHON: '3.13'\njobs:\n", 1)
    return texts


@pytest.mark.parametrize("empty", ["''", "' '", '""', "null"])
def test_an_empty_step_value_masks_the_outer_pin(empty: str) -> None:
    """The action reads an empty `UV_PYTHON` as unset, whatever an outer scope set."""
    masked = f"        env:\n          UV_PYTHON: {empty}\n"
    found = _judge(_hoisted("coverage-main.yml", masked))
    assert len(found) == 1, found
    assert "masks the one set in an outer scope" in found[0], found


def test_an_empty_step_value_in_a_lane_masks_the_outer_pin() -> None:
    """A lane is held to the same rule, and the finding names the lane."""
    masked = "        env:\n          UV_PYTHON: ''\n"
    found = _judge(_hoisted("ci.yml", masked))
    assert len(found) == 1, found
    assert found[0].startswith("ci.yml: "), found
    assert "masks the one set in an outer scope" in found[0], found


def test_an_empty_job_value_masks_the_workflow_pin() -> None:
    """The job's `env` shadows the workflow's as the step's does."""
    texts = _hoisted("coverage-main.yml", "")
    texts["coverage-main.yml"] = texts["coverage-main.yml"].replace(
        JOB_HEAD, JOB_HEAD + "    env:\n      UV_PYTHON: ''\n", 1
    )
    assert len(_judge(texts)) == 1


def test_an_inner_pin_over_a_different_outer_one_is_the_pin() -> None:
    """The innermost non-empty value decides, so the narrow case still passes."""
    texts = _hoisted("coverage-main.yml", STEP_ENV)
    texts["coverage-main.yml"] = texts["coverage-main.yml"].replace(
        "env:\n  UV_PYTHON: '3.13'\njobs:", "env:\n  UV_PYTHON: '3.12'\njobs:", 1
    )
    assert _judge(texts) == []


def test_an_empty_unrelated_key_masks_nothing() -> None:
    """Only `UV_PYTHON` is the pin; an empty sibling key is not a finding."""
    texts = mutate("coverage-main.yml", PIN_LINE, PIN_LINE + "\n          OTHER: ''")
    assert _judge(texts) == []


def test_a_setup_that_always_runs_and_agrees_is_accepted() -> None:
    """A reliable `setup-python` naming the pinned version declares no conflict."""
    assert _judge(_with_setups(_setup("3.13"))) == []


def test_a_setup_that_always_runs_and_disagrees_is_refused() -> None:
    """Two sources naming different versions is how lanes drift."""
    found = _judge(_with_setups(_setup("3.12")))
    assert len(found) == 1, found
    assert "a setup-python step" in found[0], found


GUARD = "        if: github.event_name == 'push'\n"


@pytest.mark.parametrize(
    "setups",
    [
        # A guarded repeat that changes the version may replace the reliable one.
        (_setup("3.13"), _setup("3.12", GUARD)),
        # A guarded repeat must not erase the earlier reliable setup: it may
        # be skipped, and then `PATH` still holds 3.12.
        (_setup("3.12"), _setup("3.13", GUARD)),
        (_setup("3.13"), _setup("3.12", "        continue-on-error: true\n")),
    ],
    ids=["guarded-changes", "guarded-cannot-erase", "continue-on-error-changes"],
)
def test_a_guarded_repeat_setup_that_names_another_version_is_refused(
    setups: tuple[str, ...],
) -> None:
    """`PATH` may hold either version, so the generator's Python is ambiguous."""
    found = _judge(_with_setups(*setups))
    assert found, found
    assert all("a setup-python step" in item for item in found), found


@pytest.mark.parametrize(
    "setups",
    [
        (_setup("3.13"), _setup("3.13", GUARD)),
        (_setup("3.12", GUARD),),
        (_setup("3.12", "        continue-on-error: true\n"),),
    ],
    ids=["guarded-same", "guarded-alone", "continue-on-error-alone"],
)
def test_a_guarded_setup_that_cannot_disagree_is_accepted(
    setups: tuple[str, ...],
) -> None:
    """A guarded repeat of the same version, or a lone guarded setup, is no conflict."""
    assert _judge(_with_setups(*setups)) == []


def test_a_setup_after_the_generator_is_not_read() -> None:
    """Only steps before the generator decide what is on `PATH` for it."""
    texts = mutate(
        "coverage-main.yml",
        "      - name: Check for the CodeScene token\n",
        _setup("3.12") + "      - name: Check for the CodeScene token\n",
    )
    assert _judge(texts) == []


def test_a_setup_in_another_job_is_not_read() -> None:
    """A job has its own `PATH`, so a sibling job's setup is not this one's."""
    other = (
        "  other:\n    runs-on: ubuntu-latest\n    steps:\n"
        + _setup("3.12")
        + "      - run: 'true'\n"
    )
    texts = mutate("coverage-main.yml", "jobs:\n", "jobs:\n" + other)
    assert _judge(texts) == []


def test_a_python_version_input_that_disagrees_is_refused() -> None:
    """The input outranks `UV_PYTHON`, so it decides which Python measures."""
    texts = mutate(
        "coverage-main.yml",
        "          with-ratchet: 'true'\n",
        "          with-ratchet: 'true'\n          python-version: '3.12'\n",
    )
    found = _judge(texts)
    assert len(found) == 1, found
    assert "the python-version input" in found[0], found


def test_a_python_version_input_that_agrees_is_accepted() -> None:
    """The same version through two sources is agreement, patch spelling aside."""
    texts = mutate(
        "coverage-main.yml",
        "          with-ratchet: 'true'\n",
        "          with-ratchet: 'true'\n          python-version: '3.13.0'\n",
    )
    assert _judge(texts) == []


LANE_GENERATOR = f"uses: {SHARED}/generate-coverage@{PIN}\n"


def _with_lookalike(name: str, source: str) -> str:
    """Return a file with a lookalike generator step added before its real one.

    The lookalike names another owner or repository at the same path, and
    pins another interpreter, so a reader taking it for the shared action
    would find a conflict.
    """
    text = tree()[name]
    assert text.count(LANE_GENERATOR) == 1, name
    lookalike = (
        f"      - uses: {source}/.github/actions/generate-coverage@{PIN}\n"
        "        env:\n          UV_PYTHON: '3.12'\n"
        "        with:\n          output-path: other.xml\n"
        "          with-ratchet: 'true'\n"
    )
    marker = "      - name: Test and Measure Coverage\n"
    marker = marker if marker in text else "      - name: Generate coverage\n"
    assert text.count(marker) == 1, name
    return text.replace(marker, lookalike + marker, 1)


FOREIGN_SOURCES = ["other/shared-actions", "leynos/other-actions"]


@pytest.mark.parametrize("name", ["ci.yml", "coverage-main.yml"])
@pytest.mark.parametrize("source", FOREIGN_SOURCES)
def test_a_foreign_generator_is_not_judged_as_the_shared_one(
    name: str, source: str
) -> None:
    """A lookalike pinning another Python beside the real generator is no conflict."""
    texts = tree() | {name: _with_lookalike(name, source)}
    assert _judge(texts) == []


@pytest.mark.parametrize("source", FOREIGN_SOURCES)
def test_a_lane_with_only_a_foreign_generator_generates_nothing(source: str) -> None:
    """Replacing the real generator with a lookalike leaves the lane with none."""
    text = tree()["ci.yml"].replace(
        f"{SHARED}/generate-coverage", f"{source}/.github/actions/generate-coverage"
    )
    assert text != tree()["ci.yml"]
    found = pull_request_lane_violations({"ci.yml": load_workflow(text)})
    assert found == ["no pull-request workflow generates coverage"], found


def test_the_shared_action_is_matched_without_regard_to_case() -> None:
    """GitHub resolves owner and repository case-insensitively, so it is the same.

    The generator carries a disagreeing input, a finding only a reader that
    recognises the step can make; an unrecognised step would read as no
    generator at all and report a missing pin instead.
    """
    real = f"{SHARED}/generate-coverage"
    text = tree()["coverage-main.yml"]
    cased = text.replace(
        real, real.replace("leynos/shared-actions", "Leynos/Shared-Actions")
    ).replace(
        "          with-ratchet: 'true'\n",
        "          with-ratchet: 'true'\n          python-version: '3.12'\n",
    )
    found = _judge(tree() | {"coverage-main.yml": cased})
    assert len(found) == 1, found
    assert "the python-version input" in found[0], found
