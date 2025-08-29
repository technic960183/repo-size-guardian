"""
Blob size resolution utilities.

Provides functions for retrieving blob sizes from git without checkout.
"""

import subprocess
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class Violation:
    """
    Minimal violation model placeholder.
    
    This is a placeholder for future violation tracking.
    Will be expanded with actual violation logic in later commits.
    """
    path: str
    blob_sha: str
    commit_sha: str
    reason: str
    size_bytes: Optional[int] = None
    
    
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


def get_blob_sizes_batch(blob_shas: list[str]) -> Dict[str, int]:
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
    # This could be optimized with batch git operations in the future
    for blob_sha in blob_shas:
        if blob_sha and blob_sha.strip():  # Skip empty blob SHAs (e.g., deleted files)
            try:
                sizes[blob_sha] = get_blob_size(blob_sha)
            except (subprocess.CalledProcessError, ValueError):
                # If we can't get size for this blob, skip it
                continue
                
    return sizes


def augment_blobs_with_sizes(blobs: list[Dict[str, str]]) -> list[Dict[str, any]]:
    """
    Augment blob records with size information.
    
    Args:
        blobs: List of blob dictionaries from enumerate_changed_blobs
        
    Returns:
        List of blob dictionaries with added 'size_bytes' field
    """
    # Extract unique blob SHAs (excluding empty ones for deleted files)
    unique_blob_shas = set()
    for blob in blobs:
        if blob['blob_sha']:
            unique_blob_shas.add(blob['blob_sha'])
    
    # Get sizes for all unique blobs
    sizes = get_blob_sizes_batch(list(unique_blob_shas))
    
    # Augment the original blob records
    augmented_blobs = []
    for blob in blobs:
        augmented_blob = blob.copy()
        if blob['blob_sha'] and blob['blob_sha'] in sizes:
            augmented_blob['size_bytes'] = sizes[blob['blob_sha']]
        else:
            augmented_blob['size_bytes'] = None  # For deleted files or errors
        augmented_blobs.append(augmented_blob)
    
    return augmented_blobs