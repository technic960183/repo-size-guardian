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
import shutil
import subprocess
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .evaluator import EvaluationConfig, evaluate_blobs, has_failing_violations
from .git_utils import get_diff_files_between, get_merge_base, list_commits
from .load_branch import enumerate_changed_blobs
from .models import Blob
from .reporting import ReportConfig, ScanStats, emit_error_annotation, report
from .rule_engine import Policy, PolicyError, load_policy
from .size_resolver import augment_blob_objects_with_sizes
from .type_detector import augment_blob_objects_with_types

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
        raise ConfigError(_no_base_ref_message())

    head_ref = args.head_ref or _event_sha(pull_request, 'head') or 'HEAD'

    return base_ref, head_ref


def _no_base_ref_message() -> str:
    """
    Build the error for when no base ref could be resolved from any source.

    This is deliberately a PR-only tool: it needs `pull_request.base.sha`/
    `pull_request.head.sha` from the event payload (or an explicit
    `base_ref`/`head_ref`) to know what range to scan. The single most
    common way to hit this is wiring the workflow to `on: push` (or leaving
    the default trigger) instead of `on: pull_request` -- in which case
    `$GITHUB_EVENT_NAME` names the actual event, and the message should say
    so explicitly rather than making the user guess from a generic
    "couldn't resolve a ref" failure.

    A local CLI invocation with explicit `--base-ref`/`--head-ref` never
    reaches this function at all (it short-circuits resolution above), so
    this message is only ever seen when auto-detection was expected to work
    and didn't.

    Returns:
        The `ConfigError` message.
    """
    event_name = os.environ.get('GITHUB_EVENT_NAME')
    if event_name and event_name != 'pull_request':
        cause = (
            f"repo-size-guardian only supports the 'pull_request' event, but "
            f"this run was triggered by a {event_name!r} event, which has no "
            "pull request to diff against."
        )
    else:
        cause = (
            "Could not resolve a base ref to compare against: no "
            "'pull_request' event payload was found, and $GITHUB_BASE_REF is "
            "not set."
        )
    return (
        f"{cause} Fix: either set this workflow's trigger to `on: "
        "pull_request`, or supply `base_ref`/`head_ref` explicitly (the "
        "action's `base_ref`/`head_ref` inputs, or `--base-ref`/`--head-ref` "
        "on the CLI)."
    )


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


def _merge_base_or_config_error(base_ref: str, head_ref: str) -> str:
    """
    Compute the merge-base of two refs, or raise an actionable ConfigError.

    `git merge-base` exits non-zero with *no output at all* when the two
    commits share no common ancestor (unrelated histories, or a base
    branch that was force-pushed so its old tip is no longer reachable).
    Letting that surface as a bare `CalledProcessError` would print
    "git command failed: Command '[...]' returned non-zero exit status 1",
    which tells the user nothing about what to do.

    Args:
        base_ref: Base ref (older side).
        head_ref: Head ref (newer side).

    Returns:
        The merge-base commit SHA.

    Raises:
        ConfigError: If the two refs have no merge base, or if either ref
            cannot be resolved.
    """
    try:
        merge_base = get_merge_base(base_ref, head_ref)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):  # pragma: no cover - get_merge_base uses text=True
            stderr = stderr.decode('utf-8', errors='replace')
        detail = (stderr or '').strip()
        raise ConfigError(
            f"Could not compute a merge base between base ref {base_ref!r} and "
            f"head ref {head_ref!r}"
            + (f": {detail}" if detail else " (they share no common ancestor)")
            + ". Check that both refs exist in this checkout and that "
            "`actions/checkout` ran with `fetch-depth: 0`; if the base "
            "branch was force-pushed, re-run the workflow or pass "
            "--base-ref/base_ref explicitly."
        ) from exc
    if not merge_base:
        raise ConfigError(
            f"git merge-base returned nothing for base ref {base_ref!r} and "
            f"head ref {head_ref!r}; they appear to share no common ancestor."
        )
    return merge_base


def _validate_numeric_args(args: argparse.Namespace) -> None:
    """
    Reject negative numeric inputs, which would otherwise degrade silently.

    A negative `--max-annotations` would be read as "unlimited" (the
    0-means-unlimited check is `limit > 0`), and a negative size threshold
    would make every single file a violation. Both are typos, and both are
    far better reported as a configuration error than acted on.

    Args:
        args: Parsed CLI arguments.

    Raises:
        ConfigError: If any numeric input is negative.
    """
    if args.max_annotations < 0:
        raise ConfigError(
            f"--max-annotations must be >= 0 (0 means unlimited), got "
            f"{args.max_annotations}"
        )
    for name, value in (('--max-text-size-kb', args.max_text_size_kb),
                        ('--max-binary-size-kb', args.max_binary_size_kb)):
        if value is not None and value < 0:
            raise ConfigError(f"{name} must be >= 0, got {value:g}")


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
this checkout is a SHALLOW clone, but repo-size-guardian requires full \
commit history to compute the merge-base and walk the commits a PR \
introduces.

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
# per-commit history walk).
# ---------------------------------------------------------------------------

def _enumerate_diff_blobs(merge_base: str, head_ref: str, head_sha: str) -> List[Dict[str, str]]:
    """
    Enumerate the net changed blobs between `merge_base` and `head_ref`.

    Used for `scan_mode='diff'`: a single diff pass over the whole range,
    rather than one diff per commit. Every entry is stamped with `head_sha`
    as its `commit_sha`, per contract (there is no single "introducing"
    commit for a net diff).

    The older side must be the **merge-base**, not the base branch tip: a
    tip-to-head two-tree diff also reports every file the base branch
    changed after the PR branched (they differ between the two trees even
    though the PR never touched them), which would block a PR over
    somebody else's file. This mirrors the three-dot diff GitHub's own
    "Files changed" view uses, and keeps diff mode consistent with history
    mode, which already walks `merge-base..head`.

    Args:
        merge_base: The diff's base (older side); the merge-base commit.
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
    entries: List[Dict[str, str]] = []
    for change in get_diff_files_between(merge_base, head_ref):
        status = change['status']
        entries.append({
            'path': change['path'],
            'blob_sha': '' if status.startswith('D') else change['blob_sha'],
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


def _warn_if_mime_matching_unavailable(policy: Policy) -> None:
    """
    Warn if the policy matches on MIME type but `file` is not installed.

    MIME types only ever come from `file --mime`; the content-heuristic
    fallback reports `mime_type=None`, and `matches_mime(None, ...)` is
    always False. So on a runner without the `file` command, every
    `disallow.mime_types` entry and every `match.mime_types` rule silently
    matches nothing and the scan passes clean -- a false negative that
    looks exactly like a clean PR. Only warn when the policy actually
    relies on MIME matching, to keep the log quiet for everyone else.

    Args:
        policy: The loaded policy.
    """
    uses_mime = bool(policy.disallow_mime_types) or any(
        rule.match_mime_types for rule in policy.rules)
    if not uses_mime or shutil.which('file') is not None:
        return

    print(
        "::warning::repo-size-guardian: your policy matches on MIME types, but "
        "the `file` command is not available on this runner. MIME detection "
        "falls back to content heuristics, which report no MIME type, so every "
        "mime_types entry in your policy will match nothing. Install `file` "
        "(e.g. `apt-get install -y file`) or match on globs/extensions instead."
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _reverse_commit_order(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Reorder change records oldest-commit-first, keeping each commit's own
    file order intact.

    `enumerate_changed_blobs` emits records grouped by commit, newest
    commit first. A flat `list.reverse()` would put the oldest commit
    first but would also reverse the file order *within* every commit,
    which needlessly scrambles the report. This reverses only the groups.

    Args:
        entries: Change records grouped by commit, newest commit first.

    Returns:
        The same records, oldest commit first, intra-commit order preserved.
    """
    groups: List[List[Dict[str, str]]] = []
    current_sha = None
    for entry in entries:
        if not groups or entry['commit_sha'] != current_sha:
            current_sha = entry['commit_sha']
            groups.append([])
        groups[-1].append(entry)
    groups.reverse()
    return [entry for group in groups for entry in group]


def _collect_blobs(scan_mode: str, merge_base: str, head_ref: str,
                    head_sha: str) -> Tuple[List[Blob], int]:
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

    `commits_scanned` is the number of commits in `merge_base..head_ref` in
    both modes. It is counted from `git rev-list` rather than from the
    distinct commit SHAs actually seen in the change records, so that
    commits introducing no blob change (empty commits, submodule-only
    commits, conflict-free merges) still count: this number is the user's
    main sanity check that the action scanned the range they expected, so
    under-reporting it would hide a mis-scoped scan.

    Args:
        scan_mode: 'history' or 'diff'.
        merge_base: Merge-base commit SHA; the older side of both the
            history range and the net diff.
        head_ref: Resolved head ref (used for the diff-mode git call and the
            history commit range).
        head_sha: Full head commit SHA (stamped as `commit_sha` in diff mode).

    Returns:
        A `(blobs, commits_scanned)` tuple.
    """
    commit_range = f'{merge_base}..{head_ref}'
    commits_scanned = len(list_commits(commit_range))

    if scan_mode == 'diff':
        raw_entries = _enumerate_diff_blobs(merge_base, head_ref, head_sha)
    else:
        raw_entries = _reverse_commit_order(list(enumerate_changed_blobs(commit_range)))

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
    _validate_numeric_args(args)
    dedupe_blobs = _parse_bool(args.dedupe_blobs, '--dedupe-blobs')
    annotate_pr = _parse_bool(args.annotate_pr, '--annotate-pr')

    if _is_shallow_repository():
        # Raised (rather than printed here) so this shares the exact same
        # stderr + `::error::` annotation treatment as every other
        # configuration error, from a single place in main().
        raise ConfigError(_SHALLOW_CLONE_MESSAGE)

    base_ref, head_ref = resolve_refs(args)
    merge_base = _merge_base_or_config_error(base_ref, head_ref)
    head_sha = _resolve_sha(head_ref)

    blobs, commits_scanned = _collect_blobs(
        args.scan_mode, merge_base, head_ref, head_sha)

    augment_blob_objects_with_sizes(blobs)
    augment_blob_objects_with_types(blobs)

    policy, was_found = load_policy(args.policy_path)
    _warn_if_nothing_enforced(policy, was_found, args.policy_path, args)
    _warn_if_mime_matching_unavailable(policy)

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


_ISSUE_TRACKER_URL = "https://github.com/technic960183/repo-size-guardian/issues"


def _report_config_error(message: str) -> None:
    """
    Print a configuration/usage-error message to stderr and mirror it as a
    GitHub ``::error::`` annotation on stdout.

    Covers every exit-2 condition that is the *user's* configuration to
    fix: `ConfigError` (a shallow clone, a bad/unresolvable ref, an
    unrelated-history merge-base, an invalid input, ...), `PolicyError`, a
    failed git command, and an invalid value. A misconfiguration is
    currently just as easy to miss inside a (typically collapsed) step's
    plain log as an internal crash, so it gets the same "make it impossible
    to miss" annotation treatment as the internal-error handler in `main()`
    (see `_internal_error_message`). Deliberately does NOT say "bug" or
    point at the issue tracker, though: fixing one of these is on the
    user, not on us.

    Args:
        message: The fully-formatted ``"repo-size-guardian: error: ..."``
            message.
    """
    print(message, file=sys.stderr)
    emit_error_annotation(message, stream=sys.stdout)


def _internal_error_message(exc: BaseException) -> str:
    """
    Build the user-facing message for an unexpected internal crash.

    Includes the installed package version and a direct link to the issue
    tracker so a bug report arrives actionable, and is deliberately
    explicit that this is a bug in repo-size-guardian itself -- not a
    policy violation in the user's PR -- since exit code 2 is shared with
    genuine configuration errors (see `_report_config_error`) that a reader
    must not confuse this with.

    Args:
        exc: The exception that escaped `run()`.

    Returns:
        A single-paragraph message suitable for both stderr and a GitHub
        ``::error::`` annotation.
    """
    return (
        "repo-size-guardian v{0}: internal error ({1}: {2}). This is a BUG in "
        "repo-size-guardian itself, not a policy violation in your PR -- see "
        "the traceback above for details, and please report it (with that "
        "traceback) at {3}".format(__version__, type(exc).__name__, exc, _ISSUE_TRACKER_URL)
    )


def main() -> int:
    """Main CLI entry point."""
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        return run(args)
    except ConfigError as exc:
        _report_config_error(f"repo-size-guardian: error: {exc}")
        return EXIT_CONFIG_ERROR
    except PolicyError as exc:
        _report_config_error(f"repo-size-guardian: error: {exc}")
        return EXIT_CONFIG_ERROR
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', errors='replace')
        detail = (stderr or str(exc)).strip()
        _report_config_error(f"repo-size-guardian: error: git command failed: {detail}")
        return EXIT_CONFIG_ERROR
    except ValueError as exc:
        _report_config_error(f"repo-size-guardian: error: {exc}")
        return EXIT_CONFIG_ERROR
    except Exception as exc:  # pylint: disable=broad-except
        # An unexpected crash must never be reported as exit code 1: that
        # is the "violations found" code, so a workflow (or a human) would
        # read an internal bug as "this PR has a policy violation". Print
        # the full traceback -- this is a bug report, not a user error --
        # and exit with the configuration/usage code instead. The message
        # also goes out as a `::error::` annotation (not just a log line),
        # since this is the one failure mode most worth surfacing loudly:
        # an internal crash the user did nothing to cause.
        traceback.print_exc()
        message = _internal_error_message(exc)
        print(message, file=sys.stderr)
        emit_error_annotation(message, stream=sys.stdout)
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
