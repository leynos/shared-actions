.PHONY: all clean help test lint lint-whitaker markdownlint nixie fmt check-fmt \
	typecheck spelling spelling-config spelling-config-write \
	spelling-phrase-check spelling-helper-test

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
MDLINT ?= $(if $(wildcard $(HOME)/.bun/bin/markdownlint-cli2),$(HOME)/.bun/bin/markdownlint-cli2,markdownlint-cli2)
NIXIE ?= nixie
RUFF_FIX_RULES ?= D202,I001
UV ?= $(if $(wildcard $(HOME)/.local/bin/uv),$(HOME)/.local/bin/uv,uv)
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
WHITAKER ?= $(if $(wildcard $(HOME)/.local/bin/whitaker),$(HOME)/.local/bin/whitaker,whitaker)
RUFF_VERSION ?= 0.15.12
PATHSPEC_VERSION ?= 1.1.1
TYPOS_VERSION ?= 1.48.0
TYPOS_CONFIG_BUILDER_COMMIT := d6da92f02240a79a945c835f69bdd08a888da1d0
TYPOS_CONFIG_BUILDER_SOURCE := git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_COMMIT)
TYPOS_CONFIG_BUILDER := $(UV_ENV) $(UV) tool run --python 3.14 \
	--from "$(TYPOS_CONFIG_BUILDER_SOURCE)" typos-config-builder
SPELLING_PY_SRCS := \
	scripts/typos_rollout_check.py scripts/tests/test_typos_rollout_check.py
SPELLING_PY_TESTS := scripts/tests/test_typos_rollout_check.py
SPELLING_COVERAGE_ARGS := --cov=typos_rollout_check --cov-fail-under=90
SPELLING_HELPER_PYTEST = PYTHONPATH=scripts $(UV_ENV) $(UV) run --no-project \
	--python 3.14 --with pathspec==$(PATHSPEC_VERSION) --with pytest==9.0.2 \
	--with pytest-cov==7.0.0 python -m pytest

test: .venv ## Run tests
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
	./.venv/bin/ty check \
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
		cmd_utils.py \
		.github/actions/generate-coverage/scripts \
		.github/actions/ratchet-coverage/scripts \
		.github/actions/linux-packages/scripts \
		.github/actions/rust-build-release/src \
		.github/actions/setup-rust/scripts \
		.github/actions/windows-package/scripts
	./.venv/bin/ty check \
		--extra-search-path . \
		--extra-search-path .github/actions/macos-package/scripts \
		.github/actions/macos-package/scripts
fmt: ## Format Python files and auto-fix selected lint rules
	$(UV) tool run ruff format
	$(UV) tool run ruff check --select $(RUFF_FIX_RULES) --fix

check-fmt: ## Check Python formatting without modifying files
	$(UV) tool run ruff format --check
	$(UV) tool run ruff check --select $(RUFF_FIX_RULES)

markdownlint: spelling ## Lint Markdown files and enforce spelling
	$(MDLINT) "**/*.md" "#.uv-cache" "#.uv-tools"

spelling: spelling-phrase-check ## Enforce en-GB-oxendict spelling in tracked Markdown prose
	@git ls-files -z '*.md' | xargs -0 -r env $(UV_ENV) \
		$(UV) tool run typos@$(TYPOS_VERSION) --config typos.toml --force-exclude

spelling-phrase-check: spelling-config ## Reject prohibited spelling phrases
	@PYTHONPATH=scripts $(UV_ENV) $(UV) run --no-project --python 3.14 scripts/typos_rollout_check.py --repository .

spelling-config: spelling-helper-test ## Verify the generated spelling configuration
	@git ls-files --error-unmatch typos.toml >/dev/null
	@$(TYPOS_CONFIG_BUILDER) --repository . --check

spelling-config-write: spelling-helper-test ## Generate the spelling configuration
	@$(TYPOS_CONFIG_BUILDER) --repository .

spelling-helper-test: ## Validate the shared spelling-policy integration
	@$(UV_ENV) $(UV) tool run ruff@$(RUFF_VERSION) format --isolated --target-version py313 --check $(SPELLING_PY_SRCS)
	@$(UV_ENV) $(UV) tool run ruff@$(RUFF_VERSION) check --isolated --target-version py313 $(SPELLING_PY_SRCS)
	@$(SPELLING_HELPER_PYTEST) $(SPELLING_PY_TESTS) -c /dev/null --rootdir=. \
		--confcutdir=scripts -p no:cacheprovider $(SPELLING_COVERAGE_ARGS)

nixie: ## Validate Mermaid diagrams
	$(NIXIE) --no-sandbox

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
