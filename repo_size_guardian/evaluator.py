"""
Blob evaluation against policy rules and size thresholds.

Implements the evaluation order and deduplication behavior described in
PRD §3.2 and §3.3: for each candidate blob, ignore/allow lists, policy
rules, disallow lists, and global size thresholds are applied in a fixed,
terminal order to produce a flat list of :class:`~repo_size_guardian.models.Violation`
objects.
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from .models import Blob, Violation
from .rule_engine import (
    Policy,
    Rule,
    find_matching_rule,
    matches_extension,
    matches_mime,
    matches_path,
)


@dataclass
class EvaluationConfig:
    """
    Global evaluation configuration derived from action inputs.

    Attributes:
        max_text_size_kb: Default size limit (KB) for text files. A policy's
            `thresholds.max_text_size_kb`, when not None, overrides this.
            None means no limit.
        max_binary_size_kb: Default size limit (KB) for binary files. A
            policy's `thresholds.max_binary_size_kb`, when not None,
            overrides this. None means no limit.
        dedupe_blobs: When True (the default), evaluate each unique
            `(blob_sha, path)` pair only once, keeping the first occurrence
            encountered in iteration order. When False, every occurrence is
            evaluated and may produce its own violation.
    """
    max_text_size_kb: Optional[float] = None
    max_binary_size_kb: Optional[float] = None
    dedupe_blobs: bool = True


def evaluate_blobs(blobs: Iterable[Blob], policy: Policy,
                   config: EvaluationConfig) -> List[Violation]:
    """
    Evaluate a stream of blobs against a policy, producing violations.

    Per-blob evaluation order (PRD §3.2), each step is terminal for the
    blob it matches (later steps do not also run):

    0. Skip blobs that are deletions (`blob.is_deleted`) or that carry an
       empty `blob_sha` (no content introduced).
    1. `policy.ignore_globs` / `policy.ignore_paths` match -> skip silently.
    2. `policy.allow_globs` match -> skip silently.
    3. The first matching rule (in declaration order) wins. If the rule has
       no `size_over_kb` gate, it always produces a violation. If it has a
       gate, it produces a violation only when the blob's size exceeds it.
       Either way, a matched rule prevents steps 4 and 5 from running.
    4. Disallow lists (extensions, globs, MIME types), checked in that
       order; the first list that matches produces a violation and steps 5
       does not run.
    5. Global thresholds: the effective limit is the policy's threshold if
       set, else the config's threshold, else no limit. Text vs. binary is
       chosen by `blob.is_binary` (`None` is treated as text).

    All size comparisons are strictly greater-than: a blob exactly at a
    limit does not violate it.

    Args:
        blobs: Blobs to evaluate. The caller is expected to provide them
            oldest-commit-first, since deduplication keeps the first
            occurrence encountered and this module does not sort.
        policy: The policy to evaluate against (may be `Policy.empty()`).
        config: Global threshold defaults and the dedupe toggle.

    Returns:
        A list of `Violation` objects in the order their blobs were
        encountered.
    """
    violations: List[Violation] = []
    seen_keys = set()

    for blob in blobs:
        if blob.is_deleted or not blob.blob_sha:
            continue

        if config.dedupe_blobs:
            key = (blob.blob_sha, blob.path)
            if key in seen_keys:
                continue
            seen_keys.add(key)

        violation = _evaluate_single_blob(blob, policy, config)
        if violation is not None:
            violations.append(violation)

    return violations


def has_failing_violations(violations: Sequence[Violation], fail_on: str) -> bool:
    """
    Decide whether a set of violations should fail the job.

    Args:
        violations: The violations produced by `evaluate_blobs`.
        fail_on: The minimum severity that causes failure, `'warn'` or
            `'error'`. With `'warn'`, any violation at all fails the job.
            With `'error'`, only `'error'`-severity violations fail it.

    Returns:
        True if the job should fail.

    Raises:
        ValueError: If `fail_on` is not `'warn'` or `'error'`.
    """
    if fail_on == 'warn':
        return len(violations) > 0
    if fail_on == 'error':
        return any(v.severity == 'error' for v in violations)
    raise ValueError(f"Invalid fail_on value: {fail_on!r}; expected 'warn' or 'error'")


def _evaluate_single_blob(blob: Blob, policy: Policy,
                          config: EvaluationConfig) -> Optional[Violation]:
    """Apply the terminal evaluation order (steps 1-5) to a single blob."""
    ignore_patterns = list(policy.ignore_globs) + list(policy.ignore_paths)
    if matches_path(blob.path, ignore_patterns):
        return None

    if matches_path(blob.path, policy.allow_globs):
        return None

    rule = find_matching_rule(policy, blob)
    if rule is not None:
        # A matched rule is terminal: it either produces a violation or not,
        # but disallow lists and global thresholds never run afterwards.
        return _evaluate_rule_match(blob, rule)

    disallow_violation = _evaluate_disallow_lists(blob, policy)
    if disallow_violation is not None:
        return disallow_violation

    return _evaluate_global_thresholds(blob, policy, config)


def _evaluate_rule_match(blob: Blob, rule: Rule) -> Optional[Violation]:
    """Apply step 3: a matched rule, which is unconditional or size-gated."""
    if rule.size_over_kb is None:
        detail = f": {rule.description}" if rule.description else ""
        return Violation(
            blob=blob,
            rule_name=rule.id,
            message=f"Matched rule '{rule.id}'{detail}",
            severity=rule.action,
            category='rule',
        )

    size_kb = _size_kb(blob)
    if size_kb is not None and size_kb > rule.size_over_kb:
        return Violation(
            blob=blob,
            rule_name=rule.id,
            message=(f"File size {size_kb:.1f} KB exceeds rule '{rule.id}' "
                     f"limit of {rule.size_over_kb:g} KB"),
            severity=rule.action,
            category='rule',
            threshold_kb=rule.size_over_kb,
        )
    return None


def _evaluate_disallow_lists(blob: Blob, policy: Policy) -> Optional[Violation]:
    """Apply step 4: extension, then glob, then MIME disallow lists."""
    if matches_extension(blob.path, policy.disallow_extensions):
        return Violation(
            blob=blob,
            rule_name='disallow.extensions',
            message="File extension is disallowed by policy",
            severity='error',
            category='disallowed',
        )

    if matches_path(blob.path, policy.disallow_globs):
        return Violation(
            blob=blob,
            rule_name='disallow.globs',
            message="Path matches a disallowed glob pattern",
            severity='error',
            category='disallowed',
        )

    if matches_mime(blob.mime_type, policy.disallow_mime_types):
        return Violation(
            blob=blob,
            rule_name='disallow.mime_types',
            message=f"MIME type '{blob.mime_type}' is disallowed by policy",
            severity='error',
            category='disallowed',
        )

    return None


def _evaluate_global_thresholds(blob: Blob, policy: Policy,
                                config: EvaluationConfig) -> Optional[Violation]:
    """Apply step 5: the policy/config threshold for the blob's text/binary kind."""
    is_binary = bool(blob.is_binary) if blob.is_binary is not None else False

    if is_binary:
        limit = policy.max_binary_size_kb if policy.max_binary_size_kb is not None \
            else config.max_binary_size_kb
        rule_name = 'threshold.max_binary_size_kb'
        kind = 'Binary'
    else:
        limit = policy.max_text_size_kb if policy.max_text_size_kb is not None \
            else config.max_text_size_kb
        rule_name = 'threshold.max_text_size_kb'
        kind = 'Text'

    if limit is None:
        return None

    size_kb = _size_kb(blob)
    if size_kb is None or size_kb <= limit:
        return None

    return Violation(
        blob=blob,
        rule_name=rule_name,
        message=f"{kind} file size {size_kb:.1f} KB exceeds {limit:g} KB limit",
        severity='error',
        category='size',
        threshold_kb=limit,
    )


def _size_kb(blob: Blob) -> Optional[float]:
    """Return the blob's size in KB, or None if its size is unknown."""
    if blob.size_bytes is None:
        return None
    return blob.size_bytes / 1024.0
