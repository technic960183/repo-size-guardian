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

A quick smoke test that only exercises argument parsing:
```bash
python -m repo_size_guardian --version
```

This does **not** run a scan — repo-size-guardian needs a base ref and a
head ref to diff, and outside of a real `pull_request` event there's
nothing to auto-detect them from (see
[Limitations](../README.md#limitations)). Passing `--max-text-size-kb` on
its own still exits `2` with a config error, because a ref pair is still
missing. To dry-run an actual scan locally, see the next section.

### Dry-running a real scan locally

On a GitHub Actions run, the base/head refs come from the `pull_request`
event automatically, and the job summary / step output files are provided
by the runner via the `GITHUB_STEP_SUMMARY`/`GITHUB_OUTPUT` environment
variables. Locally there is no event and no runner, so supply both by
hand: pass `--base-ref`/`--head-ref` explicitly, and point
`GITHUB_STEP_SUMMARY`/`GITHUB_OUTPUT` at plain temp files (this is all
`reporting.py` checks — see `ReportConfig` in
[`repo_size_guardian/reporting.py`](../repo_size_guardian/reporting.py)).

The following creates a throwaway repo with two commits — a clean base,
and a head that adds an oversized file — then runs a real scan across
them. Every command below was run as written; the exact commit SHA in
your output will differ, but the rest should match:

```bash
mkdir -p /tmp/rsg-demo && cd /tmp/rsg-demo
git init -q -b main
git config user.email "dev@example.com"
git config user.name "Dev"
echo "# demo" > README.md
git add README.md && git commit -q -m "Initial commit"
BASE_SHA=$(git rev-parse HEAD)

python -c "print('x' * 2000)" > big_notes.txt
git add big_notes.txt && git commit -q -m "Add oversized notes file"
HEAD_SHA=$(git rev-parse HEAD)

export GITHUB_STEP_SUMMARY=$(mktemp)
export GITHUB_OUTPUT=$(mktemp)

python -m repo_size_guardian \
  --base-ref "$BASE_SHA" --head-ref "$HEAD_SHA" \
  --max-text-size-kb 1
```

That scans the one commit between `BASE_SHA` and `HEAD_SHA`, finds
`big_notes.txt` (2.0 KB) over the 1 KB limit we passed, and prints the
console report plus a `::error::` annotation — two of the four output
channels — straight to the terminal:

```
Repo Size Guardian scan report
================================
Commits scanned: 1  |  Blobs scanned: 1  |  Unique blobs: 1

Violations (1):
  ERROR  1336879  big_notes.txt  (2.0 KB)  Text file size 2.0 KB exceeds 1 KB limit  [rule: threshold.max_text_size_kb]

Summary:
  By severity: 1 error
  By category: 1 size
::error file=big_notes.txt::Text file size 2.0 KB exceeds 1 KB limit
```

It exits with code `1` (a violation at or above the default
`fail_on: error` — see [Exit codes](../README.md#exit-codes)). The other
two channels went to the files `GITHUB_STEP_SUMMARY`/`GITHUB_OUTPUT`
point at instead of the console. Inspect them the same way the runner's
own later steps would:

```bash
cat "$GITHUB_STEP_SUMMARY"
```

```markdown
## Repo Size Guardian

**Status:** 1 violation(s) found (1 error)  
Scanned 1 blob(s) across 1 commit(s) (1 unique).

| Severity | File | Size | Reason | Rule |
| --- | --- | --- | --- | --- |
| ERROR | `big_notes.txt` | 2.0 KB | Text file size 2.0 KB exceeds 1 KB limit | `threshold.max_text_size_kb` |

### How to fix

- Large text file — split it up or compress it, or raise `thresholds.max_text_size_kb` in your policy file if this size is legitimate. Deleting the file in a later commit will NOT fix this — the blob is already in this branch's history. Rewrite history instead, e.g. `git rebase -i <base>` to drop/edit the offending commit (or squash and force-push).
```

```bash
cat "$GITHUB_OUTPUT"
```

```
violations_found=1
summary=1 violation(s) found (1 error)
```

That's all four surfaces the action can produce, all exercised without a
GitHub runner: the console report, the `::error::` annotation, the job
summary, and the step output. Clean up when you're done:

```bash
rm -rf /tmp/rsg-demo
```

## Project structure

- `repo_size_guardian/` - Main Python package
- `tests/` - Test suite
- `docs/` - Documentation
- `action.yml` - GitHub Action metadata
- `.github/workflows/ci.yml` - CI/CD pipeline