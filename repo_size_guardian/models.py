"""
Data models shared between the git-enumeration, evaluation, and reporting
stages.

`Blob` is one candidate file version: a path plus the content a commit
introduced there. `Violation` is one rule (or quick-start input, which acts
as a rule -- see `main.py`) matching one file version: a "hit" in this
package's vocabulary, called a "violation" in user-facing text. `ReportEntry`
groups every hit a single file version collected into the one row it becomes
in the report.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Blob:
    """
    One file version under consideration: a path plus the blob (content) a
    commit introduced there.

    Carries the per-file-version facts the rule engine matches against
    (`is_binary`, `mime_type`, `size_bytes`, `is_transient`,
    `is_transient_version`), computed once by the `size_resolver`/
    `type_detector`/`transience` augmentation passes before evaluation.

    Attributes:
        is_transient: True when this file version's *path* exists at
            neither the merge-base commit nor the head commit -- it was
            added and removed again somewhere within the scanned range.
            Always a definite `True`/`False` for a non-deleted blob once
            `transience.augment_blob_objects_with_transience` has run;
            `None` beforehand. Always `False` in `scan_mode: diff`, which
            only ever sees the merge-base..head diff, never the
            intermediate history that would make this `True`.
        is_transient_version: True when this exact (path, content) pair --
            this specific blob at this specific path -- exists at neither
            the merge-base nor the head commit, even if some other content
            now lives at the same path. `is_transient` implies
            `is_transient_version`. Same `None`/diff-mode rules as
            `is_transient`.
    """
    path: str
    blob_sha: str
    commit_sha: str
    status: str  # 'A' (add), 'M' (modify), 'D' (delete)
    size_bytes: Optional[int] = None
    is_binary: Optional[bool] = None
    mime_type: Optional[str] = None
    type_confidence: Optional[str] = None
    is_transient: Optional[bool] = None
    is_transient_version: Optional[bool] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Blob':
        """
        Create a Blob instance from a dictionary.

        Args:
            data: Dictionary containing blob data

        Returns:
            Blob instance
        """
        return cls(
            path=data['path'],
            blob_sha=data['blob_sha'],
            commit_sha=data['commit_sha'],
            status=data['status'],
            size_bytes=data.get('size_bytes'),
            is_binary=data.get('is_binary'),
            mime_type=data.get('mime_type'),
            type_confidence=data.get('type_confidence'),
            is_transient=data.get('is_transient'),
            is_transient_version=data.get('is_transient_version')
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the Blob to a dictionary.

        Returns:
            Dictionary representation of the blob
        """
        return {
            'path': self.path,
            'blob_sha': self.blob_sha,
            'commit_sha': self.commit_sha,
            'status': self.status,
            'size_bytes': self.size_bytes,
            'is_binary': self.is_binary,
            'mime_type': self.mime_type,
            'type_confidence': self.type_confidence,
            'is_transient': self.is_transient,
            'is_transient_version': self.is_transient_version
        }

    @property
    def is_deleted(self) -> bool:
        """Check if this blob represents a deleted file."""
        return self.status == 'D'

    @property
    def is_added(self) -> bool:
        """Check if this blob represents an added file."""
        return self.status == 'A'

    @property
    def is_modified(self) -> bool:
        """Check if this blob represents a modified file."""
        return self.status == 'M'


@dataclass
class Violation:
    """
    One rule matching one file version -- a "hit".

    Attributes:
        rule_name: The matched rule's report name (its `id`, `rules[N]`, or
            a quick-start input name such as `max_text_size_kb`).
        message: The human-readable reason, ready to show as-is.
        severity: `'warn'` or `'error'`.
        is_input_rule: True when `rule_name` names a quick-start input
            rather than a policy rule; only affects remediation wording.
        has_size_condition: True when the matched rule had a `size`
            condition, so a report can group it with the size-specific "how
            to fix" hint.
        is_binary: The file version's `Blob.is_binary` at match time, kept
            here so a size-related hint can be phrased for binary vs. text
            without needing the `Blob` back.
    """
    rule_name: str
    message: str
    severity: str  # 'warn' | 'error'
    is_input_rule: bool = False
    has_size_condition: bool = False
    is_binary: Optional[bool] = None


@dataclass
class ReportEntry:
    """
    One file version that collected at least one `Violation`.

    Attributes:
        blob: The file version.
        violations: Its hits, in the order the rules that produced them
            were declared.
    """
    blob: Blob
    violations: List[Violation] = field(default_factory=list)

    @property
    def path(self) -> str:
        """The file version's path."""
        return self.blob.path

    @property
    def commit_sha(self) -> str:
        """The commit that introduced this file version."""
        return self.blob.commit_sha

    @property
    def size_bytes(self) -> Optional[int]:
        """The file version's size, or None if unknown."""
        return self.blob.size_bytes

    @property
    def severity(self) -> str:
        """The highest severity among this entry's violations ('error' > 'warn')."""
        return 'error' if any(v.severity == 'error' for v in self.violations) else 'warn'
