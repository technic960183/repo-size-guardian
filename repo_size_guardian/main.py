"""
Main entry point for repo-size-guardian.

Wires the pipeline described in PRD §3: resolve the PR's base/head refs,
compute the merge-base, enumerate the blobs introduced in that range (either
by walking every commit, or as a single net diff), augment them with size
and type metadata, evaluate them against the configured policy, and report
the results (console log, GitHub annotations, job summary, step outputs).
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .evaluator import EvaluationConfig, evaluate_blobs, has_failing_violations
from .git_utils import get_merge_base
from .load_branch import enumerate_changed_blobs
from .models import Blob
from .reporting import ReportConfig, ScanStats, report
from .rule_engine import Policy, PolicyError, load_policy
from .size_resolver import augment_blob_objects_with_sizes
from .type_detector import augment_blob_objects_with_types

#: Tree entry mode of a gitlink (submodule) entry -- see git_utils._GITLINK_MODE.
#: Duplicated here (rather than imported) because it is an implementation
#: detail of raw diff parsing, needed locally for `_enumerate_diff_blobs`.
_GITLINK_MODE = '160000'

#: Accepted spellings for CLI/action boolean inputs, which arrive as strings
#: from the composite action (e.g. "true"/"false"), not real booleans.
_TRUE_STRINGS = frozenset({'true', '1', 'yes'})
_FALSE_STRINGS = frozenset({'false', '0', 'no'})

#: Exit code for a clean run (no failing violations).
EXIT_OK = 0
#: Exit code for a run with violations at/above the configured fail_on severity.
EXIT_VIOLATIONS = 1
#: Exit code for a configuration/usage error (bad policy, shallow clone,
#: unresolvable ref, invalid input).
EXIT_CONFIG_ERROR = 2


class ConfigError(Exception):
    """Raised for a usage/configuration problem that should exit with code 2."""


def _parse_bool(value: str, arg_name: str) -> bool:
    """
    Parse a boolean-ish CLI/action string value.

    Action inputs arrive as the literal strings "true"/"false" (composite
    actions have no real boolean type), so relying on Python truthiness of a
    non-empty string would make "false" mean True. This accepts
    true/false/1/0/yes/no, case-insensitively.

    Args:
        value: The raw string value.
        arg_name: Name of the argument, for the error message.

    Returns:
        The parsed boolean.

    Raises:
        ConfigError: If `value` is not a recognized boolean spelling.
    """
    normalized = value.strip().lower()
    if normalized in _TRUE_STRINGS:
        return True
    if normalized in _FALSE_STRINGS:
        return False
    raise ConfigError(
        f"Invalid boolean value for {arg_name}: {value!r} "
        f"(expected one of: true, false, 1, 0, yes, no)"
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="repo-size-guardian: GitHub Action for PR History File & Size Policy"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"repo-size-guardian {__version__}"
    )
    parser.add_argument(
        "--max-text-size-kb",
        type=float,
        default=None,
        help="Maximum size for text files in KB (unlimited if not specified)"
    )
    parser.add_argument(
        "--max-binary-size-kb",
        type=float,
        default=None,
        help="Maximum size for binary files in KB (unlimited if not specified)"
    )
    parser.add_argument(
        "--policy-path",
        default=".github/repo-size-guardian.yml",
        help="Path to policy configuration file"
    )
    parser.add_argument(
        "--fail-on",
        choices=["warn", "error"],
        default="error",
        help="Minimum severity that causes job failure"
    )
    parser.add_argument(
        "--scan-mode",
        choices=["history", "diff"],
        default="history",
        help="Scan mode"
    )
    parser.add_argument(
        "--dedupe-blobs",
        type=str,
        default="true",
        help="Deduplicate blob evaluation (true/false)"
    )
    parser.add_argument(
        "--annotate-pr",
        type=str,
        default="true",
        help="Add PR annotations for violations (true/false)"
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        help="Explicit base ref/SHA to diff from (overrides auto-detection)"
    )
    parser.add_argument(
        "--head-ref",
        default=None,
        help="Explicit head ref/SHA to diff to (overrides auto-detection)"
    )
    parser.add_argument(
        "--max-annotations",
        type=int,
        default=50,
        help="Maximum number of GitHub annotations to emit (0 = unlimited)"
    )
    return parser


# ---------------------------------------------------------------------------
# Ref resolution
# ---------------------------------------------------------------------------

def _read_event_pull_request(event_path: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Read the `pull_request` object from the `$GITHUB_EVENT_PATH` JSON file.

    A `pull_request` event's payload carries `pull_request.base.sha` and
    `pull_request.head.sha`, which are what `actions/checkout` actually put
    in the working tree for that event (it checks out the *merge* commit as
    `HEAD`, so `HEAD` itself is not a usable head ref for a pull_request
    event). A missing env var, missing/unreadable file, malformed JSON, or a
    payload without a `pull_request` object are all treated the same way:
    there is nothing usable here, so the caller falls through to its next
    resolution option instead of failing.

    Args:
        event_path: Value of `$GITHUB_EVENT_PATH`, or None/empty.

    Returns:
        The `pull_request` mapping, or None if unavailable/malformed.
    """
    if not event_path:
        return None
    try:
        with open(event_path, 'r', encoding='utf-8') as event_file:
            data = json.load(event_file)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pull_request = data.get('pull_request')
    if not isinstance(pull_request, dict):
        return None
    return pull_request


def _event_sha(pull_request: Optional[Dict[str, Any]], side: str) -> Optional[str]:
    """Extract `pull_request.<side>.sha`, tolerating a malformed shape."""
    if pull_request is None:
        return None
    side_obj = pull_request.get(side)
    if not isinstance(side_obj, dict):
        return None
    sha = side_obj.get('sha')
    return sha if isinstance(sha, str) and sha else None


def resolve_refs(args: argparse.Namespace) -> Tuple[str, str]:
    """
    Resolve the base and head refs to diff, independently, in priority order.

    For each of base/head, the resolution order is:
      1. The explicit `--base-ref`/`--head-ref` CLI argument.
      2. `$GITHUB_EVENT_PATH`'s `pull_request.base.sha` / `pull_request.head.sha`.
      3. A fixed fallback: `origin/$GITHUB_BASE_REF` for base, `HEAD` for head.

    On a `pull_request` event, `actions/checkout` checks out the *merge*
    commit as `HEAD`, not the PR's head commit -- using the event payload
    SHAs (rather than `HEAD`) is what makes the base/head resolution correct
    for that event. A missing or malformed event file simply falls through
    to the next option rather than raising.

    Args:
        args: Parsed CLI arguments (uses `base_ref`, `head_ref`).

    Returns:
        A `(base_ref, head_ref)` tuple, each a ref name or a SHA.

    Raises:
        ConfigError: If no base ref can be resolved from any source.
    """
    pull_request = _read_event_pull_request(os.environ.get('GITHUB_EVENT_PATH'))

    base_ref = args.base_ref or _event_sha(pull_request, 'base')
    if not base_ref:
        github_base_ref = os.environ.get('GITHUB_BASE_REF')
        if github_base_ref:
            base_ref = f'origin/{github_base_ref}'
    if not base_ref:
        raise ConfigError(
            "Could not resolve a base ref to compare against. Provide "
            "--base-ref explicitly, run this action on a 'pull_request' "
            "event (so $GITHUB_EVENT_PATH carries pull_request.base.sha), "
            "or set $GITHUB_BASE_REF."
        )

    head_ref = args.head_ref or _event_sha(pull_request, 'head') or 'HEAD'

    return base_ref, head_ref


def _resolve_sha(ref: str) -> str:
    """
    Resolve `ref` (a ref name or SHA) to a full commit SHA.

    Args:
        ref: Any git commit-ish.

    Returns:
        The full 40-character commit SHA.

    Raises:
        ConfigError: If `ref` cannot be resolved.
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--verify', f'{ref}^{{commit}}'],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ConfigError(f"Could not resolve ref {ref!r}: {exc.stderr.strip()}") from exc
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Shallow-clone preflight
# ---------------------------------------------------------------------------

def _is_shallow_repository() -> bool:
    """
    Check whether the current repository is a shallow clone.

    Returns:
        True if `git rev-parse --is-shallow-repository` reports `true`.

    Raises:
        ConfigError: If the check itself fails (e.g. not inside a git repo).
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--is-shallow-repository'],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ConfigError(
            f"Could not determine whether this is a shallow clone: {exc.stderr.strip()}"
        ) from exc
    return result.stdout.strip() == 'true'


_SHALLOW_CLONE_MESSAGE = """\
repo-size-guardian requires full commit history to compute the merge-base \
and walk the commits a PR introduces, but this checkout is a SHALLOW clone.

Fix: set `fetch-depth: 0` on your `actions/checkout` step, e.g.:

    - uses: actions/checkout@v4
      with:
        fetch-depth: 0

A shallow checkout (the default `fetch-depth: 1`) only has the tip commit, \
so `git merge-base` and history walks cannot see the PR's actual commits -- \
this would make history scanning silently look at little or nothing.\
"""


# ---------------------------------------------------------------------------
# Diff-mode blob enumeration (a single merge-base..head diff, rather than a
# per-commit history walk). `git_utils.get_diff_files` only diffs a single
# commit against its parent, so it cannot express this directly; this
# mirrors its raw-diff parsing for two explicit refs instead.
# ---------------------------------------------------------------------------

def _enumerate_diff_blobs(base_ref: str, head_ref: str, head_sha: str) -> List[Dict[str, str]]:
    """
    Enumerate the net changed blobs between `base_ref` and `head_ref`.

    Used for `scan_mode='diff'`: a single diff pass over the whole range,
    rather than one diff per commit. Every yielded entry is stamped with
    `head_sha` as its `commit_sha`, per contract (there is no single
    "introducing" commit for a net diff).

    Args:
        base_ref: The diff's base (older side).
        head_ref: The diff's head (newer side).
        head_sha: Full commit SHA to record as `commit_sha` on every entry.

    Returns:
        List of dicts with keys: path, blob_sha, commit_sha, status.
        `blob_sha` is `""` for deletions.

    Raises:
        subprocess.CalledProcessError: If the underlying git command fails.
        ValueError: If the diff output is not in the expected raw format
            (e.g. a rename/copy record, which is not requested here).
    """
    result = subprocess.run(
        ['git', 'diff-tree', '--no-commit-id', '--raw', '--no-abbrev', '-r', '-z',
         base_ref, head_ref],
        capture_output=True,
        check=True,
    )
    out = result.stdout

    fields = [os.fsdecode(field) for field in out.split(b'\0')]
    if fields and fields[-1] == '':
        fields.pop()

    entries: List[Dict[str, str]] = []
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
            raise ValueError(f"Unexpected diff output format: {meta}")

        if new_mode == _GITLINK_MODE:
            continue

        blob_sha = '' if status.startswith('D') else new_sha
        entries.append({
            'path': path,
            'blob_sha': blob_sha,
            'commit_sha': head_sha,
            'status': status,
        })
    return entries


# ---------------------------------------------------------------------------
# Empty-config warning
# ---------------------------------------------------------------------------

def _warn_if_nothing_enforced(policy: Policy, was_found: bool, policy_path: str,
                               args: argparse.Namespace) -> None:
    """
    Emit a prominent warning if the effective configuration enforces nothing.

    This fires when the policy is empty (no ignore/disallow/rules/allow_globs
    and no policy-level thresholds) AND neither `--max-text-size-kb` nor
    `--max-binary-size-kb` was given, i.e. a clean run would be entirely
    meaningless (nothing was ever checked). Never fails the run.

    Args:
        policy: The loaded policy.
        was_found: Whether a policy file was actually found on disk.
        policy_path: The configured policy path, for the message.
        args: Parsed CLI arguments (checked for the two threshold inputs).
    """
    if not policy.is_empty():
        return
    if args.max_text_size_kb is not None or args.max_binary_size_kb is not None:
        return

    if was_found:
        reason = f"the policy file at '{policy_path}' does not configure anything"
    else:
        reason = f"no policy file was found at '{policy_path}'"

    print("=" * 78)
    print("WARNING: repo-size-guardian is not enforcing anything.")
    print(f"({reason}, and no --max-text-size-kb / --max-binary-size-kb was set)")
    print("A passing ('clean') result from this run is meaningless.")
    print("Fix: add thresholds/rules to your policy file, or set the")
    print("     max_text_size_kb / max_binary_size_kb action inputs.")
    print("=" * 78)
    print(
        "::warning::repo-size-guardian: no policy and no size thresholds are "
        "configured, so nothing is being enforced and a passing result means "
        "nothing was checked. Add rules/thresholds to your policy file or set "
        "max_text_size_kb/max_binary_size_kb."
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _collect_blobs(scan_mode: str, merge_base: str, base_ref: str,
                    head_ref: str, head_sha: str) -> Tuple[List[Blob], int]:
    """
    Enumerate and build `Blob` objects for the configured scan mode.

    For `history`, every commit in `merge_base..head_ref` is walked and its
    changed blobs collected, then reordered oldest-commit-first: this
    module's dedupe keeps the *first* occurrence encountered, so blobs must
    be fed in the order they actually entered history for "first occurrence"
    to mean "earliest commit" (`list_commits` -- and therefore
    `enumerate_changed_blobs` -- returns newest-first).

    For `diff`, a single net diff `merge_base..head_ref` is taken instead;
    there is only one pass, so no reordering is needed.

    Args:
        scan_mode: 'history' or 'diff'.
        merge_base: Merge-base commit SHA.
        base_ref: Resolved base ref (used for the diff-mode git call).
        head_ref: Resolved head ref (used for the diff-mode git call and the
            history commit range).
        head_sha: Full head commit SHA (stamped as `commit_sha` in diff mode).

    Returns:
        A `(blobs, commits_scanned)` tuple.
    """
    commit_range = f'{merge_base}..{head_ref}'

    if scan_mode == 'diff':
        raw_entries = _enumerate_diff_blobs(base_ref, head_ref, head_sha)
        commits_scanned = 1
    else:
        # enumerate_changed_blobs walks commits newest-first; reverse so
        # blobs are fed oldest-commit-first (see docstring above).
        raw_entries = list(enumerate_changed_blobs(commit_range))
        raw_entries.reverse()
        commits_scanned = len({entry['commit_sha'] for entry in raw_entries})

    blobs = [Blob.from_dict(entry) for entry in raw_entries]
    return blobs, commits_scanned


def run(args: argparse.Namespace) -> int:
    """
    Execute the scan pipeline for already-parsed arguments.

    Args:
        args: Parsed CLI arguments.

    Returns:
        The process exit code (0 clean, 1 violations, 2 configuration error).
    """
    if _is_shallow_repository():
        print(_SHALLOW_CLONE_MESSAGE, file=sys.stderr)
        return EXIT_CONFIG_ERROR

    dedupe_blobs = _parse_bool(args.dedupe_blobs, '--dedupe-blobs')
    annotate_pr = _parse_bool(args.annotate_pr, '--annotate-pr')

    base_ref, head_ref = resolve_refs(args)
    merge_base = get_merge_base(base_ref, head_ref)
    head_sha = _resolve_sha(head_ref)

    blobs, commits_scanned = _collect_blobs(
        args.scan_mode, merge_base, base_ref, head_ref, head_sha)

    augment_blob_objects_with_sizes(blobs)
    augment_blob_objects_with_types(blobs)

    policy, was_found = load_policy(args.policy_path)
    _warn_if_nothing_enforced(policy, was_found, args.policy_path, args)

    eval_config = EvaluationConfig(
        max_text_size_kb=args.max_text_size_kb,
        max_binary_size_kb=args.max_binary_size_kb,
        dedupe_blobs=dedupe_blobs,
    )
    violations = evaluate_blobs(blobs, policy, eval_config)

    unique_blobs = len({blob.blob_sha for blob in blobs if blob.blob_sha})
    stats = ScanStats(
        commits_scanned=commits_scanned,
        blobs_scanned=len(blobs),
        unique_blobs=unique_blobs,
    )
    report_config = ReportConfig(annotate_pr=annotate_pr, max_annotations=args.max_annotations)
    # Pass sys.stdout explicitly rather than relying on `report`'s default
    # argument (bound once, at reporting.py's import time): callers such as
    # tests that swap sys.stdout at runtime (contextlib.redirect_stdout)
    # would otherwise have their capture silently bypassed.
    report(violations, stats, report_config, stream=sys.stdout)

    return EXIT_VIOLATIONS if has_failing_violations(violations, args.fail_on) else EXIT_OK


def main() -> int:
    """Main CLI entry point."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        return run(args)
    except ConfigError as exc:
        print(f"repo-size-guardian: error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except PolicyError as exc:
        print(f"repo-size-guardian: error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', errors='replace')
        detail = (stderr or str(exc)).strip()
        print(f"repo-size-guardian: error: git command failed: {detail}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValueError as exc:
        print(f"repo-size-guardian: error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
