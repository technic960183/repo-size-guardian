# Product Requirements Document (PRD)

Project: repo-size-guardian — GitHub Action for PR History File & Size Policy
Goal: Prevent large or unwanted files from entering a repository’s Git history via Pull Requests (PRs), with configurable, generalized rules.

## 1. Overview

repo-size-guardian scans all commits introduced by a PR (not just the final diff) to detect files that are disallowed by policy or exceed size thresholds. It provides actionable feedback and can optionally fail the check so maintainers can stop problematic files from being merged into history (which is costly to rewrite later).

## 2. Main Objectives

- Scan PR history (all commits introduced by the PR) to detect:
  - Files that match disallowed patterns (extensions, globs, MIME types).
  - Binary files larger than a configurable threshold.
  - Text files larger than a configurable threshold.
  - Optional per-pattern thresholds (e.g., stricter limits for specific paths/types).
- Provide clear, actionable reporting (summary logs and optional PR annotations).
- Configurable failure behavior (warn vs. fail).
- Usable in any repository (public, versioned, and documented).
- Tested with unit tests and self-test CI.

## 3. Functional Requirements

### 3.1 Action Inputs (Configurable Parameters)

- max_text_size_kb (number)
  - Default: 500
  - Any detected text file larger than this triggers the configured action (warn/error) unless overridden by the policy file.
- max_binary_size_kb (number)
  - Default: 100
  - Any detected binary file larger than this triggers the configured action (warn/error) unless overridden by the policy file.
- policy_path (string)
  - Default: ".github/repo-size-guardian.yml"
  - Path to an optional policy file defining disallowed/ignored patterns and advanced rules.
- fail_on (string: "warn" | "error")
  - Default: "error"
  - Minimum severity that causes the job to fail. If "error", warnings don’t fail the job.
- scan_mode (string: "history" | "diff")
  - Default: "history"
  - "history": scan all commits introduced by the PR (merge-base..HEAD).
  - "diff": scan only net-new/changed blobs relative to base (optional faster mode).
- dedupe_blobs (boolean)
  - Default: true
  - If true, evaluate each unique blob SHA once (report earliest occurrence).
- annotate_pr (boolean)
  - Default: true
  - If true, use GitHub Action annotations for violations in addition to logs.

Notes:
- Global thresholds (max_text_size_kb, max_binary_size_kb) serve as defaults; per-rule thresholds in the policy file override these.

### 3.2 Optional Policy File (YAML) — Generalized Configuration

When present at policy_path, the file augments/overrides default behavior. It supports both simple and advanced configurations.

Schema (conceptual):

- ignore:
  - globs: [string...]
  - paths: [string...]            # exact or glob-like
- disallow:
  - extensions: [string...]       # e.g., ["ipynb", "exe"]
  - globs: [string...]            # e.g., ["**/*.secret", "data/**/*.parquet"]
  - mime_types: [string...]       # e.g., ["application/x-executable"]
- thresholds:
  - max_text_size_kb: number      # overrides input for all text files
  - max_binary_size_kb: number    # overrides input for all binary files
- rules:
  - - id: string
    description: string
    match:
      globs: [string...]
      extensions: [string...]
      mime_types: [string...]
      binary: true|false           # optional filter
    size_over_kb: number           # optional per-rule threshold
    action: "warn" | "error"
- overrides (optional, advanced):
  - allow_globs: [string...]       # explicit allowlist to exempt matches even if disallowed elsewhere

Evaluation order:
1) If path matches ignore.globs/paths, skip.
2) If path matches overrides.allow_globs, allow (no violation).
3) If a rules[] item matches, apply its action and threshold (if provided).
4) Otherwise, apply simple disallow lists (extensions, globs, mime_types).
5) Apply global thresholds (from policy thresholds if set, else action inputs).

Default policy (if no file is provided):
- No default disallowed extensions or globs.
- Global thresholds come from action inputs.
- This avoids hardcoding a specific file type (e.g., .ipynb) and keeps defaults neutral.

Example policy (for documentation):

```yaml
ignore:
  globs:
    - "docs/**"
    - "**/*.md"

disallow:
  extensions: ["ipynb", "exe"]
  globs:
    - "**/*.secret"
  mime_types:
    - "application/x-dosexec"

thresholds:
  max_text_size_kb: 500
  max_binary_size_kb: 100

rules:
  - id: large-binaries
    description: "Block binaries over 100 KB"
    match:
      binary: true
    size_over_kb: 100
    action: "error"
  - id: large-text-warn
    description: "Warn on text files over 500 KB"
    match:
      binary: false
    size_over_kb: 500
    action: "warn"
```

### 3.3 Detection Rules and Implementation Details

- Binary vs. text detection:
  - Prefer OS "file --mime" where available; fallback to content heuristics (null bytes, encoding).
- File size detection:
  - Use `git cat-file -s <blob>` to retrieve blob size without checkout.
- Commit scope:
  - Use merge-base between PR base branch and PR head; scan all commits reachable from head and not from base.
- Dedupe:
  - When dedupe_blobs=true, evaluate each unique blob once; report the earliest commit where it appears.
- Path/match semantics:
  - Globs use standard minimatch-style patterns. Extensions are compared case-insensitively.

### 3.4 Output Behavior

- Logs:
  - Summary with counts by severity and type (size, disallowed pattern, etc.).
  - For each violation: commit (short SHA), path, reason, size (KB), rule id (if any).
- Annotations (if annotate_pr=true):
  - Add GitHub Action warnings/errors for each violation (capped if needed for performance).
- Exit code:
  - Non-zero if any violation severity ≥ fail_on.

### 3.5 Configuration Examples

Minimal (no policy file, only thresholds):
- max_text_size_kb: 1000
- max_binary_size_kb: 200

With policy file:
- Disallow notebooks and executables.
- Warn on big text, error on big binaries.
- Ignore docs and Markdown.

## 4. Non-Functional Requirements

- Performance:
  - Target up to 500 commits and 10,000 files introduced by a PR.
  - Deduplicate by blob SHA to reduce repeated checks.
- Security:
  - No elevated permissions. No external network calls required.
  - Read-only access to repository contents.
- Portability:
  - Supported on ubuntu-latest GitHub runners.
- Usability:
  - Clear, concise logs; optional annotations; documented examples.
- Reliability:
  - Deterministic results across reruns on the same commit set.

## 5. Repository Structure

```
.
├── action.yml                  # Action metadata (composite; runs Python)
├── repo_size_guardian/         # Python package (single layer; no extra src/)
│   ├── __init__.py
│   ├── main.py                 # Entrypoint
│   ├── git_utils.py            # Git range, blob enumeration
│   ├── size_resolver.py        # Blob size via git cat-file
│   ├── type_detector.py        # Binary/text & MIME detection
│   ├── rule_engine.py          # Policy evaluation
│   ├── evaluator.py            # Thresholds, violations, dedupe
│   └── reporting.py            # Logs and annotations
├── README.md                   # Overview, inputs, examples, policy schema
├── docs/
│   ├── policy-schema.md        # Detailed policy file reference
│   └── examples.md             # Example configs and workflows
├── tests/                      # Built-in unittest test suite
│   ├── fixtures/               # Sample files/configs
│   ├── test_git_utils.py
│   ├── test_size_resolver.py
│   ├── test_type_detector.py
│   ├── test_rule_engine.py
│   ├── test_thresholds.py
│   ├── test_dedupe.py
│   └── test_reporting.py
└── .github/
    └── workflows/
        └── ci.yml              # Lint (optional) & python -m unittest, self-test
```

Notes on dependencies:
- Prefer standard library. Use PyYAML for policy parsing as the only third-party dependency.

## 6. Versioning Strategy

- Semantic versioning (v1.0.0, v1.1.0, etc.).
- Moving tag v1 points to latest stable v1.x.
- Usage:
  - uses: technic960183/repo-size-guardian@v1
  - or pin exact: technic960183/repo-size-guardian@v1.0.0

## 7. Testing Requirements

### 7.1 Unit Tests (unittest)
- Binary vs. text detection heuristics.
- Size checks across a range (small/large).
- Pattern matching (globs, extensions, MIME types).
- Rule precedence and overrides.
- Dedupe behavior across multiple commits.

### 7.2 CI Workflow
- Run built-in unittest on push/PR.
- Self-test job: run the Action against this repo on a synthetic branch with known fixtures to validate outputs (non-blocking on main).

## 8. Marketplace Publishing

- Public Action listed on GitHub Marketplace.
- Metadata: name, description, author, categories (Code Quality, Continuous Integration, Utility).
- README includes:
  - Description and positioning.
  - Inputs and outputs.
  - Policy file schema and examples.
  - Example workflows.
  - Versioning guidance.
  - Contribution guide.

## 9. Example Usage

Workflow (history scan with thresholds only):
```yaml
name: PR File History Scan
on: [pull_request]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: technic960183/repo-size-guardian@v1
        with:
          max_text_size_kb: 1000
          max_binary_size_kb: 200
          fail_on: "error"
          scan_mode: "history"
```

Workflow (with policy file):
```yaml
name: PR File History Scan (Policy)
on: [pull_request]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: technic960183/repo-size-guardian@v1
        with:
          policy_path: ".github/repo-size-guardian.yml"
          annotate_pr: true
          fail_on: "error"
```

Example policy file at .github/repo-size-guardian.yml:
```yaml
ignore:
  globs: ["docs/**", "**/*.md"]

disallow:
  extensions: ["ipynb", "exe"]

thresholds:
  max_text_size_kb: 500
  max_binary_size_kb: 100

rules:
  - id: large-binaries
    match: { binary: true }
    size_over_kb: 100
    action: "error"
  - id: large-text
    match: { binary: false }
    size_over_kb: 500
    action: "warn"
```

## 10. Out of Scope (for initial release)

- Automatic remediation (e.g., removing files or rewriting history).
- LFS pointer validation against remote object sizes.
- SARIF or JSON artifact outputs (may be added later).
- Language-specific ignores/presets (can be provided as sample policies).

## 11. Open Questions

- Cap annotations to avoid rate limits? Default cap (e.g., 50) with overflow summary?