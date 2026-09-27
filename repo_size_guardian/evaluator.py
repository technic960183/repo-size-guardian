"""
File-version evaluation against an ordered list of rules.

For each file version (`Blob`) that survives the pre-evaluation skips
(deletions, no-content entries, and -- when enabled -- the `(blob_sha,
path)` dedupe), every rule is checked top to bottom. A matching `warn` or
`error` rule records a hit and evaluation continues to the next rule; a
matching `stop` rule ends evaluation for that file version, keeping
whatever hits were already recorded. A file version with at least one hit
becomes one `ReportEntry`, holding its hits in rule order.

The rule list itself (the policy's rules, or the quick-start input rules)
is assembled by `main.py`; this module only walks whatever list it is given.
"""

import sys
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Set, TextIO, Tuple

from .formatting import format_number, format_size, short_sha
from .models import Blob, ReportEntry, Violation
from .reporting import emit_warning
from .rule_engine import Rule, extension_of, rule_matches


@dataclass
class EvaluationConfig:
    """
    Evaluation configuration derived from action inputs.

    Attributes:
        dedupe_blobs: When True (the default), evaluate each unique
            `(blob_sha, path)` pair only once, keeping the first occurrence
            encountered in iteration order. When False, every occurrence is
            evaluated and may produce its own report entry.
    """
    dedupe_blobs: bool = True


def evaluate_blobs(blobs: Iterable[Blob], rules: Sequence[Rule], config: EvaluationConfig,
                   stream: TextIO = sys.stdout) -> List[ReportEntry]:
    """
    Evaluate a stream of blobs against an ordered list of rules.

    Args:
        blobs: File versions to evaluate. The caller is expected to provide
            them oldest-commit-first, since deduplication keeps the first
            occurrence encountered and this module does not sort.
        rules: The rules to walk, top to bottom, for every file version --
            the policy's rules, or the quick-start input rules (never both;
            see `main.py`).
        config: The dedupe toggle.
        stream: Stream to emit the "could not read the size" warning to
            (normally stdout, so the GitHub Actions runner picks it up as a
            workflow command).

    Returns:
        A list of `ReportEntry` objects, one per file version with at least
        one hit, in the order their blobs were encountered.
    """
    entries: List[ReportEntry] = []
    seen_keys = set()
    warned_unknown_size: Set[Tuple[str, str]] = set()

    for blob in blobs:
        if blob.is_deleted or not blob.blob_sha:
            continue

        if config.dedupe_blobs:
            key = (blob.blob_sha, blob.path)
            if key in seen_keys:
                continue
            seen_keys.add(key)

        violations = _evaluate_single_blob(blob, rules, warned_unknown_size, stream)
        if violations:
            entries.append(ReportEntry(blob=blob, violations=violations))

    return entries


def has_failing_violations(entries: Sequence[ReportEntry], fail_on: str) -> bool:
    """
    Decide whether a set of report entries should fail the job.

    Args:
        entries: The report entries produced by `evaluate_blobs`.
        fail_on: `'error'` or `'any'`. With `'error'`, only an entry whose
            highest severity is `'error'` fails the job. With `'any'`, any
            entry at all fails it (every entry has at least one hit).

    Returns:
        True if the job should fail.

    Raises:
        ValueError: If `fail_on` is not `'error'` or `'any'`.
    """
    if fail_on == 'any':
        return len(entries) > 0
    if fail_on == 'error':
        return any(entry.severity == 'error' for entry in entries)
    raise ValueError(f"Invalid fail_on value: {fail_on!r}; expected 'error' or 'any'")


def _evaluate_single_blob(blob: Blob, rules: Sequence[Rule],
                          warned_unknown_size: Set[Tuple[str, str]],
                          stream: TextIO) -> List[Violation]:
    """Walk `rules` in order for one blob, per the module docstring."""
    violations: List[Violation] = []
    for rule in rules:
        matched, size_unknown = rule_matches(rule, blob)
        if size_unknown:
            _warn_unknown_size_once(blob, warned_unknown_size, stream)
        if not matched:
            continue
        if rule.action == 'stop':
            break
        violations.append(_build_violation(rule, blob))
    return violations


def _warn_unknown_size_once(blob: Blob, warned: Set[Tuple[str, str]], stream: TextIO) -> None:
    """Emit the "could not read the size" warning at most once per file version."""
    key = (blob.path, blob.blob_sha)
    if key in warned:
        return
    warned.add(key)
    emit_warning(
        "repo-size-guardian: could not read the size of {0} (commit {1}), "
        "so size conditions can't match it. The checkout may be "
        "incomplete.".format(blob.path, short_sha(blob.commit_sha)),
        stream=stream,
    )


def _build_violation(rule: Rule, blob: Blob) -> Violation:
    """Build the hit recorded when `rule` matches `blob`."""
    if rule.is_input_rule:
        message = _input_rule_message(rule, blob)
    else:
        message = _policy_rule_message(rule, blob)
    return Violation(
        rule_name=rule.name,
        message=message,
        severity=rule.action,
        is_input_rule=rule.is_input_rule,
        has_size_condition=rule.match_size is not None,
        is_binary=blob.is_binary,
    )


def _input_rule_message(rule: Rule, blob: Blob) -> str:
    """
    Build the hit message for one of the three quick-start-input rules,
    in today's established style rather than the generic "Matched rule"
    wording used for a policy rule.
    """
    if rule.name == 'disallow_extensions':
        return "File extension '.{0}' is disallowed".format(extension_of(blob.path))

    # match_size is always set for max_text_size_kb/max_binary_size_kb --
    # see main.py's _quick_start_rules -- and rule_matches only reaches
    # here once it has held, so blob.size_bytes is not None.
    kind = 'Text' if rule.name == 'max_text_size_kb' else 'Binary'
    size_kb = blob.size_bytes / 1024.0
    threshold_kb = rule.match_size.threshold_bytes / 1024.0
    return "{0} file size {1:.1f} KB exceeds {2} KB limit".format(
        kind, size_kb, format_number(threshold_kb))


def _policy_rule_message(rule: Rule, blob: Blob) -> str:
    """Build the hit message for a user-defined policy rule."""
    message = rule.description if rule.description else "Matched rule '{0}'".format(rule.name)
    if rule.match_size is not None:
        # rule_matches only reaches a match with match_size set once it has
        # held, so blob.size_bytes is not None here.
        message += " ({0} {1} {2})".format(
            format_size(blob.size_bytes), rule.match_size.operator,
            rule.match_size.render_threshold())
    return message
