"""
Reporting utilities for scan results.

Renders the report entries produced by the evaluator into the four surfaces
a GitHub Actions run offers: a plain-text console report (job log),
PR-annotation workflow commands, a Markdown job summary, and `GITHUB_OUTPUT`
step outputs. This module owns the entire user-facing experience of the
action; it does not decide *what* is a violation, only how to present one.
"""

import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence, TextIO

from .formatting import format_size, short_sha
from .models import ReportEntry, Violation

#: Table/annotation truncation limit for the Markdown job summary.
_STEP_SUMMARY_MAX_ROWS = 100

#: Maps a Violation.severity value to the GitHub workflow-command keyword.
#: 'warn' (a rule's `action: warn`) maps to GitHub's own 'warning' command
#: name.
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

#: Appended to a report entry's reason when its file version is
#: `transient_version` (see `models.Blob.is_transient_version`): it exists
#: at neither the merge-base nor the head commit, so a reader who only
#: checked out the PR's final state would never find it and might otherwise
#: assume the report is stale.
_TRANSIENT_VERSION_NOTE = (
    "This version of {0} was removed or replaced later in this pull "
    "request, but it stays in the history."
)


@dataclass
class ReportConfig:
    """
    Configuration controlling how a scan's results are reported.

    Attributes:
        annotate_pr: Whether to emit GitHub workflow-command annotations.
        max_annotations: Maximum number of annotations to emit; 0 means
            unlimited. Defaults to 10: GitHub's own `actions/toolkit`
            problem-matchers documentation caps annotations to 10 warnings
            + 10 errors + 10 notices *per step* (50 per job, summed across
            steps) -- and this action emits everything from a single step,
            so 10 errors/10 warnings is the real per-severity budget.
            Anything past that is dropped by GitHub itself, reportedly
            non-deterministically, and the `::notice::` "N more
            suppressed" line this module emits also competes for the
            notice budget. The job summary is not subject to this cap and
            always lists every entry. One annotation is emitted per report
            entry (a file version), not per hit.
        step_summary_path: Path to append the Markdown job summary to.
            Defaults to the `GITHUB_STEP_SUMMARY` environment variable
            (unset/empty outside of GitHub Actions, which disables it).
        github_output_path: Path to append `GITHUB_OUTPUT` step outputs to.
            Defaults to the `GITHUB_OUTPUT` environment variable (unset/empty
            outside of GitHub Actions, which disables it).
    """
    annotate_pr: bool = True
    max_annotations: int = 10
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


def _violation_count(entries: Sequence[ReportEntry]) -> int:
    """Total number of hits across every entry."""
    return sum(len(entry.violations) for entry in entries)


def _violating_file_count(entries: Sequence[ReportEntry]) -> int:
    """Number of distinct paths among the given entries."""
    return len({entry.path for entry in entries})


def _build_summary_text(entries: Sequence[ReportEntry]) -> str:
    """
    Build the one-line summary shared by the console report's totals line,
    the job summary's status line, and the `summary` GITHUB_OUTPUT value.

    Args:
        entries: Report entries found during the scan.

    Returns:
        `"No violations found"`, or
        `"<violation_count> violation(s) in <violating_file_count> file(s)
        (<n> error, <m> warn)"`, omitting a severity whose count is 0.
    """
    if not entries:
        return "No violations found"

    severity_counts = Counter(
        violation.severity for entry in entries for violation in entry.violations)
    counts_text = ", ".join(
        "{0} {1}".format(count, name)
        for name, count in sorted(severity_counts.items()) if count)
    return "{0} violation(s) in {1} file(s) ({2})".format(
        _violation_count(entries), _violating_file_count(entries), counts_text)


def remediation_hint(violation: Violation) -> str:
    """
    Build a short, concrete suggestion for fixing a single hit.

    The hint always states that deleting the file in a later commit does
    not resolve a history-scan hit, since the blob remains reachable from
    the branch's history until that history is rewritten. This is the
    single most confusing behavior of this tool for first-time users.

    Args:
        violation: The hit to explain.

    Returns:
        A short, human-readable remediation suggestion.
    """
    if violation.has_size_condition:
        if violation.is_binary:
            lead = (
                "Large binary file — move it to Git LFS or an external "
                "data/artifact store instead of committing it directly."
            )
        else:
            lead = (
                "Large text file — split it up or compress it, or raise "
                "the size limit in the policy rule or input that flagged it."
            )
    elif violation.is_input_rule:
        lead = (
            "Remove the file, or change the `{0}` input if this match "
            "isn't wanted, or switch to a policy file and add a `stop` "
            "rule above it.".format(violation.rule_name)
        )
    else:
        lead = (
            "Remove the file, or change the policy rule `{0}` if this "
            "match isn't wanted, or add a `stop` rule above `{0}`.".format(
                violation.rule_name)
        )

    return "{0} {1}".format(lead, _HISTORY_NOTE)


def _entry_reason(entry: ReportEntry) -> str:
    """
    Build the reason text shown for one report entry: its hits' messages,
    joined by "; ", the same text used for the console line, the job
    summary's Reason column, and the annotation message alike.

    When the entry's file version is `transient_version` (see
    `models.Blob.is_transient_version`), a note is appended explaining that
    it was removed or replaced later in the pull request and so exists at
    neither the merge-base nor the head commit, but still shows up here
    because it stays reachable from the branch's history.

    Args:
        entry: The report entry.

    Returns:
        The reason text.
    """
    reason = "; ".join(violation.message for violation in entry.violations)
    if entry.blob.is_transient_version:
        reason += " " + _TRANSIENT_VERSION_NOTE.format(entry.path)
    return reason


def _entry_rule_label(entry: ReportEntry) -> str:
    """`"rule: x"`, or `"rules: x, y"` when the entry collected several hits."""
    rule_names = [violation.rule_name for violation in entry.violations]
    if len(rule_names) == 1:
        return "rule: {0}".format(rule_names[0])
    return "rules: {0}".format(", ".join(rule_names))


def format_console_report(entries: Sequence[ReportEntry], stats: ScanStats) -> str:
    """
    Render a plain-text report of a scan for the job log.

    Deliberately avoids ANSI colour codes (GitHub's log viewer mangles
    them) and box-drawing characters, so it stays readable both in the
    Actions UI and in a plain terminal when run locally.

    Args:
        entries: Report entries found during the scan, in report order.
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

    for entry in entries:
        message = _entry_reason(entry)
        lines.append(
            "  {severity:<5}  {sha}  {path}  ({size})  {message}  [{rule_label}]".format(
                severity=entry.severity.upper(),
                sha=short_sha(entry.commit_sha),
                path=entry.path,
                size=format_size(entry.size_bytes),
                message=message,
                rule_label=_entry_rule_label(entry),
            )
        )

    if entries:
        lines.append("")
    lines.append(_build_summary_text(entries))

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


def format_step_summary(entries: Sequence[ReportEntry], stats: ScanStats) -> str:
    """
    Render a GitHub-flavoured Markdown job summary for a scan.

    Args:
        entries: Report entries found during the scan, in report order.
        stats: Aggregate scan counters.

    Returns:
        Markdown text intended to be appended to `GITHUB_STEP_SUMMARY`.
    """
    lines = ["## Repo Size Guardian", ""]

    if not entries:
        lines.append(
            "**Status:** No violations found  \n"
            "Scanned {0} blob(s) across {1} commit(s) ({2} unique).".format(
                stats.blobs_scanned, stats.commits_scanned, stats.unique_blobs)
        )
        lines.append("")
        return "\n".join(lines) + "\n"

    lines.append(
        "**Status:** {0}  \n"
        "Scanned {1} blob(s) across {2} commit(s) ({3} unique).".format(
            _build_summary_text(entries), stats.blobs_scanned,
            stats.commits_scanned, stats.unique_blobs)
    )
    lines.append("")
    lines.append("| Severity | File | Size | Reason | Rules |")
    lines.append("| --- | --- | --- | --- | --- |")

    shown = entries[:_STEP_SUMMARY_MAX_ROWS]
    for entry in shown:
        reason = _entry_reason(entry)
        rules = ", ".join(violation.rule_name for violation in entry.violations)
        lines.append(
            "| {severity} | `{path}` | {size} | {reason} | `{rules}` |".format(
                severity=entry.severity.upper(),
                path=_escape_markdown_cell(entry.path),
                size=format_size(entry.size_bytes),
                reason=_escape_markdown_cell(reason),
                rules=_escape_markdown_cell(rules),
            )
        )

    if len(entries) > _STEP_SUMMARY_MAX_ROWS:
        lines.append("")
        lines.append(
            "_... and {0} more row(s) not shown "
            "(truncated at {1} rows)._".format(
                len(entries) - _STEP_SUMMARY_MAX_ROWS, _STEP_SUMMARY_MAX_ROWS)
        )

    lines.append("")
    lines.append("### How to fix")
    lines.append("")

    seen = set()
    distinct_hints = []
    for entry in entries:
        for violation in entry.violations:
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


def emit_annotations(entries: Sequence[ReportEntry], config: ReportConfig,
                     stream: TextIO = sys.stdout) -> None:
    """
    Emit GitHub workflow-command annotations, one per report entry.

    Annotations are file-level only (no `line=`): a historical blob from an
    older commit cannot be reliably anchored to a line in the PR's diff. An
    entry with several hits gets one annotation whose message lists every
    hit. Emits nothing if `config.annotate_pr` is False. Respects
    `config.max_annotations` (0 means unlimited, and counts entries, not
    hits); when the entry list is truncated, a final `::notice::` line
    reports how many more were suppressed and points at the job summary for
    the full list.

    Args:
        entries: Report entries found during the scan, in report order.
        config: Reporting configuration.
        stream: Stream to write workflow commands to (normally stdout, so
            the GitHub Actions runner picks them up from the job log).
    """
    if not config.annotate_pr:
        return

    total = len(entries)
    limit = config.max_annotations
    truncate = limit > 0 and total > limit
    emitted = entries[:limit] if limit > 0 else entries

    for entry in emitted:
        command = _SEVERITY_TO_ANNOTATION_COMMAND.get(entry.severity, 'error')
        file_value = _escape_annotation_property(entry.path)
        message = _escape_annotation_message(_entry_reason(entry))
        stream.write("::{0} file={1}::{2}\n".format(command, file_value, message))

    if truncate:
        suppressed = total - limit
        stream.write(
            "::notice::{0} more file(s) suppressed from annotations — "
            "see the job summary for the full list\n".format(suppressed)
        )


def emit_error_annotation(message: str, stream: TextIO = sys.stdout) -> None:
    """
    Emit a single ad-hoc GitHub ``::error::`` annotation.

    Unlike `emit_annotations` (one annotation per `ReportEntry`, with a
    `file=` property), this is for conditions that are not a violation at
    all -- a configuration error or an internal crash in main.py -- but
    that still need to surface prominently in the Actions UI rather than as
    a plain log line easy to miss inside a (typically collapsed) step.
    Reuses the same message-escaping as `emit_annotations` so a message
    containing a literal `%`, CR, or LF renders as one intact annotation
    instead of corrupting the workflow-command stream or being cut off at
    the first embedded newline.

    Args:
        message: Human-readable annotation text. May contain newlines.
        stream: Stream to write the workflow command to (normally stdout,
            so the runner picks it up from the job log).
    """
    stream.write("::error::{0}\n".format(_escape_annotation_message(message)))


def emit_warning(message: str, stream: TextIO = sys.stdout) -> None:
    """
    Emit a single ad-hoc GitHub ``::warning::`` annotation.

    Used for anything the scan wants to flag without failing the run: an
    empty configuration, MIME matching that can't work on this runner, or a
    file version whose size could not be read. Reuses the same message-
    escaping as `emit_error_annotation`.

    Args:
        message: Human-readable warning text. May contain newlines.
        stream: Stream to write the workflow command to (normally stdout).
    """
    stream.write("::warning::{0}\n".format(_escape_annotation_message(message)))


def append_to_file(path: str, content: str) -> None:
    """
    Append text to a file, degrading gracefully on failure.

    Used for the `GITHUB_STEP_SUMMARY` and `GITHUB_OUTPUT` files (which
    other steps in the same job may also write to, hence append rather than
    overwrite) as well as the job summary written for a configuration or
    internal error. Never raises: an unwritable path (e.g. a misconfigured
    environment) prints a warning to stderr instead of failing the run.

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


def write_github_output(entries: Sequence[ReportEntry], config: ReportConfig) -> None:
    """
    Append `violating_file_count`, `violation_count` and `summary` to the
    `GITHUB_OUTPUT` file.

    No-ops silently if `config.github_output_path` is None (e.g. running
    locally outside GitHub Actions). Only called after a scan completes, so
    these outputs are never written for a configuration or internal error.

    Args:
        entries: Report entries found during the scan.
        config: Reporting configuration.
    """
    if not config.github_output_path:
        return

    content = "violating_file_count={0}\nviolation_count={1}\nsummary={2}\n".format(
        _violating_file_count(entries), _violation_count(entries), _build_summary_text(entries))
    append_to_file(config.github_output_path, content)


def report(entries: Sequence[ReportEntry], stats: ScanStats, config: ReportConfig,
           stream: TextIO = sys.stdout) -> None:
    """
    Produce the full report for a scan: console output, annotations, the
    job summary, and step outputs.

    Args:
        entries: Report entries found during the scan, in report order.
        stats: Aggregate scan counters.
        config: Reporting configuration.
        stream: Stream for the console report and annotations (normally
            stdout).
    """
    stream.write(format_console_report(entries, stats))
    emit_annotations(entries, config, stream=stream)

    if config.step_summary_path:
        append_to_file(config.step_summary_path, format_step_summary(entries, stats))

    write_github_output(entries, config)
