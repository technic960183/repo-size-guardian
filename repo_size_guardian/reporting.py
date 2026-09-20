"""
Reporting utilities for scan results.

Renders violations found by the evaluator into the four surfaces a GitHub
Actions run offers: a plain-text console report (job log), PR-annotation
workflow commands, a Markdown job summary, and `GITHUB_OUTPUT` step outputs.
This module owns the entire user-facing experience of the action; it does
not decide *what* is a violation, only how to present one.
"""

import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence, TextIO

from .models import Violation

#: Table/annotation truncation limit for the Markdown job summary.
_STEP_SUMMARY_MAX_ROWS = 100

#: Maps a Violation.severity value to the GitHub workflow-command keyword.
#: 'warn' (our vocabulary, matching the `fail_on` input) maps to GitHub's
#: own 'warning' command name.
_SEVERITY_TO_ANNOTATION_COMMAND = {
    'error': 'error',
    'warn': 'warning',
}

#: Reminder appended to every remediation hint. This is the single most
#: confusing thing about a *history* scan for first-time users: the
#: offending blob is already reachable from the branch's history, so
#: deleting the file in a later commit changes the tree but not the
#: history, and the violation will still be reported. Rewriting history
#: (interactive rebase, squash, or filter-repo) is the only real fix.
_HISTORY_NOTE = (
    "Deleting the file in a later commit will NOT fix this — the blob is "
    "already in this branch's history. Rewrite history instead, e.g. "
    "`git rebase -i <base>` to drop/edit the offending commit (or squash "
    "and force-push)."
)


@dataclass
class ReportConfig:
    """
    Configuration controlling how a scan's results are reported.

    Attributes:
        annotate_pr: Whether to emit GitHub workflow-command annotations.
        max_annotations: Maximum number of annotations to emit; 0 means
            unlimited. Defaults to 50 to avoid overwhelming a PR or hitting
            GitHub's own rate limits on log annotations.
        step_summary_path: Path to append the Markdown job summary to.
            Defaults to the `GITHUB_STEP_SUMMARY` environment variable
            (unset/empty outside of GitHub Actions, which disables it).
        github_output_path: Path to append `GITHUB_OUTPUT` step outputs to.
            Defaults to the `GITHUB_OUTPUT` environment variable (unset/empty
            outside of GitHub Actions, which disables it).
    """
    annotate_pr: bool = True
    max_annotations: int = 50
    step_summary_path: Optional[str] = field(
        default_factory=lambda: os.environ.get('GITHUB_STEP_SUMMARY') or None)
    github_output_path: Optional[str] = field(
        default_factory=lambda: os.environ.get('GITHUB_OUTPUT') or None)


@dataclass
class ScanStats:
    """
    Aggregate counters describing the scope of a scan, for reporting.

    Attributes:
        commits_scanned: Number of commits walked.
        blobs_scanned: Number of (blob, path) entries evaluated, including
            duplicates when deduping is disabled.
        unique_blobs: Number of distinct blob SHAs encountered.
    """
    commits_scanned: int = 0
    blobs_scanned: int = 0
    unique_blobs: int = 0


def _format_size(size_bytes: Optional[int]) -> str:
    """
    Render a byte count as a human-readable size string.

    Args:
        size_bytes: Size in bytes, or None if unknown.

    Returns:
        A string like `"512 B"`, `"12.3 KB"`, or `"4.2 MB"`, or
        `"unknown size"` if size_bytes is None.
    """
    if size_bytes is None:
        return "unknown size"

    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return "{0} B".format(int(size))
            return "{0:.1f} {1}".format(size, unit)
        size /= 1024.0
    return "{0:.1f} TB".format(size)  # pragma: no cover - astronomically large


def _short_sha(sha: Optional[str], length: int = 7) -> str:
    """
    Truncate a commit SHA for display, with a placeholder for missing SHAs.

    Args:
        sha: Full commit SHA, or empty/None.
        length: Number of leading characters to keep.

    Returns:
        The shortened SHA, or a run of dashes if sha is empty/None.
    """
    if not sha:
        return "-" * length
    return sha[:length]


def remediation_hint(violation: Violation) -> str:
    """
    Build a short, concrete suggestion for fixing a single violation.

    The hint always states that deleting the file in a later commit does
    not resolve a history-scan violation, since the blob remains reachable
    from the branch's history until that history is rewritten. This is the
    single most confusing behavior of this tool for first-time users.

    Args:
        violation: The violation to explain.

    Returns:
        A short, human-readable remediation suggestion.
    """
    category = violation.category

    if category == 'size':
        if violation.blob.is_binary:
            lead = (
                "Large binary file — move it to Git LFS or an external "
                "data/artifact store instead of committing it directly."
            )
        else:
            lead = (
                "Large text file — split it up or compress it, or raise "
                "`thresholds.max_text_size_kb` in your policy file if this "
                "size is legitimate."
            )
    elif category == 'disallowed':
        lead = (
            "File matches a disallowed pattern — remove it, or if this "
            "match is intentional, add it to `ignore.globs` or "
            "`overrides.allow_globs` in your policy file."
        )
    elif category == 'rule':
        lead = (
            "Flagged by rule `{0}` — adjust or remove that rule in your "
            "policy file if this match isn't wanted.".format(violation.rule_name)
        )
    else:  # pragma: no cover - defensive; category is a closed set in v1
        lead = "Review this file and adjust your policy file if needed."

    return "{0} {1}".format(lead, _HISTORY_NOTE)


def format_console_report(violations: Sequence[Violation], stats: ScanStats) -> str:
    """
    Render a plain-text report of a scan for the job log.

    Deliberately avoids ANSI colour codes (GitHub's log viewer mangles
    them) and box-drawing characters, so it stays readable both in the
    Actions UI and in a plain terminal when run locally.

    Args:
        violations: Violations found during the scan, in report order.
        stats: Aggregate scan counters.

    Returns:
        The full console report as a single string, ending in a newline.
    """
    lines = [
        "Repo Size Guardian scan report",
        "=" * 32,
        "Commits scanned: {0}  |  Blobs scanned: {1}  |  Unique blobs: {2}".format(
            stats.commits_scanned, stats.blobs_scanned, stats.unique_blobs),
        "",
    ]

    if not violations:
        lines.append("No violations found.")
        return "\n".join(lines) + "\n"

    lines.append("Violations ({0}):".format(len(violations)))
    for violation in violations:
        lines.append(
            "  {severity:<5}  {sha}  {path}  ({size})  {message}  [rule: {rule}]".format(
                severity=violation.severity.upper(),
                sha=_short_sha(violation.commit_sha),
                path=violation.path,
                size=_format_size(violation.size_bytes),
                message=violation.message,
                rule=violation.rule_name,
            )
        )

    severity_counts = Counter(violation.severity for violation in violations)
    category_counts = Counter(violation.category for violation in violations)
    severity_summary = ", ".join(
        "{0} {1}".format(count, name) for name, count in sorted(severity_counts.items()))
    category_summary = ", ".join(
        "{0} {1}".format(count, name) for name, count in sorted(category_counts.items()))

    lines.append("")
    lines.append("Summary:")
    lines.append("  By severity: {0}".format(severity_summary))
    lines.append("  By category: {0}".format(category_summary))

    return "\n".join(lines) + "\n"


def _escape_markdown_cell(text: str) -> str:
    """
    Escape text so it can sit inside a single Markdown table cell.

    Args:
        text: Raw text (a path or a message), possibly containing table-
            breaking characters or newlines.

    Returns:
        Text safe to place between `|` delimiters on one table row.
    """
    return (
        text.replace('\\', '\\\\')
        .replace('|', '\\|')
        .replace('`', '\\`')
        .replace('\r\n', ' ')
        .replace('\n', ' ')
        .replace('\r', ' ')
    )


def format_step_summary(violations: Sequence[Violation], stats: ScanStats) -> str:
    """
    Render a GitHub-flavoured Markdown job summary for a scan.

    Args:
        violations: Violations found during the scan, in report order.
        stats: Aggregate scan counters.

    Returns:
        Markdown text intended to be appended to `GITHUB_STEP_SUMMARY`.
    """
    lines = ["## Repo Size Guardian", ""]

    if not violations:
        lines.append(
            "**Status:** No violations found  \n"
            "Scanned {0} blob(s) across {1} commit(s) ({2} unique).".format(
                stats.blobs_scanned, stats.commits_scanned, stats.unique_blobs)
        )
        lines.append("")
        return "\n".join(lines) + "\n"

    severity_counts = Counter(violation.severity for violation in violations)
    severity_summary = ", ".join(
        "{0} {1}".format(count, name) for name, count in sorted(severity_counts.items()))

    lines.append(
        "**Status:** {0} violation(s) found ({1})  \n"
        "Scanned {2} blob(s) across {3} commit(s) ({4} unique).".format(
            len(violations), severity_summary, stats.blobs_scanned,
            stats.commits_scanned, stats.unique_blobs)
    )
    lines.append("")
    lines.append("| Severity | File | Size | Reason | Rule |")
    lines.append("| --- | --- | --- | --- | --- |")

    shown = violations[:_STEP_SUMMARY_MAX_ROWS]
    for violation in shown:
        lines.append(
            "| {severity} | `{path}` | {size} | {message} | `{rule}` |".format(
                severity=violation.severity.upper(),
                path=_escape_markdown_cell(violation.path),
                size=_format_size(violation.size_bytes),
                message=_escape_markdown_cell(violation.message),
                rule=_escape_markdown_cell(violation.rule_name),
            )
        )

    if len(violations) > _STEP_SUMMARY_MAX_ROWS:
        lines.append("")
        lines.append(
            "_... and {0} more violation(s) not shown "
            "(truncated at {1} rows)._".format(
                len(violations) - _STEP_SUMMARY_MAX_ROWS, _STEP_SUMMARY_MAX_ROWS)
        )

    lines.append("")
    lines.append("### How to fix")
    lines.append("")

    seen = set()
    distinct_hints = []
    for violation in violations:
        hint = remediation_hint(violation)
        if hint not in seen:
            seen.add(hint)
            distinct_hints.append(hint)
    for hint in distinct_hints:
        lines.append("- {0}".format(hint))
    lines.append("")

    return "\n".join(lines) + "\n"


def _escape_annotation_message(text: str) -> str:
    """
    Escape text for use as a workflow-command *message* (the part after
    `::`). Percent must be escaped first so the later escapes' own
    percent signs are not themselves re-escaped.

    Args:
        text: Raw text.

    Returns:
        Text safe to place as a workflow-command message.
    """
    return text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def _escape_annotation_property(text: str) -> str:
    """
    Escape text for use as a workflow-command *property value* (e.g. the
    `file=` value), which additionally escapes `:` and `,`.

    Args:
        text: Raw text.

    Returns:
        Text safe to place as a workflow-command property value.
    """
    escaped = _escape_annotation_message(text)
    return escaped.replace(':', '%3A').replace(',', '%2C')


def emit_annotations(violations: Sequence[Violation], config: ReportConfig,
                      stream: TextIO = sys.stdout) -> None:
    """
    Emit GitHub workflow-command annotations, one per violation.

    Annotations are file-level only (no `line=`): a historical blob from an
    older commit cannot be reliably anchored to a line in the PR's diff.
    Emits nothing if `config.annotate_pr` is False. Respects
    `config.max_annotations` (0 means unlimited); when the violation list
    is truncated, a final `::notice::` line reports how many more were
    suppressed and points at the job summary for the full list.

    Args:
        violations: Violations found during the scan, in report order.
        config: Reporting configuration.
        stream: Stream to write workflow commands to (normally stdout, so
            the GitHub Actions runner picks them up from the job log).
    """
    if not config.annotate_pr:
        return

    total = len(violations)
    limit = config.max_annotations
    truncate = limit > 0 and total > limit
    emitted = violations[:limit] if limit > 0 else violations

    for violation in emitted:
        command = _SEVERITY_TO_ANNOTATION_COMMAND.get(violation.severity, 'error')
        file_value = _escape_annotation_property(violation.path)
        message = _escape_annotation_message(violation.message)
        stream.write("::{0} file={1}::{2}\n".format(command, file_value, message))

    if truncate:
        suppressed = total - limit
        stream.write(
            "::notice::{0} more violation(s) suppressed from annotations — "
            "see the job summary for the full list\n".format(suppressed)
        )


def _append_to_file(path: str, content: str) -> None:
    """
    Append text to a file, degrading gracefully on failure.

    Used for both the `GITHUB_STEP_SUMMARY` and `GITHUB_OUTPUT` files,
    which other steps in the same job may also write to, hence append
    rather than overwrite. Never raises: an unwritable path (e.g. a
    misconfigured environment) prints a warning to stderr instead of
    failing the whole scan.

    Args:
        path: File path to append to.
        content: Text to append.
    """
    try:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(content)
    except OSError as exc:
        print(
            "repo_size_guardian: warning: could not write to '{0}': {1}".format(
                path, exc),
            file=sys.stderr,
        )


def _build_output_summary(violations: Sequence[Violation]) -> str:
    """
    Build the single-line summary used for the `GITHUB_OUTPUT` `summary=`.

    Args:
        violations: Violations found during the scan.

    Returns:
        A one-line human-readable summary (no newlines).
    """
    if not violations:
        return "No violations found"

    severity_counts = Counter(violation.severity for violation in violations)
    severity_summary = ", ".join(
        "{0} {1}".format(count, name) for name, count in sorted(severity_counts.items()))
    summary = "{0} violation(s) found ({1})".format(len(violations), severity_summary)
    # Defensive: this text is built entirely from our own counters, but
    # GITHUB_OUTPUT requires a single line, so guarantee it here too.
    return summary.replace('\r', ' ').replace('\n', ' ')


def write_github_output(violations: Sequence[Violation], config: ReportConfig) -> None:
    """
    Append `violations_found` and `summary` to the `GITHUB_OUTPUT` file.

    No-ops silently if `config.github_output_path` is None (e.g. running
    locally outside GitHub Actions). The summary is guaranteed to be a
    single line, so no multiline heredoc delimiter is needed.

    Args:
        violations: Violations found during the scan.
        config: Reporting configuration.
    """
    if not config.github_output_path:
        return

    content = "violations_found={0}\nsummary={1}\n".format(
        len(violations), _build_output_summary(violations))
    _append_to_file(config.github_output_path, content)


def report(violations: Sequence[Violation], stats: ScanStats, config: ReportConfig,
           stream: TextIO = sys.stdout) -> None:
    """
    Produce the full report for a scan: console output, annotations, the
    job summary, and step outputs.

    Args:
        violations: Violations found during the scan, in report order.
        stats: Aggregate scan counters.
        config: Reporting configuration.
        stream: Stream for the console report and annotations (normally
            stdout).
    """
    stream.write(format_console_report(violations, stats))
    emit_annotations(violations, config, stream=stream)

    if config.step_summary_path:
        _append_to_file(config.step_summary_path, format_step_summary(violations, stats))

    write_github_output(violations, config)
