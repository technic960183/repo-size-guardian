"""
Branch loading and change enumeration.

This module composes low-level git operations from git_utils to provide
clear, typed results without touching subprocess results directly.
"""

from typing import Dict, Iterator

from .git_utils import get_diff_files, list_commits


def enumerate_changed_blobs(commit_range: str) -> Iterator[Dict[str, str]]:
    """
    Enumerate changed blobs in the given commit range.

    Args:
        commit_range: Git commit range (e.g., 'abc123..def456')

    Yields:
        Dict with keys: path, blob_sha, commit_sha, status
        - path: File path
        - blob_sha: Blob SHA hash (empty string for deleted files)
        - commit_sha: Commit SHA where this change occurred
        - status: Change status (A=added, M=modified, D=deleted, etc.)
    """
    # Get all commits in the range (reverse chronological as provided by git)
    commits = list_commits(commit_range)

    for commit_sha in commits:
        # List files changed in this commit with their statuses
        files = get_diff_files(commit_sha)

        for file in files:
            status = file["status"]
            path = file["path"]

            if status.startswith("D"):
                # Deleted files don't have a blob at this commit
                yield {
                    "path": path,
                    "blob_sha": "",
                    "commit_sha": commit_sha,
                    "status": status,
                }
                continue

            # `get_diff_files` already resolves the post-image blob SHA from
            # `git diff-tree --raw`, so no per-path `git rev-parse` is needed.
            yield {
                "path": path,
                "blob_sha": file["blob_sha"],
                "commit_sha": commit_sha,
                "status": status,
            }
