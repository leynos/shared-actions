.PHONY: all clean help makeutil test lint lint-whitaker markdownlint nixie fmt check-fmt \
	typecheck spelling skylos-allow

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
SKYLOS_VERSION ?= 4.33.2
MAKEUTIL_REVISION := 29fc5a1634ffbaa18a773eed9dff1b2838a45d9c
MAKEUTIL_TOOLCHAIN := nightly-2026-05-28
MAKEUTIL ?= $(if $(wildcard $(HOME)/.cargo/bin/makeutil),$(HOME)/.cargo/bin/makeutil,makeutil)
# Skylos parses source using its own Python AST, so Python 3.14 prevents
# phantom dead-code findings from syntax older tool runtimes cannot parse.
SKYLOS_CLI = $(UV_ENV) $(UV) tool run --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' skylos
SKYLOS = $(SKYLOS_CLI) --config-file pyproject.toml
SKYLOS_PRODUCTION_TARGETS ?= .github/actions workflow_scripts scripts \
	actions_common.py bool_utils.py cargo_utils.py cmd_utils.py cmd_utils_importer.py
SKYLOS_EXCLUDE_FOLDERS ?= tests
SKYLOS_WHITELIST_LOCK ?= .skylos-whitelist.lock

makeutil: ## Verify the Makefile parser used by contract tests
	@command -v $(MAKEUTIL) >/dev/null 2>&1 || { printf "Error: makeutil is required; install the pinned parser documented in docs/developers-guide.md\\n" >&2; exit 1; }

test: makeutil .venv ## Run tests
	$(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist --with pytest-bdd --with syrupy --with hypothesis pytest -n auto --dist worksteal -v
# Truthy values: 1, true, TRUE, True, yes, YES, Yes, on, ON, On
ifneq ($(strip $(filter 1 true TRUE True yes YES Yes on ON On,$(ACT_WORKFLOW_TESTS))),)
	ACT='$(ACT)' ACT_WORKFLOW_TESTS=1 $(UV) run --with typer --with packaging --with plumbum --with pyyaml --with pytest-xdist --with pytest-bdd --with syrupy --with hypothesis pytest tests/workflows -v
endif

.venv:
	$(UV) venv
	$(UV) sync --group dev

lint: ## Check code and actions, including dead production code
	$(UV) tool run ruff check
	find .github/actions -type f \( -name 'action.yml' -o -name 'action.yaml' \) \
		-exec $(ACTION_VALIDATOR) {} \;
	$(MAKE) lint-whitaker
	$(SKYLOS) $(SKYLOS_PRODUCTION_TARGETS) --exclude $(SKYLOS_EXCLUDE_FOLDERS) --category dead_code \
		--gate --format concise --no-upload --no-provenance --no-grep-verify

lint-whitaker: ## Run the Whitaker Dylint suite on rust-toy-app with warnings denied
	cd rust-toy-app && RUSTFLAGS="-D warnings" $(WHITAKER) --all -- --all-targets --all-features

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception, not an entry point
	@case "$${SKYLOS_SYMBOL}" in *[![:space:]]*) ;; *) printf "Error: SYMBOL is required for a named whitelist exception\\n" >&2; exit 2;; esac
	@case "$${SKYLOS_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a named whitelist exception\\n" >&2; exit 2;; esac
	flock "$(SKYLOS_WHITELIST_LOCK)" env $(SKYLOS_CLI) whitelist "$${SKYLOS_SYMBOL}" --reason "$${SKYLOS_REASON}"

typecheck: .venv ## Run static type checking with Ty
	$(UV) run ty check \
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
	$(UV) run ty check \
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
