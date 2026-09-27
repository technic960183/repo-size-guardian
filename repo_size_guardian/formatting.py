"""
Shared human-readable formatting helpers.

`format_size` is used both when a policy rule's `size` condition is reported
(e.g. "58.6 KB > 50 KB") and throughout the reporting surfaces (the console
report, the job summary table, and GitHub annotations), so a byte count
renders the same way everywhere it appears. `short_sha` gets the same
treatment for commit SHAs, and `format_number` for the numbers in size
limits.
"""

from typing import Optional


def format_size(size_bytes: Optional[int]) -> str:
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


def format_number(value: float) -> str:
    """
    Render a size limit's number the way a person would write it.

    Unlike `{:g}`, never switches to exponent notation, which would both
    lose precision and not be valid size syntax (`1048576` would become
    `1.04858e+06`).

    Args:
        value: The number, e.g. `500.0`, `1.5` or `1048576.0`.

    Returns:
        The number without a trailing `.0`, e.g. `"500"`, `"1.5"` or
        `"1048576"`.
    """
    return '{0:f}'.format(value).rstrip('0').rstrip('.')


def short_sha(sha: Optional[str], length: int = 7) -> str:
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
