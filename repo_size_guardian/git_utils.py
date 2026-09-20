"""
Git utilities for repository analysis.

Provides low-level Git operations for accessing blob content, metadata,
commit ranges, and file changes.
"""

import os
import subprocess
from typing import Dict, List

# Tree entry mode of a gitlink (submodule) entry. Its "blob SHA" is a commit
# SHA in the submodule's own repository and does not name any object in this
# repository, so such entries are not blobs and are skipped.
_GITLINK_MODE = '160000'


def git_cat_file_size(blob_sha: str) -> int:
    """
    Get the size of a blob using git cat-file -s.

    Args:
        blob_sha: SHA hash of the blob

    Returns:
        Size of the blob in bytes

    Raises:
        subprocess.CalledProcessError: If git command fails
        ValueError: If blob_sha is empty or invalid
    """
    if not blob_sha or not blob_sha.strip():
        raise ValueError("blob_sha cannot be empty")

    result = subprocess.run(
        ['git', 'cat-file', '-s', blob_sha],
        capture_output=True,
        text=True,
        check=True
    )

    try:
        return int(result.stdout.strip())
    except ValueError as e:
        raise ValueError(f"Invalid size output from git cat-file: {result.stdout}") from e


def git_cat_file_content(blob_sha: str) -> bytes:
    """
    Get the content of a blob using git cat-file -p.

    Args:
        blob_sha: SHA hash of the blob

    Returns:
        Content of the blob as bytes

    Raises:
        subprocess.CalledProcessError: If git command fails
        ValueError: If blob_sha is empty or invalid
    """
    if not blob_sha or not blob_sha.strip():
        raise ValueError("blob_sha cannot be empty")

    result = subprocess.run(
        ['git', 'cat-file', '-p', blob_sha],
        capture_output=True,
        check=True
    )

    return result.stdout


def git_cat_file_exists(blob_sha: str) -> bool:
    """
    Check if a blob exists using git cat-file -e.

    Args:
        blob_sha: SHA hash of the blob

    Returns:
        True if the blob exists, False otherwise

    Raises:
        ValueError: If blob_sha is empty or invalid
    """
    if not blob_sha or not blob_sha.strip():
        raise ValueError("blob_sha cannot be empty")

    result = subprocess.run(
        ['git', 'cat-file', '-e', blob_sha],
        capture_output=True
    )

    return result.returncode == 0


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


def _is_merge_commit(commit_sha: str) -> bool:
    """
    Check whether a commit is a merge (i.e. has a second parent).

    Args:
        commit_sha: Commit to inspect

    Returns:
        True if the commit has two or more parents, False otherwise
    """
    result = subprocess.run(
        ['git', 'rev-parse', '-q', '--verify', f'{commit_sha}^2'],
        capture_output=True,
        text=True
    )
    return result.returncode == 0


def get_diff_files(commit_sha: str) -> List[Dict[str, str]]:
    """
    Get status, path, and post-image blob SHA for the changed files in a
    commit compared with its parent.

    Uses `git diff-tree --raw`, which reports each change's post-image blob
    SHA in the same record as its status, so callers do not need a separate
    `git rev-parse <commit>:<path>` per file to resolve it.

    `--root` is passed so that a parentless (root) commit is reported as a
    creation of every file in it, instead of producing no output at all; the
    flag is a no-op for commits that do have a parent.

    Output is requested with `-z`, so records are NUL-separated and paths are
    printed literally. Without it git would C-quote any path holding
    non-ASCII bytes or special characters (`caf\\303\\251.txt`), and trailing
    whitespace in the final path would be indistinguishable from the
    trailing newline of the output.

    Submodule (gitlink) entries, recognisable by their `160000` post-image
    mode, are skipped: their SHA is a commit in the submodule's own
    repository rather than a blob in this one. An entry whose *pre*-image was
    a gitlink but whose post-image is a regular file (a submodule replaced by
    a file) is a normal blob change and is reported as usual.

    For a merge commit, the diff is taken against the first parent only, so
    the result reflects the changes the merge itself introduces (including
    any conflict resolution), without double-reporting changes from every
    parent. This is done by resolving the first parent and diffing the two
    commits explicitly, rather than with `--diff-merges=first-parent`, so
    that the function works with older Git versions as well.

    Args:
        commit_sha: Commit to inspect

    Returns:
        List of dicts with keys: status, path, blob_sha
        - status: Change status (A=added, M=modified, D=deleted, etc.)
        - path: File path
        - blob_sha: Full 40-character post-image blob SHA. All zeros for
          deleted files (status starting with 'D').

    Raises:
        subprocess.CalledProcessError: If git command fails
        ValueError: If a record of diff-tree output is not in the expected
            raw format (e.g. a rename/copy record with two paths, which this
            function does not request via -M/-C and so does not support)
    """
    diff_flags = ['--no-commit-id', '--raw', '--no-abbrev', '-r', '--root', '-z']

    result = subprocess.run(
        ['git', 'diff-tree'] + diff_flags + [commit_sha],
        capture_output=True,
        check=True
    )
    out = result.stdout

    if not out and _is_merge_commit(commit_sha):
        # A merge has no single implicit parent to diff against, so the
        # single-commit form above prints nothing for it. Resolve the first
        # parent and diff the two commits explicitly instead. This
        # two-tree-ish form of diff-tree works on every Git version, unlike
        # `--diff-merges=first-parent`, which needs Git 2.31+ and makes git
        # fail outright on older versions. The extra processes are only
        # spawned for merges, so ordinary commits still cost one git call.
        first_parent = subprocess.run(
            ['git', 'rev-parse', f'{commit_sha}^1'],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        result = subprocess.run(
            ['git', 'diff-tree'] + diff_flags + [first_parent, commit_sha],
            capture_output=True,
            check=True
        )
        out = result.stdout

    entries: List[Dict[str, str]] = []
    if not out:
        return entries

    # With -z the output is a flat sequence of NUL-terminated fields, in
    # which each change contributes two of them:
    #   ":<old_mode> <new_mode> <old_sha> <new_sha> <status>\0<path>\0"
    # Splitting on NUL therefore yields alternating metadata and path fields,
    # plus one empty trailing field from the final terminator. Only that
    # artifact is dropped: paths themselves are used verbatim, so a filename
    # ending in whitespace keeps it. Paths are decoded the way the OS decodes
    # filenames, since -z prints them as the raw bytes they are on disk.
    fields = [os.fsdecode(field) for field in out.split(b'\0')]
    if fields and fields[-1] == '':
        fields.pop()

    records = iter(fields)
    for meta in records:
        if not meta.startswith(':'):
            raise ValueError(f"Unexpected diff output format: {meta}")

        meta_fields = meta[1:].split(' ')
        if len(meta_fields) != 5:
            raise ValueError(f"Unexpected diff output format: {meta}")
        _old_mode, new_mode, _old_sha, new_sha, status = meta_fields

        path = next(records, None)
        if path is None:
            raise ValueError(f"Unexpected diff output format: {meta}")

        if status.startswith(('R', 'C')):
            # Rename/copy status (e.g. "R100") carries two paths, and so
            # would desynchronise the metadata/path pairing above. This
            # function never requests rename/copy detection, so such a
            # status is unexpected.
            raise ValueError(f"Unexpected diff output format: {meta}")

        if new_mode == _GITLINK_MODE:
            # Submodule entry: the SHA names a commit in another repository,
            # not a blob here, so there is nothing for a blob scan to check.
            continue

        entries.append({'status': status, 'path': path, 'blob_sha': new_sha})
    return entries


def get_blob_sha_at_commit(commit_sha: str, path: str) -> str:
    """
    Resolve blob SHA for a file path at a given commit.

    Args:
        commit_sha: Commit SHA
        path: File path

    Returns:
        The SHA of the blob at the given commit.

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    result = subprocess.run(
        ['git', 'rev-parse', f'{commit_sha}:{path}'],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()
