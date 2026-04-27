.PHONY: all clean help test lint markdownlint nixie fmt check-fmt typecheck

export GITHUB_ACTION_PATH ?= $(CURDIR)

ifndef PYTHONPATH
export PYTHONPATH := $(CURDIR)
else
export PYTHONPATH := $(PYTHONPATH):$(CURDIR)
endif

all: fmt lint typecheck test ## Run format, lint, typecheck, then tests

clean: ## Remove transient artefacts
	rm -rf .venv .pytest_cache .ruff_cache workspace/.ruff_cache

BUILD_JOBS ?=
ACTION_VALIDATOR ?= $(if $(wildcard $(HOME)/.cargo/bin/action-validator),$(HOME)/.cargo/bin/action-validator,action-validator)
MDLINT ?= $(if $(wildcard $(HOME)/.bun/bin/markdownlint),$(HOME)/.bun/bin/markdownlint,markdownlint)
MARKDOWNLINT_BASE ?= origin/main
NIXIE ?= nixie
RUFF_FIX_RULES ?= D202,I001
UV ?= $(if $(wildcard $(HOME)/.local/bin/uv),$(HOME)/.local/bin/uv,uv)

test: .venv ## Run tests
	$(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist pytest -n auto --dist worksteal -v
# Truthy values: 1, true, TRUE, True, yes, YES, Yes, on, ON, On
ifneq ($(strip $(filter 1 true TRUE True yes YES Yes on ON On,$(ACT_WORKFLOW_TESTS))),)
	ACT_WORKFLOW_TESTS=1 $(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist pytest tests/workflows -v
endif

.venv:
	$(UV) venv
	$(UV) sync --group dev

lint: ## Check test scripts and actions
	$(UV) tool run ruff check
	find .github/actions -type f \( -name 'action.yml' -o -name 'action.yaml' \) \
		-exec $(ACTION_VALIDATOR) {} \;

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

markdownlint: ## Lint Markdown files
	MARKDOWNLINT_BASE='$(MARKDOWNLINT_BASE)' MDLINT='$(MDLINT)' ./workflow_scripts/markdownlint-check.sh

nixie: ## Validate Mermaid diagrams
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(NIXIE)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
