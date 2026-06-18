# 3.3.3 Replace Shared TUI References with Host-Neutral Review View References

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes &
Retrospective` must be kept up to date as work proceeds.

Status: DRAFT


## Purpose / big picture

This milestone introduces a host-neutral abstraction for review view references,
decoupling TUI-specific implementation details from domain models that represent
code review summaries and comments. Currently, review linking is tightly coupled
to the TUI presentation layer, making it impossible for other clients (CLI tools,
libraries, web services) to reference review comments without importing
TUI-specific types and protocols.

After this change, code review summary data transfer objects (DTOs) will expose
host-neutral `ReviewViewRef` values that reference review comments without
encoding TUI-specific URLs or view implementations. The TUI adapter will render
these references as `frankie://review-comment/<id>?view=detail` links, and CLI
and library tests will prove that serialization does not depend on TUI-only
types, enabling other delivery mechanisms (CLI commands, web APIs, library
consumers) to resolve references independently using host-specific adapters.

Users will be able to export, serialize, and consume review data in CLI tools
and library code without runtime or static dependencies on the TUI layer.


## Glossary

**Host-neutral reference**: A serializable, domain-owned value object that
identifies a review comment without encoding any host-specific presentation
details (e.g., TUI URLs, handler functions, view technologies). It can be
safely serialized to JSON and deserialized in contexts with no TUI dependencies.

**Host**: The runtime environment consuming review data. Includes TUI (terminal
user interface), CLI commands, web service APIs, and library consumers.

**ReviewViewRef**: The domain type (dataclass) that represents a host-neutral
reference to a review comment. Contains only logical identifiers (comment ID,
view type), never host-specific details.

**Summary DTO**: Data transfer object representing aggregated review comment
data, including the comment text, metadata, and a `ReviewViewRef` for linking.
Does not import TUI-specific modules.

**View resolver**: An adapter interface that translates a `ReviewViewRef` into
a host-specific URL or reference string (e.g., `frankie://` for TUI, `cli://`
for CLI, raw ID for library). One resolver implementation per host.

**frankie://**: The TUI-specific URL scheme used by the Terminal User Interface
to render review comment links. Example: `frankie://review-comment/abc123?view=detail`.


## Constraints

Hard invariants that must hold throughout implementation.

- New public APIs introduced for review data export must remain stable after
  release. Internal APIs (non-exported functions) may be refactored if necessary.
- The TUI must continue to work without modification after this change; the
  adapter layer must translate host-neutral references to `frankie://` URLs.
- No new external dependencies without explicit approval. Review models and
  resolvers must use only Python standard library (dataclasses, abc, typing).
- All code must pass type checking with the project's type checker configuration.
- Tests must achieve ≥85% line coverage for new modules; domain model files
  (`models.py`, `resolution.py`) must have 100% coverage to ensure no accidental
  TUI imports.
- The domain model for `ReviewViewRef` and view resolution must not import from
  TUI-specific modules. This is a hard architectural boundary enforced by static
  and runtime checks.
- ReviewViewRef serialization must use JSON format and be JSON-safe
  (strings, numbers, booleans, dicts, lists only). Deserialization must handle
  schema evolution gracefully (ignore unknown fields, validate required fields).


## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- Scope: if implementation requires changes to more than 8 files or more than 800 net lines of code, stop and escalate.
- Interface changes: if any public function signature in an existing module must change, stop and escalate unless the change is a non-breaking addition.
- Dependencies: if any new external package is required, stop and escalate.
- Test coverage: if any new module falls below 85% coverage, do not commit without escalating.
- Iterations: if a single test fails more than 3 consecutive attempts to fix, stop and escalate.
- Design ambiguity: if a choice between two equally valid approaches materially affects the outcome, stop and present options for approval.


## Risks

Known uncertainties that might affect the plan.

- Risk: Unclear specification of what "host-neutral" means in the context of review link resolution.
  Severity: medium
  Likelihood: medium
  Mitigation: Define host-neutral as "no imports of TUI-specific modules in domain models; view references must be serializable and discoverable by non-TUI code". Prototype a small resolver to confirm this is achievable.

- Risk: Existing code in review-related modules may already have tight coupling to TUI types that is difficult to untangle without broader refactoring.
  Severity: medium
  Likelihood: medium
  Mitigation: Start with a codebase exploration to measure coupling; if coupling is severe, propose a two-phase approach: (1) create new domain models and tests, (2) refactor adapters gradually. Escalate if more than 3 files would need changes to untangle existing coupling.

- Risk: Prerequisite tasks (2.1.3 and 3.3.2) may introduce design conflicts or incomplete contracts that affect this task.
  Severity: high
  Likelihood: low
  Mitigation: This plan assumes no review data structures or contracts exist yet in the expected shape. Phase 1 includes a pre-flight check to confirm prerequisites have not been implemented. If 2.1.3 or 3.3.2 are implemented concurrently, suspend this task and merge work at a common base.

- Risk: CLI and library tests may reveal that the view reference abstraction is too loose or too strict to be useful for non-TUI consumers.
  Severity: low
  Likelihood: medium
  Mitigation: Design tests that exercise view reference resolution from both TUI and non-TUI contexts (e.g., mock CLI and library contexts). Use test-driven design to refine the abstraction as needed.

- Risk: Python type checker (type annotation requirements) may flag dataclass usage or missing Protocol definitions.
  Severity: low
  Likelihood: medium
  Mitigation: Use frozen dataclasses with slots for domain models. Test with the project's type checker after Phase 2. If type checking fails, define Protocol interfaces for critical types.

- Risk: Domain models living in `workflow_scripts/` alongside procedural scripts may cause namespace confusion.
  Severity: low
  Likelihood: low
  Mitigation: Phase 1 evaluation may propose moving domain models to `src/review_views/` or `workflow_scripts/lib/review_views/`. Document the final location choice in Decision Log.

- Risk: Coverage measurement tool (slipcover vs coverage.py) may produce different results than the specified pytest --cov flag.
  Severity: low
  Likelihood: low
  Mitigation: Use `make test` (the canonical test command) which manages slipcover setup. Validate coverage with the project's standard tooling, not manual pytest --cov.


## Progress

Use a list with checkboxes to summarise granular steps with timestamps.

- [ ] **Phase 1: Design and Specification**
  - [ ] (TBD) Pre-flight check: Confirm prerequisites 2.1.3 and 3.3.2 have not been implemented. If they have, STOP and rebase onto prerequisite work.
  - [ ] (TBD) Clarify "host-neutral" definition and inventory existing TUI-specific modules in the codebase.
  - [ ] (TBD) Determine where the TUI adapter layer should live (e.g., `src/tui/adapters/`, `.github/actions/*/src/tui/`) and confirm `workflow_scripts/review_views/` is the right home for domain models.
  - [ ] (TBD) Create ADR 008: PR Discussion Summary Contract (defines summary DTO schema and ReviewViewRef structure).
  - [ ] (TBD) Create ADR 010: Review Adapter Capability Gap (explains view resolver pattern, adapter responsibilities, and TUI/domain boundary).
  - [ ] (TBD) Design the view resolver interface, including resolver semantics (what each host returns for a given ReviewViewRef).
  - [ ] (TBD) Design reference equality and round-trip serialization strategy.
  - [ ] (TBD) Obtain design approval before proceeding to Phase 2.

- [ ] **Phase 2: Domain Model Implementation (Red-Green-Refactor)**
  - [ ] (TBD) Write failing tests for `ReviewViewRef` serialization, deserialization, error handling, and edge cases (missing fields, round-trip, JSON safety, boundary isolation).
  - [ ] (TBD) Implement `ReviewViewRef` dataclass in `workflow_scripts/review_views/models.py` (frozen, with slots, JSON-safe fields only).
  - [ ] (TBD) Implement view resolver interface in `workflow_scripts/review_views/resolution.py`.
  - [ ] (TBD) Add `workflow_scripts/review_views/errors.py` with custom exception types.
  - [ ] (TBD) Verify `models.py` and `resolution.py` have 100% coverage; verify no TUI imports appear in domain modules.
  - [ ] (TBD) Add static import verification test (enforce domain layer cannot import from TUI-specific modules).

- [ ] **Phase 3: Summary DTO Refactoring (Red-Green-Refactor)**
  - [ ] (TBD) Phase 1 output determines: does a summary DTO exist? If yes, refactor in place; if no, create `workflow_scripts/review_views/summary.py`.
  - [ ] (TBD) Write failing tests that verify summary DTOs do not import TUI types and expose `ReviewViewRef` instead of TUI-specific link fields (type hint constraint test, no-tui-import test, serialization test).
  - [ ] (TBD) Implement/refactor summary DTO to include `ReviewViewRef` field; remove or deprecate TUI-specific fields (no `frankie_url`, `tui_handler`, etc. in domain layer).
  - [ ] (TBD) Enumerate all existing consumers of the old summary DTO format and update them to use `ReviewViewRef`.
  - [ ] (TBD) Verify summary DTO module has ≥85% coverage and 100% coverage for no-TUI-import constraints.

- [ ] **Phase 4: TUI Adapter Layer and Registry**
  - [ ] (TBD) Phase 1 output determines: where should the TUI adapter live? (e.g., `src/tui/adapters/review_views.py`, `.github/actions/*/src/adapters/`, etc.). Document location in Decision Log.
  - [ ] (TBD) Implement ViewResolver registry in `workflow_scripts/review_views/adapters/__init__.py` to allow stateless, host-agnostic resolver discovery (TUI code registers its resolver at startup).
  - [ ] (TBD) Implement TUI adapter that translates `ReviewViewRef` to `frankie://` URLs (may live in TUI-specific module, not in domain layer).
  - [ ] (TBD) Write integration tests that verify TUI adapter correctly renders `frankie://` links from summary data.
  - [ ] (TBD) Verify no TUI types appear in domain layer (both via runtime sys.modules check and via boundary isolation test).

- [ ] **Phase 5: CLI and Library Testing (Non-TUI Contexts)**
  - [ ] (TBD) Write tests demonstrating that summary DTOs serialize/deserialize to JSON without TUI imports (`test_library_review_context.py`).
  - [ ] (TBD) Implement mock CLI resolver and write CLI integration test showing a CLI tool can consume review data and resolve view references without TUI dependency (`test_cli_review_context.py`).
  - [ ] (TBD) Write library example code showing how to export review data, serialize it, and consume it in a context with zero TUI imports.
  - [ ] (TBD) Verify both CLI and library tests pass with ≥85% coverage.

- [ ] **Phase 6: Documentation and Quality Gates (Local)**
  - [ ] (TBD) Update `docs/developers-guide.md` with a new "View Resolution Pattern" section: explain how domain models expose view references, how adapters translate them per-host, include code example showing the three layers, and add ASCII dependency diagram.
  - [ ] (TBD) Update `docs/users-guide.md` with observable behavior (how users will interact with view references in CLI and library contexts).
  - [ ] (TBD) Run `make check-fmt`, `make lint`, `make test` and verify all pass with no failures.
  - [ ] (TBD) Verify coverage: use project's canonical tooling (likely `make test` with slipcover) and confirm ≥85% for all new modules, 100% for domain models.

- [ ] **Phase 7: Integration and Edge Cases**
  - [ ] (TBD) Write tests for view reference resolution edge cases (missing references, ambiguous IDs, invalid hosts).
  - [ ] (TBD) Integration test with real TUI: verify that TUI can still render review comments and links after changes.
  - [ ] (TBD) Verify backward compatibility: if existing code exports review data, verify it still works.

- [ ] **Phase 8: Final Quality Verification (Local)**
  - [ ] (TBD) Run full test suite: `make test` and verify all pass.
  - [ ] (TBD) Run `make check-fmt` and `make lint` and verify no violations.
  - [ ] (TBD) Verify coverage: ≥85% for all new modules, 100% for domain models.
  - [ ] (TBD) Confirm all constraints and tolerances have been met.

- [ ] **Phase 9: Code Review and Completion**
  - [ ] (TBD) Rename branch to `3-3-3-replace-shared-tui-references-with-host-neutral-references` and push to remote.
  - [ ] (TBD) Create draft PR with execplan summary, ADR 008/010 links, and lody session reference.
  - [ ] (TBD) Request CodeRabbit review: `coderabbit review --agent`.
  - [ ] (TBD) Resolve all CodeRabbit concerns and mark PR ready for review.
  - [ ] (TBD) Mark task 3.3.3 as "done" in roadmap (if roadmap exists).


## Surprises & discoveries

Unexpected findings during implementation that were not anticipated as risks.

- Observation: (placeholder)
  Evidence: (to be filled as work progresses)
  Impact: (to be filled as work progresses)


## Decision log

Record every significant decision made while working on the plan.

- Decision: Use Python dataclasses for `ReviewViewRef` and related DTOs (following project convention).
  Rationale: The project already uses dataclasses extensively (e.g., `stage_common/` pattern). This ensures consistency and type safety with mypy.
  Date/Author: 2026-06-18 Claude (planning phase)

- Decision: Place domain models in new `workflow_scripts/review_views/` submodule, following the `stage_common/` pattern.
  Rationale: The codebase already separates domain logic into dedicated submodules. This pattern is proven in the monorepo and keeps review-specific code isolated and testable.
  Date/Author: 2026-06-18 Claude (planning phase)

- Decision: Assume prerequisites (2.1.3 and 3.3.2) do not yet exist and plan for greenfield implementation of review data structures.
  Rationale: Codebase exploration found no existing review domain models or TUI-specific linking code. If prerequisites are implemented later, this plan can be adapted.
  Date/Author: 2026-06-18 Claude (planning phase)

- Decision: (placeholder—to be filled during implementation)
  Rationale: (to be filled)
  Date/Author: TBD


## Outcomes & retrospective

Summarize outcomes, gaps, and lessons learned at major milestones or at completion.

- Placeholder: to be completed at end of work.


## Context and orientation

This project is a GitHub Actions monorepo written in Python, containing 14+ actions and shared utilities. The codebase demonstrates clear separation of concerns: presentation/CLI code at the `src/` level, domain logic in submodules like `stage_common/`, and infrastructure concerns isolated in adapters.

The project uses:
- **Python 3.13+** with strong type checking (mypy).
- **pytest** for testing with ≥80% branch coverage requirement.
- **dataclasses** for domain modeling.
- **Modular architecture** with explicit submodule boundaries (e.g., `stage_common/config.py`, `stage_common/pipeline.py`).

The task requires decoupling TUI-specific types from review data models. Currently, no `TuiViewLink`, `ReviewViewRef`, or review linking code exists in the codebase (confirmed via codebase exploration). This is greenfield work to introduce new abstractions that enable CLI and library code to consume review data without TUI-specific imports.

Key directories:
- `workflow_scripts/`: Where domain logic and data structures live (e.g., `stage-release-artefacts/stage_common/`).
- `workflow_scripts/tests/`: Where unit tests are located.
- `docs/adr/`: Where architecture decision records are stored.
- `docs/developers-guide.md`: Where internal patterns and conventions are documented.
- `docs/users-guide.md`: Where user-visible behavior is documented.


## Plan of work

The implementation proceeds in nine stages, each with clear validation and go/no-go criteria. Stages 1–3 are exploratory and design-focused; stages 4–8 are implementation and testing; stage 9 is completion.

**Stage 1: Design and Specification** answers the question "What is a host-neutral review view reference and how does it work?" This stage produces ADR 008 (summary contract) and ADR 010 (adapter gap). No code is written yet. Output: two ADRs and a decision on the `ReviewViewRef` abstraction and resolver interface. Validation: design review approval and clarity on the resolver protocol.

**Stage 2: Domain Model Implementation** introduces the core `ReviewViewRef` dataclass and a view resolver interface. Using Red-Green-Refactor, failing tests are written first to specify serialization and deserialization behavior. The `ReviewViewRef` must be serializable (JSON-safe) and must not include TUI-specific URLs or imports. Output: `workflow_scripts/review_views/models.py` with `ReviewViewRef` dataclass and `workflow_scripts/review_views/resolution.py` with resolver interface. Validation: tests pass, coverage ≥85%, no TUI imports in domain models.

**Stage 3: Summary DTO Refactoring** updates review summary data structures to use `ReviewViewRef` instead of TUI-specific link types. Using Red-Green-Refactor, failing tests verify that summary DTOs do not import from TUI modules and expose `ReviewViewRef`. Existing consumers are updated to use the new abstraction. Output: updated summary DTO (or new file `workflow_scripts/review_views/summary.py`). Validation: tests pass, coverage ≥85%, no TUI imports visible in the domain model.

**Stage 4: TUI Adapter Layer** implements a TUI-specific adapter that translates `ReviewViewRef` to `frankie://` URLs. This adapter lives in the TUI layer and can import TUI types (the boundary is one-directional: domain → TUI adapter, never TUI → domain). Output: `workflow_scripts/review_views/adapters/tui_adapter.py` with translation logic. Validation: integration tests verify that TUI can render correct URLs from review data.

**Stage 5: CLI and Library Testing** writes end-to-end tests demonstrating that summary DTOs can be serialized and used in non-TUI contexts (e.g., a CLI tool or library that has no TUI dependencies). Output: test cases in `workflow_scripts/tests/test_review_serialization.py` and `workflow_scripts/tests/test_cli_review_context.py`. Validation: tests pass, serialization round-trip works without TUI imports.

**Stage 6: Documentation** updates developer and user guides to explain the view resolver pattern and the new host-neutral reference design. Output: updates to `docs/developers-guide.md` and `docs/users-guide.md`. Validation: quality gates pass (fmt, lint, test).

**Stage 7: Integration and Edge Cases** covers error handling, missing references, and backward compatibility. Output: comprehensive edge-case tests. Validation: all tests pass, coverage remains ≥85%.

**Stage 8: Final Quality Verification** ensures the complete implementation meets all acceptance criteria. Output: passing tests, code review approval. Validation: make check-fmt, make lint, make test all pass; CodeRabbit review resolves all concerns.

**Stage 9: Completion** marks the task as done in the roadmap, renames the branch, and creates a PR. Output: draft PR with execplan summary and lody session link.


## Concrete steps

1. **Phase 1 exploration** (before any code changes): Run the following commands to understand the current state and confirm no review linking code exists:

```bash
cd /tmp/lody-title-agent
grep -r "TuiViewLink\|ReviewViewRef\|frankie" --include="*.py" . 2>/dev/null | grep -v ".venv" | head -20
```

Expected: No results or only results from venv or unrelated files.

```bash
find . -path "./.venv" -prune -o -name "*review*.py" -type f -print | grep -v ".venv"
```

Expected: List of any existing review-related files (may be empty).

2. **Phase 2 (Red-Green-Refactor)** starts with failing tests. Create `workflow_scripts/tests/test_review_views.py`:

```python
import pytest
import json
import sys
from dataclasses import dataclass
from workflow_scripts.review_views.models import ReviewViewRef

# Red tests: Serialization, deserialization, error handling, edge cases
def test_review_view_ref_serialization():
    """ReviewViewRef must be serializable without TUI imports."""
    ref = ReviewViewRef(comment_id="abc123", view_type="detail")
    data = ref.to_dict()
    assert "comment_id" in data
    assert "view_type" in data
    assert "frankie_url" not in data  # No TUI-specific fields

def test_review_view_ref_deserialization():
    """ReviewViewRef must deserialize from dict without TUI imports."""
    data = {"comment_id": "abc123", "view_type": "detail"}
    ref = ReviewViewRef.from_dict(data)
    assert ref.comment_id == "abc123"
    assert ref.view_type == "detail"

def test_review_view_ref_deserialization_missing_comment_id():
    """from_dict raises KeyError if comment_id is missing."""
    data = {"view_type": "detail"}
    with pytest.raises(KeyError):
        ReviewViewRef.from_dict(data)

def test_review_view_ref_serialization_roundtrip():
    """Serialization and deserialization are inverses."""
    original = ReviewViewRef(comment_id="xyz789", view_type="summary")
    dict_form = original.to_dict()
    restored = ReviewViewRef.from_dict(dict_form)
    assert restored == original

def test_review_view_ref_json_safe():
    """ReviewViewRef must be JSON-serializable."""
    ref = ReviewViewRef(comment_id="abc123", view_type="detail")
    json_str = json.dumps(ref.to_dict())  # Must not raise
    data = json.loads(json_str)
    restored = ReviewViewRef.from_dict(data)
    assert restored == ref

def test_domain_models_do_not_import_tui():
    """Domain models must have zero TUI imports at runtime."""
    # Record modules before import
    modules_before = set(sys.modules.keys())
    
    from workflow_scripts.review_views.models import ReviewViewRef
    
    # Check modules loaded during import
    modules_after = set(sys.modules.keys())
    new_modules = modules_after - modules_before
    tui_modules = [m for m in new_modules if 'tui' in m.lower() or 'frankie' in m.lower()]
    assert not tui_modules, f"Domain imported TUI modules: {tui_modules}"
```

Run the tests to confirm they fail:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_review_view_ref_serialization -xvs
```

Expected output:

```plaintext
FAILED workflow_scripts/tests/test_review_views.py::test_review_view_ref_serialization
ModuleNotFoundError: No module named 'workflow_scripts.review_views'
```

3. **Phase 2 (Green)** implements the minimal code to make the test pass. Create `workflow_scripts/review_views/__init__.py` (empty) and `workflow_scripts/review_views/models.py`:

```python
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class ReviewViewRef:
    """Host-neutral reference to a review view. No TUI-specific types."""
    comment_id: str
    view_type: str  # e.g., "detail", "summary"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (JSON-safe)."""
        return {
            "comment_id": self.comment_id,
            "view_type": self.view_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewViewRef":
        """Deserialize from dict."""
        return cls(
            comment_id=data["comment_id"],
            view_type=data["view_type"],
        )
```

Run the test again:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_review_view_ref_serialization -xvs
```

Expected: test passes.

4. **Phase 3 verification**: After domain model is implemented, verify no TUI imports appear:

```bash
cd /tmp/lody-title-agent
python -c "import workflow_scripts.review_views.models; print('Domain model loaded successfully without TUI imports')"
```

Expected: successful import with no TUI-related errors.

5. **Phase 4 (TUI Adapter and Registry)**: Create `workflow_scripts/review_views/adapters/__init__.py`:

```python
from abc import ABC, abstractmethod
from workflow_scripts.review_views.models import ReviewViewRef

class ViewResolver(ABC):
    """Abstract resolver for translating ReviewViewRef to host-specific URLs."""
    @abstractmethod
    def resolve(self, ref: ReviewViewRef) -> str:
        """Resolve a ReviewViewRef to a host-specific view URL."""
        pass

class ViewResolverRegistry:
    """Central registry for view resolvers across hosts."""
    _resolvers = {}
    
    @classmethod
    def register(cls, host: str, resolver: ViewResolver) -> None:
        cls._resolvers[host] = resolver
    
    @classmethod
    def resolve_for_host(cls, host: str) -> ViewResolver:
        if host not in cls._resolvers:
            raise ValueError(f"No resolver registered for host: {host}")
        return cls._resolvers[host]
```

Create `workflow_scripts/review_views/adapters/tui_adapter.py` (in TUI-specific module location, NOT in domain):

```python
from workflow_scripts.review_views.models import ReviewViewRef
from workflow_scripts.review_views.adapters import ViewResolver

class TUIViewResolver(ViewResolver):
    """Resolves ReviewViewRef to frankie:// URLs for TUI rendering."""
    def resolve(self, ref: ReviewViewRef) -> str:
        return f"frankie://review-comment/{ref.comment_id}?view={ref.view_type}"
```

Write tests in `workflow_scripts/tests/test_review_views.py`:

```python
def test_tui_adapter_translation():
    """TUI adapter translates ReviewViewRef to frankie:// URL."""
    from workflow_scripts.review_views.adapters.tui_adapter import TUIViewResolver
    ref = ReviewViewRef(comment_id="xyz789", view_type="detail")
    resolver = TUIViewResolver()
    url = resolver.resolve(ref)
    assert url == "frankie://review-comment/xyz789?view=detail"

def test_cli_resolver_without_tui():
    """Mock CLI resolver works without TUI imports."""
    from workflow_scripts.review_views.adapters import ViewResolver
    
    class CLIViewResolver(ViewResolver):
        def resolve(self, ref: ReviewViewRef) -> str:
            return f"cli://view/{ref.comment_id}"
    
    ref = ReviewViewRef(comment_id="abc123", view_type="detail")
    resolver = CLIViewResolver()
    url = resolver.resolve(ref)
    assert url == "cli://view/abc123"
    assert "frankie" not in url  # No TUI-specific content

def test_library_serializes_without_tui():
    """Library code can serialize review data for export without TUI imports."""
    import json
    ref = ReviewViewRef(comment_id="xyz789", view_type="summary")
    json_str = json.dumps(ref.to_dict())
    data = json.loads(json_str)
    restored = ReviewViewRef.from_dict(data)
    assert restored == ref
    assert "frankie_url" not in json_str
    assert "tui_" not in json_str
```

Run tests:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_tui_adapter_translation workflow_scripts/tests/test_review_views.py::test_cli_resolver_without_tui workflow_scripts/tests/test_review_views.py::test_library_serializes_without_tui -xvs
```

Expected: all tests pass.

6. **Quality gates after each phase**:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py -v --cov=workflow_scripts.review_views --cov-fail-under=85
```

Expected: all tests pass, coverage ≥85%.

```bash
cd /tmp/lody-title-agent
make lint  # or equivalent linting command
make check-fmt
```

Expected: no lint or format violations.

7. **Final validation** after all phases:

```bash
cd /tmp/lody-title-agent
make test
make lint
make check-fmt
```

Expected: all pass.

## Validation and acceptance

The feature is complete when all of the following acceptance criteria are satisfied:

**Phase 1 Pre-Flight Check:**
1. Prerequisites 2.1.3 and 3.3.2 do not exist in the codebase (confirmed via grep or git search).

```bash
cd /tmp/lody-title-agent
grep -r "ReviewCommentSummary\|PullRequestDiscussionSummary" --include="*.py" workflow_scripts/ || echo "OK: No existing summary contracts found"
```

Expected: "OK: No existing summary contracts found" or empty results.

**Phase 2 Validation: Domain Model is Host-Neutral**
2. `ReviewViewRef` and view resolver interface can be imported without TUI-specific modules:

```bash
python -c "
import sys
from workflow_scripts.review_views.models import ReviewViewRef
from workflow_scripts.review_views.resolution import ViewResolver
tui_modules = [m for m in sys.modules if 'tui' in m.lower() or 'frankie' in m.lower()]
assert not tui_modules, f'Domain imported TUI modules: {tui_modules}'
print('PASS: Domain models imported without TUI dependencies')
"
```

Expected: "PASS: Domain models imported without TUI dependencies"

3. Serialization is JSON-safe and round-trip preserves data:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_review_view_ref_serialization workflow_scripts/tests/test_review_views.py::test_review_view_ref_deserialization workflow_scripts/tests/test_review_views.py::test_review_view_ref_serialization_roundtrip workflow_scripts/tests/test_review_views.py::test_review_view_ref_json_safe -v
```

Expected: all 4 tests pass.

4. Deserialization error handling is correct:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_review_view_ref_deserialization_missing_comment_id -v
```

Expected: test passes (raises KeyError on missing fields).

**Phase 4 Validation: Adapter Boundary is Isolated**
5. Domain models do not import from adapter layer:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_domain_models_do_not_import_tui -v
```

Expected: test passes (zero TUI imports in domain).

6. Multiple adapters can be implemented and used independently:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_tui_adapter_translation workflow_scripts/tests/test_review_views.py::test_cli_resolver_without_tui -v
```

Expected: both tests pass (TUI and CLI adapters work independently).

**Phase 5 Validation: CLI and Library Contexts Work**
7. Library code can serialize review data without TUI imports:

```bash
cd /tmp/lody-title-agent
python -m pytest workflow_scripts/tests/test_review_views.py::test_library_serializes_without_tui -v
```

Expected: test passes (JSON serialization works, no TUI-specific fields).

**Phase 6 Validation: Code Quality**
8. All tests pass with adequate coverage:

```bash
cd /tmp/lody-title-agent
make test
```

Expected: all tests pass. Coverage (using project's canonical tooling, typically slipcover):
- Domain model files (`models.py`, `resolution.py`): ≥100%
- Adapter files: ≥85%
- All other files: ≥85%

9. Code quality gates pass:

```bash
cd /tmp/lody-title-agent
make check-fmt && make lint
```

Expected: all commands exit with status 0, no format or lint violations.

10. Documentation is updated:

- `docs/developers-guide.md` includes a "View Resolution Pattern" section with code example and ASCII dependency diagram.
- `docs/users-guide.md` describes how view references work in CLI and library contexts.

**Phase 9 Validation: Final PR and Review**
11. PR created with draft status and CodeRabbit review requested:

```bash
cd /tmp/lody-title-agent
# PR URL should contain "draft" label
# PR description includes execplan summary and lody session link
# CodeRabbit review completed: coderabbit review --agent
```

Expected: PR is draft, CodeRabbit review has no blocking concerns, all concerns resolved.


## Idempotence and recovery

All steps in this plan are idempotent and can be re-run safely:

- Running tests multiple times with the same code should produce the same results.
- Creating files is safe if the files don't exist; if they do, the plan specifies what should be in them.
- Deleting files: No destructive operations are planned. If a file needs to be removed, document the reason and decision in the Decision Log before deletion.

**Rollback**: If the entire implementation needs to be rolled back, delete the `workflow_scripts/review_views/` directory and revert any changes to existing files (tracked by git). No database migrations or configuration changes are planned.


## Artifacts and notes

Key design decisions and expected artifacts:

1. **ReviewViewRef dataclass** (critical, Phase 2):

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ReviewViewRef:
    """Host-neutral reference to a review comment."""
    comment_id: str
    view_type: str
    
    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        return {"comment_id": self.comment_id, "view_type": self.view_type}
    
    @classmethod
    def from_dict(cls, data: dict) -> "ReviewViewRef":
        """Deserialize from dict; raises KeyError if required fields missing."""
        return cls(comment_id=data["comment_id"], view_type=data["view_type"])
```

2. **ViewResolver interface and registry** (critical, Phase 4):

```python
from abc import ABC, abstractmethod
from workflow_scripts.review_views.models import ReviewViewRef

class ViewResolver(ABC):
    """Abstract resolver for translating ReviewViewRef to host-specific URLs."""
    @abstractmethod
    def resolve(self, ref: ReviewViewRef) -> str:
        """Resolve a ReviewViewRef to a host-specific view URL."""
        pass

class ViewResolverRegistry:
    """Central registry for view resolvers across hosts."""
    _resolvers = {}
    
    @classmethod
    def register(cls, host: str, resolver: ViewResolver) -> None:
        cls._resolvers[host] = resolver
    
    @classmethod
    def resolve_for_host(cls, host: str) -> ViewResolver:
        if host not in cls._resolvers:
            raise ValueError(f"No resolver registered for host: {host}")
        return cls._resolvers[host]
```

3. **Expected test files** (all should pass by end of Phase 7):
   - `workflow_scripts/tests/test_review_views.py` — comprehensive domain model tests (serialization, deserialization, error handling, edge cases, adapter boundary, JSON safety)
   - `workflow_scripts/tests/test_cli_review_context.py` — CLI resolver and non-TUI context
   - `workflow_scripts/tests/test_library_review_context.py` — library serialization without TUI

4. **ADRs to be created** (Phase 1 deliverables):
   - `docs/adr/0008-pr-discussion-summary-contract.md` — defines ReviewCommentSummary schema, ReviewViewRef structure, serialization format, and backward compatibility strategy
   - `docs/adr/0010-close-review-adapter-capability-gap.md` — explains ViewResolver pattern, adapter responsibilities (translation only, no business logic), hexagonal dependency direction, registry pattern, TUI/domain boundary enforcement

5. **Documentation updates** (Phase 6):
   - `docs/developers-guide.md`: New section "View Resolution Pattern" with code example, ASCII dependency diagram, and adapter registration example
   - `docs/users-guide.md`: Describe how users/developers interact with ReviewViewRef in CLI and library contexts


## Interfaces and dependencies

**New interfaces to be created**:

1. `ReviewViewRef` dataclass in `workflow_scripts/review_views/models.py`:
   - Fields: `comment_id: str`, `view_type: str` (frozen=True, slots=True if Python supports)
   - Methods: `to_dict() -> dict`, `from_dict(data: dict) -> ReviewViewRef`
   - Must be serializable to JSON without loss of information
   - Must not import TUI-specific modules
   - Deserialization must validate required fields and raise KeyError if missing
   - Serialization must not include TUI-specific fields or metadata

2. `ViewResolver` abstract base class in `workflow_scripts/review_views/adapters/__init__.py`:
   - Abstract method: `resolve(ref: ReviewViewRef) -> str`
   - Concrete implementations (one per host): `TUIViewResolver`, `CLIViewResolver`, `LibraryViewResolver`
   - Stateless (no shared mutable state; safe to instantiate once and reuse)

3. `ViewResolverRegistry` class in `workflow_scripts/review_views/adapters/__init__.py`:
   - Class methods: `register(host: str, resolver: ViewResolver)`, `resolve_for_host(host: str) -> ViewResolver`
   - Allows hosts to discover and use the correct resolver without importing domain types directly
   - TUI code calls `ViewResolverRegistry.register("tui", TUIViewResolver())` at startup

4. **ReviewCommentSummary DTO** in `workflow_scripts/review_views/summary.py` (or refactored from existing location, Phase 1 determines):
   - Fields: `id: str`, `text: str`, `view_ref: ReviewViewRef`
   - Methods: `to_dict() -> dict`, `from_dict(data: dict) -> ReviewCommentSummary`
   - Must not include TUI-specific link fields (`frankie_url`, `tui_handler`, etc.)
   - Must be serializable via to_dict()/from_dict() without TUI imports

5. Custom exception types in `workflow_scripts/review_views/errors.py`:
   - `ReviewViewError` (base exception)
   - `ResolutionError` (raised when view reference cannot be resolved)

**Dependencies** (external packages):

- **No new external packages required**. The implementation uses only Python standard library:
  - `dataclasses` (for dataclass decorator and frozen/slots)
  - `abc` (for ABC and abstractmethod)
  - `typing` (for type hints)
  - `json` (for serialization tests)

**Existing modules to integrate with** (identified during design phase, Phase 1):

- Any existing review summary export code in the codebase (location TBD, Phase 1 discovers these)
- TUI-specific code that renders review comments (will be updated to use ViewResolverRegistry)

---

## Revision Note (2026-06-18)

**What changed:**
- Added formal Glossary section defining key terms ("host-neutral reference", "host", "ReviewViewRef", "view resolver", "frankie://", "summary DTO")
- Enhanced Constraints to specify serialization format (JSON), type checking requirements, and coverage thresholds (100% for domain models, 85% for others)
- Added five new Risks specific to Python ecosystem (type checking, namespace pollution, coverage tooling, test infrastructure)
- Expanded Phase 1 with pre-flight check (verify prerequisites), explicit design artifacts (ADR 008/010), and approval gate
- Clarified Phase 3 decision criteria (create vs. refactor summary DTO)
- Introduced ViewResolverRegistry pattern in Phase 4 to prevent circular dependencies between TUI and domain layers
- Added comprehensive error handling and edge case tests in Phase 2 (missing fields, serialization round-trip, JSON safety, adapter boundary isolation)
- Expanded Phase 5 with mock CLI resolver and library context examples
- Moved CodeRabbit review from Phase 6 to Phase 9 (PR creation) where it belongs
- Enhanced validation and acceptance section with concrete test commands and expected outputs
- Updated Artifacts section with complete code examples (frozen dataclasses with slots, registry pattern, complete DTO)
- Updated Interfaces and dependencies to specify ViewResolverRegistry entry point and custom exception types

**Why it changed:**
The expert review team identified three classes of gaps:
1. **Foundational concept clarity**: "Host-neutral reference" needed formal definition before Phase 1 work could lock in the abstraction
2. **Architectural enforcement**: Hexagonal boundary violations (TUI types leaking into domain, adapter business logic) needed explicit tests and patterns (registry) to prevent
3. **Test completeness**: Error handling, edge cases, and adapter isolation tests were missing; CLI/library tests were mentioned but not exemplified

**How it affects remaining work:**
- Phase 1 is now more demanding (pre-flight check, design artifacts, approval gate) but reduces downstream rework
- Phase 2 test examples are now concrete and comprehensive, reducing ambiguity during Red-Green-Refactor
- Phases 4-5 include explicit adapter boundary tests and registry pattern, enforcing the hexagonal architecture
- The validation section now provides exact test commands and expected outputs, making acceptance criteria unambiguous
- Overall risk of architectural drift (TUI types in domain, circular dependencies) is significantly reduced through explicit tests and the registry pattern
