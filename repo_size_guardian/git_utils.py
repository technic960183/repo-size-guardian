"""
Git utilities for repository analysis.

Provides low-level Git operations for accessing blob content, metadata,
commit ranges, and file changes.
"""

import os
import re
import subprocess
from typing import Dict, List, Sequence

# Tree entry mode of a gitlink (submodule) entry. Its "blob SHA" is a commit
# SHA in the submodule's own repository and does not name any object in this
# repository, so such entries are not blobs and are skipped.
_GITLINK_MODE = '160000'

#: Any whitespace, which a line-oriented `git cat-file --batch*` protocol
#: cannot carry in an object name.
_WHITESPACE_RE = re.compile(r'\s')


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


def git_cat_file_sizes_batch(object_names: Sequence[str]) -> Dict[str, int]:
    """
    Get the sizes of many objects with a single `git cat-file --batch-check`.

    One `git cat-file -s` per object costs one process spawn per object,
    which dominates the runtime of a large PR (PRD 4 targets 10,000 files).
    `--batch-check` reads object names from stdin and writes one result
    line per input line, so the whole set costs a single process.

    Names are fed and results read via `communicate()`, so neither pipe can
    fill and deadlock. Objects git cannot resolve are simply absent from
    the result, matching `get_blob_sizes_batch`'s "skip what we cannot
    measure" behaviour. A name containing whitespace cannot be expressed in
    the line-oriented batch protocol and is resolved individually instead.

    Args:
        object_names: Object names (normally 40-character blob SHAs).

    Returns:
        Dict mapping each resolvable input name to its size in bytes.
        Unresolvable names are omitted.
    """
    unique_names: List[str] = []
    seen = set()
    for name in object_names:
        if not name or not name.strip() or name in seen:
            continue
        seen.add(name)
        unique_names.append(name)

    batchable = [name for name in unique_names if not _WHITESPACE_RE.search(name)]
    awkward = [name for name in unique_names if _WHITESPACE_RE.search(name)]

    sizes: Dict[str, int] = {}

    if batchable:
        result = subprocess.run(
            ['git', 'cat-file', '--batch-check'],
            input='\n'.join(batchable) + '\n',
            capture_output=True,
            text=True,
        )
        lines = result.stdout.splitlines()
        # `--batch-check` emits exactly one line per input line. If that
        # ever fails to hold, positional pairing would mis-attribute every
        # subsequent size, so fall back rather than report wrong numbers.
        if len(lines) == len(batchable):
            for name, line in zip(batchable, lines):
                parts = line.split()
                # "<oid> <type> <size>", or "<name> missing" / "<name> ambiguous".
                if len(parts) != 3:
                    continue
                try:
                    sizes[name] = int(parts[2])
                except ValueError:
                    continue
        else:
            awkward = unique_names

    for name in awkward:
        try:
            sizes[name] = git_cat_file_size(name)
        except (subprocess.CalledProcessError, ValueError):
            continue

    return sizes


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


#: Flags shared by every `git diff-tree` invocation in this module.
#:
#: `--raw` reports each change's post-image blob SHA in the same record as
#: its status, so callers need no separate `git rev-parse <commit>:<path>`.
#: `-z` makes records NUL-separated and prints paths literally: without it
#: git C-quotes any path holding non-ASCII bytes or special characters
#: (`caf\303\251.txt`), and trailing whitespace in the final path would be
#: indistinguishable from the trailing newline of the output.
_DIFF_TREE_FLAGS = ['--no-commit-id', '--raw', '--no-abbrev', '-r', '-z']


def parse_raw_diff_z(out: bytes) -> List[Dict[str, str]]:
    """
    Parse the `-z` raw output of `git diff-tree` into change records.

    This is the single implementation of raw-diff parsing used by every
    caller in this package (both the per-commit diff of `get_diff_files`
    and the two-tree diff of `get_diff_files_between`), so that the
    gitlink, rename, NUL and path-decoding handling below cannot drift
    between them.

    With `-z` the output is a flat sequence of NUL-terminated fields, in
    which each change contributes two of them:
      ":<old_mode> <new_mode> <old_sha> <new_sha> <status>\\0<path>\\0"
    Splitting on NUL therefore yields alternating metadata and path fields,
    plus one empty trailing field from the final terminator. Only that
    artifact is dropped: paths themselves are used verbatim, so a filename
    ending in whitespace keeps it. Paths are decoded the way the OS decodes
    filenames, since -z prints them as the raw bytes they are on disk.

    Submodule (gitlink) entries, recognisable by their `160000` post-image
    mode, are skipped: their SHA is a commit in the submodule's own
    repository rather than a blob in this one. An entry whose *pre*-image
    was a gitlink but whose post-image is a regular file (a submodule
    replaced by a file) is a normal blob change and is reported as usual.

    Args:
        out: Raw bytes written by `git diff-tree ... -z`.

    Returns:
        List of dicts with keys: status, path, blob_sha. `blob_sha` is the
        full 40-character post-image blob SHA, all zeros for deletions.

    Raises:
        ValueError: If a record is not in the expected raw format (e.g. a
            rename/copy record with two paths, which no caller requests via
            -M/-C and so is not supported).
    """
    entries: List[Dict[str, str]] = []
    if not out:
        return entries

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
            # would desynchronise the metadata/path pairing above. Rename
            # /copy detection is never requested, so such a status is
            # unexpected.
            raise ValueError(f"Unexpected diff output format: {meta}")

        if new_mode == _GITLINK_MODE:
            # Submodule entry: the SHA names a commit in another repository,
            # not a blob here, so there is nothing for a blob scan to check.
            continue

        entries.append({'status': status, 'path': path, 'blob_sha': new_sha})
    return entries


def get_diff_files_between(base_ref: str, head_ref: str) -> List[Dict[str, str]]:
    """
    Get the net changed files between two tree-ishes, as one diff.

    Unlike `get_diff_files`, which diffs a single commit against its
    parent, this compares two arbitrary commits directly. Callers wanting
    "what a PR introduces" must pass the *merge-base* as `base_ref`, not
    the base branch tip: a direct tip-to-head diff also reports files the
    base branch changed after the PR branched, which the PR never touched.

    Args:
        base_ref: The diff's base (older side); normally a merge-base.
        head_ref: The diff's head (newer side).

    Returns:
        List of dicts with keys: status, path, blob_sha. See
        `parse_raw_diff_z`.

    Raises:
        subprocess.CalledProcessError: If git command fails
        ValueError: If the output is not in the expected raw format
    """
    result = subprocess.run(
        ['git', 'diff-tree'] + _DIFF_TREE_FLAGS + [base_ref, head_ref],
        capture_output=True,
        check=True
    )
    return parse_raw_diff_z(result.stdout)


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
    diff_flags = _DIFF_TREE_FLAGS + ['--root']

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

    return parse_raw_diff_z(out)


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
