"""
Transient-file detection: whether a file version was added and later removed
or replaced again within the same scanned range.

Meaningful only in `scan_mode: history`, which walks every commit a pull
request introduces rather than collapsing them into a single net diff: a
blob a PR adds and then removes again is invisible to a merge-base..head
diff (see `main._enumerate_diff_blobs`), yet it stays reachable from the
branch's history forever -- exactly the case this project exists to catch.
A run computes this once, from the merge-base and head commits' tree
listings, rather than per rule or per file version.
"""

from typing import List

from .git_utils import list_tree_blobs
from .models import Blob


def augment_blob_objects_with_transience(blobs: List[Blob], scan_mode: str,
                                         merge_base: str, head_sha: str) -> List[Blob]:
    """
    Augment Blob objects with `is_transient` / `is_transient_version`.

    In `scan_mode: 'diff'`, both fields are set to `False` on every blob
    without listing any tree at all: diff mode only ever sees the
    merge-base..head diff, so a file version transient within that range
    has already been collapsed out of existence by the time it would reach
    this pass (see `main._enumerate_diff_blobs`), and neither condition can
    ever hold.

    In `scan_mode: 'history'`, the merge-base and head commits' trees are
    each listed once (`git_utils.list_tree_blobs`), and every blob is
    checked against both:
    - `is_transient`: the blob's *path* exists at neither tree.
    - `is_transient_version`: this exact (path, blob_sha) pair exists at
      neither tree -- true whenever `is_transient` is, and also true for a
      path that survives at one end but with *different* content (the file
      was changed again later in the range).

    A deleted file version, or one with no content to look up, is left at
    `None` for both fields (it never reaches evaluation at all -- see
    `evaluator.py`'s skip), matching how `size_resolver`/`type_detector`
    leave their own fields `None` for the same blobs.

    Args:
        blobs: File versions to augment, in any order.
        scan_mode: `'history'` or `'diff'`.
        merge_base: The merge-base commit SHA (the range's older side).
        head_sha: The head commit SHA (the range's newer side).

    Returns:
        The same `blobs` list, for consistency with the other augmentation
        passes (`augment_blob_objects_with_sizes`/`_types`).
    """
    if scan_mode != 'history':
        for blob in blobs:
            blob.is_transient = False
            blob.is_transient_version = False
        return blobs

    merge_base_tree = list_tree_blobs(merge_base)
    head_tree = list_tree_blobs(head_sha)

    for blob in blobs:
        if blob.is_deleted or not blob.blob_sha:
            blob.is_transient = None
            blob.is_transient_version = None
            continue

        path_survives = blob.path in merge_base_tree or blob.path in head_tree
        version_survives = (
            merge_base_tree.get(blob.path) == blob.blob_sha
            or head_tree.get(blob.path) == blob.blob_sha
        )
        blob.is_transient = not path_survives
        blob.is_transient_version = not version_survives

    return blobs
