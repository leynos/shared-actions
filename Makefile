.PHONY: all clean help makeutil test test-act doctest lint lint-clippy lint-whitaker \
	markdownlint nixie fmt check-fmt typecheck spelling skylos-allow interrogate

export GITHUB_ACTION_PATH ?= $(CURDIR)

ifndef PYTHONPATH
export PYTHONPATH := $(CURDIR)
else
export PYTHONPATH := $(PYTHONPATH):$(CURDIR)
endif

all: fmt lint typecheck test spelling ## Run the complete validation suite

clean: ## Remove transient artefacts
	rm -rf .venv .venv-coverage .pytest_cache .ruff_cache workspace/.ruff_cache \
		.uv-cache .uv-tools

BUILD_JOBS ?=
ACTION_VALIDATOR ?= $(or $(firstword $(wildcard $(HOME)/.bun/bin/action-validator) $(wildcard $(HOME)/.cargo/bin/action-validator)),action-validator)
ACT ?= $(or $(firstword $(wildcard $(HOME)/go/bin/act) $(wildcard $(HOME)/.local/bin/act)),act)
# Opt into the act workflow lane. `ACT_WORKFLOW_TESTS` is the canonical name the
# pytest suite reads; `WITH_ACT` is an accepted alias so the lane can be asked
# for by what it is rather than by the variable that gates it.
WITH_ACT ?=
MDLINT ?= $(shell command -v markdownlint-cli2 2>/dev/null || printf '%s' "$$HOME/.bun/bin/markdownlint-cli2")
# `make fmt` and `make check-fmt` call mdtablefix directly. `--git` selects the
# Markdown files Git tracks and `--include-untracked` adds the untracked files
# Git does not ignore, so a new document is formatted before it is staged.
# Both modes need mdtablefix 0.6.0 or later; CI pins the version at the
# install-mdtablefix step.
MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences
NIXIE ?= nixie
RUFF_FIX_RULES ?= D202,I001
UV ?= $(if $(wildcard $(HOME)/.local/bin/uv),$(HOME)/.local/bin/uv,uv)
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
WHITAKER ?= $(if $(wildcard $(HOME)/.local/bin/whitaker),$(HOME)/.local/bin/whitaker,whitaker)
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.3
TYPOS_CONFIG_BUILDER = $(UV_ENV) $(UV) tool run --python 3.14 --from \
	"git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
	typos-config-builder
MAKEUTIL_REVISION := 7ab9b7b73a8f50ef1d3df548c7def7b94db35ba2
MAKEUTIL_TOOLCHAIN := nightly-2026-05-28
MAKEUTIL ?= $(if $(wildcard $(HOME)/.cargo/bin/makeutil),$(HOME)/.cargo/bin/makeutil,makeutil)
# The dead-code gate is the installed df12-skylos command; its `skylos` extra
# carries the one Skylos pin. Skylos parses source with its own runtime AST, so
# the tool environment runs Python 3.14 to avoid phantom dead-code findings
# from syntax older tool runtimes cannot parse. The package pin is the commit
# the df12-skylos action pins; bump them together.
DF12_GATES_SHA ?= b83e3eb850de93b3f9ce7fbc9ffdb429925714dc
DF12_GATES = git+https://github.com/leynos/df12-python-lints.git@$(DF12_GATES_SHA)
SKYLOS = $(UV_ENV) $(UV) tool run --python 3.14 --from \
	'df12-python-lints[skylos] @ $(DF12_GATES)' df12-skylos

# Modules whose docstring examples are executed.
#
# A named list rather than the whole tree, because `--doctest-modules`
# imports every module it collects and many action scripts are importable
# only with the `sys.path` their action sets up: collecting all of them
# fails at import in twenty-one places.
#
# A list is itself a trap, the same one `pytest.ini`'s testpaths used to be,
# where a module added to it ran nowhere and passed by never running. So
# `tests/workflows/test_doctest_coverage.py` asserts that every file
# carrying a `>>>` is named here, and fails naming the file when one is not.
DOCTEST_PATHS ?= bool_utils.py cargo_utils.py cmd_utils.py composite_fragments.py \
	test_support \
	.github/actions/determine-release-modes/scripts/determine_release_modes.py \
	.github/actions/generate-coverage/scripts/resolve_python.py \
	.github/actions/generate-coverage/scripts/run_python.py \
	.github/actions/setup-rust/scripts/install_mold.py \
	.github/actions/upload-codescene-coverage/scripts/prove_parser.py \
	.github/actions/upload-release-assets/scripts/upload_release_assets.py \
	packages/cv005-contracts/cv005_contracts \
	tests/workflows/_watchdog_command_reading.py \
	tests/workflows/publisher_binding.py \
	tests/workflows/pull_request_boundary.py \
	tests/workflows/test_coverage_timeout_tiers.py \
	tests/workflows/workflow_boundary.py \
	tests/workflows/workflow_expressions.py \
	tests/workflows/workflow_triggers.py \
	tests/workflows/workflow_yaml.py \
	uv_gate/uv_gate.py \
	workflow_scripts/dependabot_commit_audit.py \
	workflow_scripts/dependabot_decision.py \
	workflow_scripts/dependabot_metrics.py

doctest: .venv ## Execute the examples in docstrings
	$(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-bdd --with syrupy --with hypothesis pytest --doctest-modules -p no:cacheprovider -q $(DOCTEST_PATHS)

# `tests/workflows` is inside the default testpaths, and the act-dependent
# modules in it gate themselves on `ACT_WORKFLOW_TESTS`. The plain suite must
# see the falsy value, or an opt-in inherited from the environment would both
# run the lane's modules in the plain suite and leave
# `make test ACT_WORKFLOW_TESTS=1` running `tests/workflows` twice: once through
# the prerequisite lane, and again inside the default run, where the inherited
# `1` would have un-skipped them.
#
# The gate is set in make rather than in the recipe. An inline `VAR=value`
# prefix makes make run the whole line through a POSIX shell, and on Windows
# that shell consumes the backslashes in the `UV=C:\...` path a caller passes,
# so the binary is not found. A bare `$(UV)` line takes make's direct-exec path
# and the path survives; this line is byte-identical to the one `main` runs on
# Windows.
#
# Forcing the value needs both `export`, so the child pytest process sees it,
# and `override`, so a command-line `ACT_WORKFLOW_TESTS=1` cannot beat it. Make
# 3.81 -- what macOS ships as /usr/bin/make, and what parses this file from
# `tests/workflows` -- cannot parse the two keywords on one target-specific
# line ("multiple target patterns"), so `export` is global and `override` stays
# on the target. `test_makefile_act_lane_runs_once.py` holds the pair to it.
export ACT_WORKFLOW_TESTS
test: override ACT_WORKFLOW_TESTS := 0

makeutil: ## Verify the Makefile parser used by contract tests
	@command -v "$(MAKEUTIL)" >/dev/null 2>&1 || { printf '%s\n' 'Error: makeutil is required; install the pinned parser documented in docs/developers-guide.md' >&2; exit 1; }

test: makeutil .venv doctest ## Run tests, docstring examples first
	$(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist --with pytest-bdd --with syrupy --with hypothesis pytest -n auto --dist worksteal -v
# Truthy values: 1, true, TRUE, True, yes, YES, Yes, on, ON, On
# `WITH_ACT` is an alias for `ACT_WORKFLOW_TESTS`: `make test WITH_ACT=1` invites
# the act lane by name, which reads better than naming the suite's own
# environment variable at the command line. Either variable opts in; the lane
# still forces `ACT_WORKFLOW_TESTS=1` on itself so the pytest-side opt-in gate
# agrees with the Makefile-side one.
ACT_LANE_REQUESTED := $(strip $(filter 1 true TRUE True yes YES Yes on ON On,$(ACT_WORKFLOW_TESTS) $(WITH_ACT)))
ifneq ($(ACT_LANE_REQUESTED),)
test: test-act
endif

# The lane turns the gate back on, and hands pytest the `ACT` it resolved.
# Both ride on the target rather than the recipe line for the reason above: the
# line stays a bare `$(UV)` command for make to exec directly. `override` stops
# a command-line `ACT_WORKFLOW_TESTS=0` from turning the lane's own gate off,
# which would leave it silently running nothing.
test-act: export ACT := $(ACT)
test-act: override ACT_WORKFLOW_TESTS := 1
test-act: .venv ## Run the act workflow lane, independently of the plain suite
	$(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist --with pytest-bdd --with syrupy --with hypothesis pytest tests/workflows -v

.venv:
	$(UV) venv
	$(UV) sync --group dev

# Interrogate measures docstring presence over the Python files it scans under
# INTERROGATE_TARGETS (it skips some path prefixes by default, so check a new
# file against the verbose report). Add a new Python directory to the targets.
# No `--python` pin: a managed interpreter would be downloaded, and a PyO3
# build could link it.
INTERROGATE_VERSION ?= 1.7.0
INTERROGATE_TARGETS ?= .github packages tests test_support uv_gate workflow_scripts actions_common.py bool_utils.py cargo_utils.py cmd_utils.py cmd_utils.pyi cmd_utils_importer.py composite_fragments.py conftest.py
# Level 2 prints a MISSED row per undocumented definition, which the
# `interrogate` target fails on: the rounded percentage alone passes a lone gap.
INTERROGATE_FLAGS ?= -vv --ext pyi --fail-under 100
INTERROGATE = $(UV) tool run \
	--from 'interrogate==$(INTERROGATE_VERSION)' interrogate

# Interrogate rounds its percentage to one decimal, so `--fail-under 100` alone
# passes a lone gap in a large repository; fail on any MISSED row as well.
interrogate: ## Enforce complete Python docstring coverage
	@report="$$($(INTERROGATE) $(INTERROGATE_FLAGS) $(INTERROGATE_TARGETS))"; \
	  status=$$?; \
	  printf '%s\n' "$$report"; \
	  test "$$status" -eq 0 || exit "$$status"; \
	  if printf '%s\n' "$$report" | grep -q '| *MISSED *|'; then \
	    printf '%s\n' 'interrogate: undocumented definitions remain; see the MISSED rows above' >&2; \
	    exit 1; \
	  fi

lint: interrogate ## Check code and actions, including dead production code
	$(UV) tool run ruff check
	find .github/actions -type f \( -name 'action.yml' -o -name 'action.yaml' \) \
		-exec $(ACTION_VALIDATOR) {} \;
	$(MAKE) lint-clippy
	$(MAKE) lint-whitaker
	$(SKYLOS) check --repository .

lint-clippy: ## Run Clippy on rust-toy-app with warnings denied
	cd rust-toy-app && cargo clippy --all-targets --all-features -- -D warnings

lint-whitaker: ## Run the Whitaker Dylint suite on rust-toy-app with warnings denied
	cd rust-toy-app && RUSTFLAGS="-D warnings" $(WHITAKER) --all -- --all-targets --all-features

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception (SYMBOL=name REASON=text)
	@case "$${SKYLOS_SYMBOL}" in *\**|*\?*|*\[*) printf "Error: SYMBOL must not contain wildcard characters (*, ?, or [)\\n" >&2; exit 2;; esac
	$(SKYLOS) allow --repository . --symbol="$${SKYLOS_SYMBOL}" --reason="$${SKYLOS_REASON}"

typecheck: .venv ## Run static type checking with Ty
	$(UV) run ty check --python .venv \
		--extra-search-path . \
		--extra-search-path .github/actions/generate-coverage/scripts \
		--extra-search-path .github/actions/ratchet-coverage/scripts \
		--extra-search-path .github/actions/rust-build-release \
		--extra-search-path .github/actions/rust-build-release/src \
		--extra-search-path .github/actions/linux-packages \
		--extra-search-path .github/actions/linux-packages/scripts \
		--extra-search-path .github/actions/windows-package \
		--extra-search-path .github/actions/windows-package/scripts \
		--extra-search-path .github/actions/setup-rust/scripts \
		--extra-search-path .github/actions/install-mdtablefix/tests \
		--extra-search-path .github/actions/install-makeutil/scripts \
		--extra-search-path .github/actions/install-makeutil/tests \
		--extra-search-path uv_gate \
		--extra-search-path uv_gate/tests \
		cmd_utils.py \
		composite_fragments.py \
		.github/actions/generate-coverage/scripts \
		.github/actions/ratchet-coverage/scripts \
		.github/actions/linux-packages/scripts \
		.github/actions/rust-build-release/src \
		.github/actions/setup-rust/scripts \
		.github/actions/install-mdtablefix/tests \
		.github/actions/install-makeutil/scripts \
		.github/actions/install-makeutil/tests \
		uv_gate \
		.github/actions/windows-package/scripts
	$(UV) run ty check --python .venv \
		--extra-search-path . \
		--extra-search-path .github/actions/macos-package/scripts \
		.github/actions/macos-package/scripts
fmt: ## Format Python files and auto-fix selected lint rules
	$(UV) tool run ruff format
	$(UV) tool run ruff check --select $(RUFF_FIX_RULES) --fix
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)
	@unset FORCE_COLOR; $(MDLINT) --fix "**/*.md"

check-fmt: ## Check Python formatting without modifying files
	$(UV) tool run ruff format --check
	$(UV) tool run ruff check --select $(RUFF_FIX_RULES)
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

markdownlint: spelling ## Lint Markdown files and enforce spelling
	$(MDLINT) "**/*.md" "#.uv-cache" "#.uv-tools"

spelling: ## Enforce en-GB-oxendict spelling
	$(TYPOS_CONFIG_BUILDER) gate --repository .

nixie: ## Validate Mermaid diagrams
	$(NIXIE) --no-sandbox

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
