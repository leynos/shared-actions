"""The GraphQL documents the Dependabot auto-merge helper sends.

Kept together and apart from the decision flow, so the shape GitHub is
asked for can be read in one place. The commit connection itself lives in
:mod:`dependabot_commit_audit`, whose adapter is the only code that
reads the response back.
"""

from __future__ import annotations

if __package__:
    from .dependabot_commit_audit import COMMITS_FRAGMENT
else:
    from dependabot_commit_audit import (  # type: ignore[import-not-found,no-redef]
        COMMITS_FRAGMENT,
    )

PULL_REQUEST_QUERY = (
    """
query PullRequestInfo(
  $owner: String!
  $name: String!
  $number: Int!
  $commitPageSize: Int!
  $authorPageSize: Int!
  $commitCursor: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      number
      isDraft
      mergeStateStatus
      mergeable
      author {
        login
      }
      labels(first: 100) {
        nodes {
          name
        }
      }
      autoMergeRequest {
        enabledAt
        mergeMethod
      }
"""
    + COMMITS_FRAGMENT
    + """
    }
  }
}
"""
)

COMMITS_PAGE_QUERY = (
    """
query PullRequestCommits(
  $owner: String!
  $name: String!
  $number: Int!
  $commitPageSize: Int!
  $authorPageSize: Int!
  $commitCursor: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
"""
    + COMMITS_FRAGMENT
    + """
    }
  }
}
"""
)

ENABLE_AUTOMERGE_MUTATION = """
mutation EnableAutomerge($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(
    input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}
  ) {
    pullRequest {
      number
    }
  }
}
"""

DISABLE_AUTOMERGE_MUTATION = """
mutation DisableAutomerge($pullRequestId: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId}) {
    pullRequest {
      number
    }
  }
}
"""

MERGE_PULL_REQUEST_MUTATION = """
mutation MergePullRequest($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  mergePullRequest(
    input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}
  ) {
    pullRequest {
      number
      merged
    }
  }
}
"""
