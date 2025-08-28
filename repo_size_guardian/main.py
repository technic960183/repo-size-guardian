"""
Main entry point for repo-size-guardian.
"""

import sys
import argparse
from . import __version__


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="repo-size-guardian: GitHub Action for PR History File & Size Policy"
    )
    parser.add_argument(
        "--version", 
        action="version", 
        version=f"repo-size-guardian {__version__}"
    )
    parser.add_argument(
        "--max-text-size-kb",
        type=int,
        default=500,
        help="Maximum size for text files in KB"
    )
    parser.add_argument(
        "--max-binary-size-kb", 
        type=int,
        default=100,
        help="Maximum size for binary files in KB"
    )
    parser.add_argument(
        "--policy-path",
        default=".github/repo-size-guardian.yml",
        help="Path to policy configuration file"
    )
    parser.add_argument(
        "--fail-on",
        choices=["warn", "error"],
        default="error",
        help="Minimum severity that causes job failure"
    )
    parser.add_argument(
        "--scan-mode",
        choices=["history", "diff"],
        default="history", 
        help="Scan mode"
    )
    parser.add_argument(
        "--dedupe-blobs",
        type=str,
        default="true",
        help="Deduplicate blob evaluation"
    )
    parser.add_argument(
        "--annotate-pr",
        type=str,
        default="true",
        help="Add PR annotations for violations"
    )
    
    args = parser.parse_args()
    
    # No-op for now - just exit successfully
    print(f"repo-size-guardian {__version__} - no-op mode")
    print(f"Config: max_text={args.max_text_size_kb}KB, max_binary={args.max_binary_size_kb}KB")
    print(f"Policy: {args.policy_path}, fail_on: {args.fail_on}, scan_mode: {args.scan_mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())