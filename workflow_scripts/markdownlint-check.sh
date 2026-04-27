#!/usr/bin/env sh
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
