.PHONY: all clean help test lint markdownlint nixie fmt typecheck

all: fmt lint typecheck test ## Run format, lint, typecheck, then tests

clean: ## Remove transient artefacts
	rm -rf .venv .pytest_cache .ruff_cache .pyright workspace/.ruff_cache

BUILD_JOBS ?=
MDLINT ?= markdownlint
NIXIE ?= nixie

test: .venv ## Run tests
	uv run pytest

.venv:
	uv venv
	uv sync --group dev

lint: ## Check test scripts and actions
	uvx ruff check
	find .github/actions -type f \( -name 'action.yml' -o -name 'action.yaml' \) -print0 \
	| xargs -r -0 -n1 ${HOME}/.bun/bin/action-validator

typecheck: .venv ## Run static type checking with Ty
	./.venv/bin/ty check \
	  --extra-search-path .github/actions/generate-coverage/scripts \
	  --extra-search-path .github/actions/ratchet-coverage/scripts \
	  --extra-search-path .github/actions/rust-build-release/scripts \
	  --extra-search-path .github/actions/setup-rust/scripts \
	  cmd_utils.py \
	  .github/actions/generate-coverage/scripts \
	  .github/actions/ratchet-coverage/scripts \
	  .github/actions/rust-build-release/scripts \
	  .github/actions/rust-build-release/src \
	  .github/actions/setup-rust/scripts \
	  shellstub.py

fmt: ## Apply formatting to Python files
	uvx ruff format

markdownlint: ## Lint Markdown files
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(MDLINT)

nixie: ## Validate Mermaid diagrams
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(NIXIE)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
