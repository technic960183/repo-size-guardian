# Policy guide

A policy file describes what your repository allows: an ordered list of
`rules` in `.github/repo-size-guardian.yml`. This guide builds one step by
step, a few rules at a time.

## 1. Move from the inputs

```yaml
rules:
  - id: max_text_size_kb
    match: { binary: false, size: ">500KB" }
  - id: max_binary_size_kb
    match: { binary: true, size: ">100KB" }
```

These are the limits the `max_text_size_kb: 500` and
`max_binary_size_kb: 100` inputs set, written out as rules. Remove the
inputs from the workflow: a policy file and the inputs can't be used
together.

A `size` condition is one quoted string: an operator (`>`, `>=`, `<`,
`<=`), a number, and a unit (`B`, `KB`, `MB`, `GB`).

## 2. Ban file types

Add to the end of the list:

```yaml
  - id: no-executables
    match: { extensions: [exe, dll, zip] }
```

A rule without a `size` condition matches at any size. Its `action`
defaults to `error`.

## 3. Make an exception

Add to the top of the list:

```yaml
  - id: allow-baseline
    match: { globs: ["data/reference/baseline.zip"] }
    action: stop
```

Rules run top to bottom, and `stop` ends checking for a matching file. This
one `.zip` never reaches `no-executables`. A `stop` rule below
`no-executables` would be too late: the file would already have failed.

## 4. Skip folders

Add below `allow-baseline`:

```yaml
  - id: skip-vendor
    match: { globs: ["vendor/**", "*.min.js"] }
    action: stop
```

Nothing under `vendor/`, and no file named `*.min.js`, reaches the rules
below. See [Globs](reference/policy.md#globs) for the full pattern syntax.

## 5. Warn instead of blocking

Add below `skip-vendor`:

```yaml
  - id: large-csv
    description: CSV files belong in the data bucket
    match: { globs: ["data/**/*.csv"], size: ">2MB" }
    action: warn
  - id: csv-handled
    match: { globs: ["data/**/*.csv"] }
    action: stop
```

`action: warn` reports a file without failing the check, unless the
workflow sets `fail_on: any`. A `warn` or `error` rule doesn't end checking,
so a big CSV would also fail `max_text_size_kb`. `csv-handled` stops every
CSV right after `large-csv`, so a CSV is only ever warned about.

## 6. Catch files a pull request adds and removes again

Add to the end of the list:

```yaml
  - id: added-then-removed
    description: Files that don't survive to the end of this pull request
    match: { transient: true, size: ">200KB" }
    action: warn
  - id: shrunk-in-history
    description: An earlier, larger version of this file stays in history
    match: { transient_version: true, size: ">1MB" }
    action: warn
```

`transient` matches a file that exists at neither end of the pull request:
added in one commit, deleted in a later one. `transient_version` also
matches an earlier version of a file that is still there at the end, such
as a 50 MB CSV later shrunk to 1 KB. Always pair `transient_version` with a
`size` condition: on its own it matches every intermediate edit.

Both need `scan_mode: history`, the default. In `diff` mode only the final
result is scanned, so neither ever matches.

## 7. The whole policy

```yaml
rules:
  - id: allow-baseline
    match: { globs: ["data/reference/baseline.zip"] }
    action: stop

  - id: skip-vendor
    match: { globs: ["vendor/**", "*.min.js"] }
    action: stop

  - id: large-csv
    description: CSV files belong in the data bucket
    match: { globs: ["data/**/*.csv"], size: ">2MB" }
    action: warn

  - id: csv-handled
    match: { globs: ["data/**/*.csv"] }
    action: stop

  - id: max_text_size_kb
    match: { binary: false, size: ">500KB" }

  - id: max_binary_size_kb
    match: { binary: true, size: ">100KB" }

  - id: no-executables
    match: { extensions: [exe, dll, zip] }

  - id: added-then-removed
    description: Files that don't survive to the end of this pull request
    match: { transient: true, size: ">200KB" }
    action: warn

  - id: shrunk-in-history
    description: An earlier, larger version of this file stays in history
    match: { transient_version: true, size: ">1MB" }
    action: warn
```

A file can match several rules before a `stop`. The report lists every
rule it matched, at the highest severity among them. See
[How rules are applied](reference/policy.md#how-rules-are-applied).

## Next

- [Policy reference](reference/policy.md): every key, and exactly how files
  are matched.
- [Workflow reference](reference/workflow.md): every input, including
  `fail_on`.
