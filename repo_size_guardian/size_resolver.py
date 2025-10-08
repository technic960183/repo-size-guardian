"""
Blob size resolution utilities.

Provides functions for retrieving blob sizes from git without checkout.
"""

import subprocess
from typing import Dict, List

from .git_utils import git_cat_file_size
from .models import Blob


def get_blob_size(blob_sha: str) -> int:
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
    return git_cat_file_size(blob_sha)


def get_blob_sizes_batch(blob_shas: List[str]) -> Dict[str, int]:
    """
    Get sizes for multiple blobs efficiently.

    Args:
        blob_shas: List of blob SHA hashes

    Returns:
        Dictionary mapping blob SHA to size in bytes

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    sizes = {}

    # For now, call get_blob_size for each blob
    # TODO: This could be optimized with batch git operations in the future
    for blob_sha in blob_shas:
        if blob_sha and blob_sha.strip():  # Skip empty blob SHAs (e.g., deleted files)
            try:
                sizes[blob_sha] = get_blob_size(blob_sha)
            except (subprocess.CalledProcessError, ValueError):
                # If we can't get size for this blob, skip it
                continue

    return sizes


def augment_blob_objects_with_sizes(blobs: List[Blob]) -> List[Blob]:
    """
    Augment Blob objects with size information.

    Args:
        blobs: List of Blob objects

    Returns:
        List of Blob objects with updated size_bytes field
    """
    # Extract unique blob SHAs (excluding empty ones for deleted files)
    unique_blob_shas = set()
    for blob in blobs:
        if blob.blob_sha and not blob.is_deleted:
            unique_blob_shas.add(blob.blob_sha)

    # Get sizes for all unique blobs
    sizes = get_blob_sizes_batch(list(unique_blob_shas))

    # Update the blob objects
    for blob in blobs:
        if blob.blob_sha and blob.blob_sha in sizes:
            blob.size_bytes = sizes[blob.blob_sha]
        else:
            blob.size_bytes = None  # For deleted files or errors

    return blobs
