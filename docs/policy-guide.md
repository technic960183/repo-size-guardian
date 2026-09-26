# Policy guide

A policy file describes what your repository allows. This guide builds one
step by step in `.github/repo-size-guardian.yml`, adding a few lines at a
time.

## 1. Limit file sizes

```yaml
thresholds:
  max_text_size_kb: 500
  max_binary_size_kb: 100
```

Text and binary files get separate limits. The action tells them apart by
content, not by file name.

## 2. Ban file types

```yaml
disallow:
  extensions: [exe, dll, zip]
```

A file with a banned extension fails at any size. `disallow` also accepts
`globs` for paths and `mime_types` for content types.

## 3. Make an exception

```yaml
overrides:
  allow_globs:
    - data/reference/baseline.zip
```

This file passes every check, even though `.zip` is banned. Name the exact
file, so the exception covers only what you meant.

## 4. Skip folders

```yaml
ignore:
  globs:
    - "vendor/**"
    - "**/*.min.js"
```

Ignored paths are never checked. Globs match the whole path from the
repository root, so `*.min.js` matches only files at the top level;
`**/*.min.js` matches them in every folder. See [Globs](reference/policy.md#globs)
for the full syntax.

## 5. Add rules

Rules give specific files their own limit and severity:

```yaml
rules:
  - id: large-csv
    description: CSV files belong in the data bucket
    match:
      globs: ["data/**/*.csv"]
    size_over_kb: 2000
    action: warn
```

- `match` picks files by `globs`, `extensions`, `mime_types` or `binary`.
- `size_over_kb` reports only files larger than this. Without it, every
  matching file is reported.
- `action: warn` reports the file without failing the check, unless the
  workflow sets `fail_on: warn`.

Rules are checked in order, and the first rule that matches a file decides
it. A 1000 KB CSV under `data/` passes here, even though it's over
`max_text_size_kb`.

## 6. The whole policy

```yaml
thresholds:
  max_text_size_kb: 500
  max_binary_size_kb: 100

disallow:
  extensions: [exe, dll, zip]

overrides:
  allow_globs:
    - data/reference/baseline.zip

ignore:
  globs:
    - "vendor/**"
    - "**/*.min.js"

rules:
  - id: large-csv
    description: CSV files belong in the data bucket
    match:
      globs: ["data/**/*.csv"]
    size_over_kb: 2000
    action: warn
```

The order of the sections doesn't matter. Every file goes through the same
[evaluation order](reference/policy.md#evaluation-order).

## Next

- [Policy reference](reference/policy.md): every key, and exactly how files
  are matched.
- [Workflow reference](reference/workflow.md): every input, including
  `fail_on`.
