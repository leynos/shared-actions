.PHONY: all clean help test doctest lint lint-whitaker markdownlint nixie fmt \
	check-fmt typecheck spelling

export GITHUB_ACTION_PATH ?= $(CURDIR)

ifndef PYTHONPATH
export PYTHONPATH := $(CURDIR)
else
export PYTHONPATH := $(PYTHONPATH):$(CURDIR)
endif

all: fmt lint typecheck test spelling ## Run the complete validation suite

clean: ## Remove transient artefacts
	rm -rf .venv .pytest_cache .ruff_cache workspace/.ruff_cache .uv-cache .uv-tools

BUILD_JOBS ?=
ACTION_VALIDATOR ?= $(or $(firstword $(wildcard $(HOME)/.bun/bin/action-validator) $(wildcard $(HOME)/.cargo/bin/action-validator)),action-validator)
ACT ?= $(or $(firstword $(wildcard $(HOME)/go/bin/act) $(wildcard $(HOME)/.local/bin/act)),act)
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
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.1
TYPOS_CONFIG_BUILDER = $(UV_ENV) $(UV) tool run --python 3.14 --from \
	"git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
	typos-config-builder

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
	.github/actions/upload-release-assets/scripts/upload_release_assets.py \
	tests/workflows/test_coverage_timeout_tiers.py

doctest: .venv ## Execute the examples in docstrings
	$(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-bdd --with syrupy --with hypothesis pytest --doctest-modules -p no:cacheprovider -q $(DOCTEST_PATHS)

test: .venv doctest ## Run tests, docstring examples first
	$(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist --with pytest-bdd --with syrupy --with hypothesis pytest -n auto --dist worksteal -v
# Truthy values: 1, true, TRUE, True, yes, YES, Yes, on, ON, On
ifneq ($(strip $(filter 1 true TRUE True yes YES Yes on ON On,$(ACT_WORKFLOW_TESTS))),)
	ACT='$(ACT)' ACT_WORKFLOW_TESTS=1 $(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist --with pytest-bdd --with syrupy --with hypothesis pytest tests/workflows -v
endif

.venv:
	$(UV) venv
	$(UV) sync --group dev

lint: ## Check test scripts and actions, then run the Whitaker Dylint suite
	$(UV) tool run ruff check
	find .github/actions -type f \( -name 'action.yml' -o -name 'action.yaml' \) \
		-exec $(ACTION_VALIDATOR) {} \;
	$(MAKE) lint-whitaker

lint-whitaker: ## Run the Whitaker Dylint suite on rust-toy-app with warnings denied
	cd rust-toy-app && RUSTFLAGS="-D warnings" $(WHITAKER) --all -- --all-targets --all-features

typecheck: .venv ## Run static type checking with Ty
	./.venv/bin/ty check --python .venv \
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
		cmd_utils.py \
		composite_fragments.py \
		.github/actions/generate-coverage/scripts \
		.github/actions/ratchet-coverage/scripts \
		.github/actions/linux-packages/scripts \
		.github/actions/rust-build-release/src \
		.github/actions/setup-rust/scripts \
		.github/actions/install-mdtablefix/tests \
		.github/actions/windows-package/scripts
	./.venv/bin/ty check --python .venv \
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
