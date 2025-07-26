.PHONY: help test lint markdownlint nixie fmt

BUILD_JOBS ?=
MDLINT ?= markdownlint
NIXIE ?= nixie

test: ## Run tests
	uv run pytest

lint: ## Check test scripts and actions
	uvx ruff check
	find .github/actions -type f \( -name 'action.yml' -o -name 'action.yaml' \) -print0 \
		| xargs -r -0 -n1 ${HOME}/.bun/bin/action-validator

fmt: ## Apply formatting to Python files
	uvx ruff format

markdownlint: ## Lint Markdown files
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(MDLINT)

nixie: ## Validate Mermaid diagrams
	find . -type f -name '*.md' -not -path './target/*' -print0 | xargs -0 -- $(NIXIE)

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'
