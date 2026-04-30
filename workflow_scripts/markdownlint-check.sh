#!/usr/bin/env sh
#
# markdownlint-check.sh - Run markdownlint over changed or all Markdown files.
#
# Usage (called from the Makefile `markdownlint` target):
#   MARKDOWNLINT_BASE=origin/main MDLINT=markdownlint ./workflow_scripts/markdownlint-check.sh
#
# Environment variables:
#   MARKDOWNLINT_BASE  Git ref used as the base for `git diff`; defaults to
#                      origin/main.  If the ref does not exist, falls back to
#                      linting all *.md files.
#   MDLINT             Path or name of the markdownlint binary; defaults to
#                      markdownlint.
#
set -eu

MARKDOWNLINT_BASE="${MARKDOWNLINT_BASE:-origin/main}"
MDLINT="${MDLINT:-markdownlint}"

if files=$(git diff --name-only --diff-filter=ACMRT "${MARKDOWNLINT_BASE}...HEAD" -- '*.md' 2>/dev/null); then
    if [ -n "${files}" ]; then
        status=0
        while IFS= read -r file; do
            "${MDLINT}" "${file}" || status=1
        done <<EOF
${files}
EOF
        exit "${status}"
    else
        find . -type f -name '*.md' -not -path './target/*' -exec "${MDLINT}" {} +
    fi
else
    echo "markdownlint: git diff failed or base '${MARKDOWNLINT_BASE}' not found; linting all .md files" >&2
    find . -type f -name '*.md' -not -path './target/*' -exec "${MDLINT}" {} +
fi
