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
MDLINT ?= markdownlint
NIXIE ?= nixie
RUFF_FIX_RULES ?= D202,I001

test: .venv ## Run tests
	uv run --with typer --with packaging --with plumbum --with pyyaml pytest -v

.venv:
	uv venv
	uv sync --group dev

lint: ## Check test scripts and actions
	uvx ruff check
	find .github/actions -type f \( -name 'action.yml' -o -name 'action.yaml' \) -print0 \
	| xargs -r -0 -n1 bunx -y @action-validator/cli

typecheck: .venv ## Run static type checking with Ty
	./.venv/bin/ty check \
		--extra-search-path . \
		--extra-search-path .github/actions/generate-coverage/scripts \
		--extra-search-path .github/actions/ratchet-coverage/scripts \
		--extra-search-path .github/actions/rust-build-release \
		--extra-search-path .github/actions/rust-build-release/src \
		--extra-search-path .github/actions/linux-packages \
		--extra-search-path .github/actions/linux-packages/scripts \
		--extra-search-path .github/actions/setup-rust/scripts \
		cmd_utils.py \
		.github/actions/generate-coverage/scripts \
		.github/actions/ratchet-coverage/scripts \
		.github/actions/linux-packages/scripts \
		.github/actions/rust-build-release/src \
		.github/actions/setup-rust/scripts \
		shellstub.py
	./.venv/bin/ty check \
		--extra-search-path . \
		--extra-search-path .github/actions/macos-package/scripts \
		.github/actions/macos-package/scripts
fmt: ## Format Python files and auto-fix selected lint rules
	uvx ruff format
	uvx ruff check --select $(RUFF_FIX_RULES) --fix

check-fmt: ## Check Python formatting without modifying files
	uvx ruff format --check
	uvx ruff check --select $(RUFF_FIX_RULES)

markdownlint: ## Lint Markdown files
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(MDLINT)

nixie: ## Validate Mermaid diagrams
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(NIXIE)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
