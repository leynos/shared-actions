.PHONY: help test lint typecheck check-fmt fmt mutation-ledger all

UV ?= uv
PYTHON ?= 3.13
RUN = $(UV) run --python $(PYTHON)

all: check-fmt lint typecheck test mutation-ledger ## Run every gate

test: ## Run the suite
	$(RUN) python -m pytest -q -n auto --doctest-modules cv005_contracts tests

lint: ## Lint with the repository's Ruff configuration
	$(UV) tool run ruff check .

typecheck: ## Type-check the library and its tests with ty
	$(RUN) --with ty ty check cv005_contracts tests scripts

check-fmt: ## Check formatting
	$(UV) tool run ruff format --check .

fmt: ## Apply formatting
	$(UV) tool run ruff format .

mutation-ledger: ## Prove every clause by mutation
	$(RUN) python scripts/mutation_ledger.py

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*##"} {printf "  %-16s %s\\n", $$1, $$2}'
