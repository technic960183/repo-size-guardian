# repo-size-guardian

[![codecov](https://codecov.io/gh/technic960183/repo-size-guardian/graph/badge.svg?token=CODECOV_TOKEN)](https://codecov.io/gh/technic960183/repo-size-guardian)

A GitHub action to prevent large or unwanted files from entering a repository's Git history via Pull Requests (PRs), with configurable, generalized rules.

## Overview

repo-size-guardian scans all commits introduced by a PR (not just the final diff) to detect files that are disallowed by policy or exceed size thresholds. It provides actionable feedback and can optionally fail the check so maintainers can stop problematic files from being merged into history (which is costly to rewrite later).

## Features

- 🔍 **PR History Scanning**: Analyzes all commits in a PR, not just the final diff
- 📏 **Configurable Size Limits**: Set thresholds for text and binary files
- 🚫 **File Pattern Filtering**: Block specific file types, extensions, or patterns  
- ⚙️ **Flexible Policy Engine**: YAML-based configuration with advanced rules
- 📝 **Clear Reporting**: Detailed logs and optional PR annotations
- 🎯 **Actionable Results**: Configurable warning vs. error behavior

## Quick Start

```yaml
name: PR File History Scan
on:
  pull_request:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: technic960183/repo-size-guardian@v1
        with:
          max_text_size_kb: 500
          max_binary_size_kb: 100
          fail_on: "error"
```

## Documentation

- [Policy Configuration](docs/policy-schema.md) - Detailed policy file reference
- [Examples](docs/examples.md) - Example configurations and workflows

## Development Status

This project is in active development. Current version provides basic scaffolding and CI setup.
