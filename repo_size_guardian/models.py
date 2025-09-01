"""
Data models for repository analysis.

Provides shared data structures for blobs, violations, and other entities.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Blob:
    """
    Represents a git blob with metadata.

    This class encapsulates all information about a git blob including
    its location, content properties, and change status.
    """
    path: str
    blob_sha: str
    commit_sha: str
    status: str  # 'A' (add), 'M' (modify), 'D' (delete)
    size_bytes: Optional[int] = None
    is_binary: Optional[bool] = None
    mime_type: Optional[str] = None
    type_confidence: Optional[str] = None

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
            type_confidence=data.get('type_confidence')
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
            'type_confidence': self.type_confidence
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
    Represents a policy violation found in repository analysis.

    This class represents any violation of repository policies such as
    file size limits, forbidden file types, or other constraints.
    """
    blob: Blob
    rule_name: str
    message: str
    severity: str = 'error'  # 'error', 'warning', 'info'

    @property
    def path(self) -> str:
        """Get the file path for this violation."""
        return self.blob.path

    @property
    def blob_sha(self) -> str:
        """Get the blob SHA for this violation."""
        return self.blob.blob_sha

    @property
    def commit_sha(self) -> str:
        """Get the commit SHA for this violation."""
        return self.blob.commit_sha

    @property
    def size_bytes(self) -> Optional[int]:
        """Get the file size for this violation."""
        return self.blob.size_bytes
