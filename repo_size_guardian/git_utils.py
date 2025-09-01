"""
Git utilities for repository analysis.

Provides functions for computing commit ranges, listing commits,
and enumerating changed blobs in a PR context.
"""

import subprocess
from typing import Dict, Iterator, List


def get_merge_base(base_ref: str, head_ref: str) -> str:
    """
    Get the merge base between two git references.

    Args:
        base_ref: Base reference (e.g., 'origin/main')
        head_ref: Head reference (e.g., 'HEAD')

    Returns:
        The SHA of the merge base commit

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    result = subprocess.run(
        ['git', 'merge-base', base_ref, head_ref],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()


def list_commits(commit_range: str) -> List[str]:
    """
    List commits in the given range.

    Args:
        commit_range: Git commit range (e.g., 'abc123..def456')

    Returns:
        List of commit SHAs in the range

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    result = subprocess.run(
        ['git', 'rev-list', commit_range],
        capture_output=True,
        text=True,
        check=True
    )
    commits = result.stdout.strip()
    if not commits:
        return []
    return commits.split('\n')


def enumerate_changed_blobs(commit_range: str) -> Iterator[Dict[str, str]]:
    """
    Enumerate changed blobs in the given commit range.

    Args:
        commit_range: Git commit range (e.g., 'abc123..def456')

    Yields:
        Dict with keys: path, blob_sha, commit_sha, status
        - path: File path
        - blob_sha: Blob SHA hash
        - commit_sha: Commit SHA where this change occurred
        - status: Change status (A=added, M=modified, D=deleted, etc.)

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    # Get all commits in the range
    commits = list_commits(commit_range)

    for commit_sha in commits:
        # Use git diff-tree to get changed files in this commit
        result = subprocess.run(
            ['git', 'diff-tree', '--no-commit-id', '--name-status', '-r', commit_sha],
            capture_output=True,
            text=True,
            check=True
        )

        if not result.stdout.strip():
            continue

        # Parse the output to get status and paths
        for line in result.stdout.strip().split('\n'):
            parts = line.split('\t', 1)
            if len(parts) != 2:
                continue

            status = parts[0]
            path = parts[1]

            # Skip deleted files - we can't get their blob SHA
            if status.startswith('D'):
                yield {
                    'path': path,
                    'blob_sha': '',
                    'commit_sha': commit_sha,
                    'status': status
                }
                continue

            # Get the blob SHA for this file at this commit
            try:
                blob_result = subprocess.run(
                    ['git', 'rev-parse', f'{commit_sha}:{path}'],
                    capture_output=True,
                    text=True,
                    check=True
                )
                blob_sha = blob_result.stdout.strip()

                yield {
                    'path': path,
                    'blob_sha': blob_sha,
                    'commit_sha': commit_sha,
                    'status': status
                }
            except subprocess.CalledProcessError:
                # File might not exist at this commit (edge case)
                continue
