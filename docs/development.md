# Development Setup

This document explains how to set up a development environment for repo-size-guardian.

## Prerequisites

- Python 3.8 or higher
- Git

## Setting up the development environment

1. Clone the repository:
   ```bash
   git clone https://github.com/technic960183/repo-size-guardian.git
   cd repo-size-guardian
   ```

2. Install the package in development mode:
   ```bash
   pip install -e .
   ```

   This installs the package in "editable" mode, so changes to the source code are immediately reflected without needing to reinstall.

3. Verify the installation:
   ```bash
   python -m repo_size_guardian --version
   ```

## Running tests

Run the test suite with:
```bash
python -m unittest discover tests -v
```

## Testing the CLI

Test the no-op CLI functionality:
```bash
python -m repo_size_guardian --version
python -m repo_size_guardian --max-text-size-kb 1000
```

## Project structure

- `repo_size_guardian/` - Main Python package
- `tests/` - Test suite
- `docs/` - Documentation
- `action.yml` - GitHub Action metadata
- `.github/workflows/ci.yml` - CI/CD pipeline