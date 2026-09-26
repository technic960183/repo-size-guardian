# Examples

Copy-pasteable workflows and policy files. Every workflow below includes
`fetch-depth: 0` on the checkout step — see the
[README's "fetch-depth: 0 is required" section](../README.md#fetch-depth-0-is-required)
for why: without it, the action fails fast with exit code 2 rather than
silently scanning the wrong thing.

Every policy YAML on this page was run through
`repo_size_guardian.rule_engine.load_policy` while writing this doc, and
parses without error.

## 1. Minimal setup — thresholds only, no policy file

**Use this when:** you just want a size cap on everything, with zero
configuration files, as a starting point.

```yaml
name: Repo Size Guardian
on:
  pull_request:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: technic960183/repo-size-guardian@v1
        with:
          max_text_size_kb: 1000
          max_binary_size_kb: 200
```

No `policy_path` is given, and no file exists at the default
`.github/repo-size-guardian.yml`, so the action just runs with the two
thresholds above: any text file over 1000 KB or binary file over 200 KB
introduced anywhere in the PR's history fails the check.

## 2. Typical research repo — block notebooks/data, allow big text, ignore docs

**Use this when:** you want to keep large datasets and checkpoints out of
history while leaving room for legitimately large plain-text outputs (logs,
CSVs) and not bothering to scan documentation.

`.github/repo-size-guardian.yml`:

```yaml
ignore:
  globs:
    - "docs/**"
    - "**/*.md"

disallow:
  extensions: ["ipynb", "exe", "parquet", "h5", "hdf5"]
  mime_types:
    - "application/x-dosexec"

thresholds:
  max_text_size_kb: 1000
  max_binary_size_kb: 5000

overrides:
  allow_globs:
    - "data/reference/known-large-dataset.h5"
```

Notebooks (`.ipynb`) and common scientific-data formats are disallowed
outright — commit exported plots/tables instead, or store the data
externally (see the [large-binaries rule](#4-per-rule-thresholds-and-overrides)
if you'd rather allow them under a size gate than ban them entirely).
`docs/**` and any `*.md` file anywhere are never scanned at all. The one
named `known-large-dataset.h5` is explicitly exempted even though `.h5` is
disallowed — see [example 5](#5-exempting-one-known-large-file).

Workflow:

```yaml
name: Repo Size Guardian
on:
  pull_request:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: technic960183/repo-size-guardian@v1
        with:
          policy_path: ".github/repo-size-guardian.yml"
          fail_on: "error"
```

## 3. Warn-only rollout mode — introducing the tool without blocking anyone

**Use this when:** you're adding repo-size-guardian to an existing repo that
already has large files or messy history habits, and you don't want to block
every open PR on day one. **This is the recommended way to adopt the tool.**

```yaml
name: Repo Size Guardian
on:
  pull_request:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: technic960183/repo-size-guardian@v1
        with:
          policy_path: ".github/repo-size-guardian.yml"
          fail_on: "warn"
```

Wait — `fail_on: "warn"` sounds backwards, but it isn't: `fail_on` is the
**minimum severity that fails the job**, so setting it to `"warn"` means
*any* violation (warn or error) fails the check. To get true warn-only
behavior — annotations and job-summary output, but a **green** check — give
every rule `action: warn` in your policy instead, and leave `fail_on` at its
default (`"error"`):

```yaml
# .github/repo-size-guardian.yml
rules:
  - id: large-binaries-warn
    description: "Flag large binaries during rollout (not yet blocking)"
    match:
      binary: true
    size_over_kb: 5000
    action: warn

disallow: {}  # nothing hard-blocked yet
```

```yaml
# workflow
      - uses: technic960183/repo-size-guardian@v1
        with:
          policy_path: ".github/repo-size-guardian.yml"
          fail_on: "error"   # warn-severity violations won't fail the job
```

This surfaces every violation (in the job log, PR annotations, and the job
summary) so the team can see what *would* be blocked, without failing
anyone's PR. **After a grace period** (a couple of weeks is typical), flip
the rules that matter to `action: error`, or drop them into `disallow` /
give them a stricter `size_over_kb`, so the check actually starts blocking.

## 4. Per-rule thresholds and overrides

**Use this when:** a single global threshold is too blunt — you want a hard
limit on binaries, a softer warning on large text, and one specific
already-committed file exempted.

```yaml
thresholds:
  max_text_size_kb: 500
  max_binary_size_kb: 100

rules:
  - id: large-binaries
    description: "Block any binary file over 100 KB"
    match:
      binary: true
    size_over_kb: 100
    action: error
  - id: large-text-warn
    description: "Warn (don't block) on text files over 500 KB"
    match:
      binary: false
    size_over_kb: 500
    action: warn

overrides:
  allow_globs:
    - "data/reference/baseline-results.csv"
```

The two `rules` entries here happen to restate the global thresholds with
explicit severities per binary/text kind — because a **matched** rule is
terminal (see
[policy-schema.md's evaluation order](policy-schema.md#the-surprising-part-a-matched-rule-never-falls-through)),
this makes `large-binaries` always the one deciding binaries' fate, never
falling through to `thresholds.max_binary_size_kb` (which, here, happens to
agree anyway — but if you changed one without the other, the rule wins).
`data/reference/baseline-results.csv` is allowed through regardless of its
size.

## 5. Exempting one known-large file

**Use this when:** one specific file legitimately needs to be large (a
reference dataset, a golden-output fixture) and you don't want to loosen
your policy for anything else.

```yaml
disallow:
  extensions: ["bin"]

overrides:
  allow_globs:
    - "vendor/known-good.bin"
```

`overrides.allow_globs` is checked **before** `disallow` and every `rules`
entry (it's step 2 in the evaluation order, right after `ignore`), so it wins
even though `.bin` is otherwise banned outright. Use an exact path (as above)
rather than a broad glob, so you don't accidentally exempt every `.bin` file
in `vendor/`.

## 6. Scan modes: `history` vs. `diff`

**Use this when:** deciding which `scan_mode` fits how your repository merges
PRs. Both examples below are otherwise identical.

**Decision rule:** if your default branch's history preserves the PR's
individual commits (regular merge commits, or rebase-and-fast-forward), use
`history` (the default) — it's the whole point of this tool, since it catches
a file that was added and later deleted *within the same PR*, which a
plain diff would miss entirely, but which is still permanently sitting in
that commit's tree forever. If your repository **squash-merges** PRs into the
default branch, the PR's intermediate commits are discarded at merge time and
never enter the default branch's history at all — so `history` mode can flag
a blob that would never actually have been permanently stored. Use `diff`
mode there instead: it diffs the **merge-base** (where the PR branched off)
against head — not the current base branch tip against head — which is the
same net change GitHub's own three-dot "Files changed" view shows, and what
squash-merge will actually commit as long as the base hasn't moved further
by merge time. Diffing from the merge-base rather than the base tip also
means a file the base branch changed on its own, after the PR branched, is
never attributed to the PR.

```yaml
# scan_mode: history (default) — for repos using merge commits or rebase
      - uses: technic960183/repo-size-guardian@v1
        with:
          scan_mode: "history"
          max_binary_size_kb: 200
```

```yaml
# scan_mode: diff — for repos that squash-merge PRs
      - uses: technic960183/repo-size-guardian@v1
        with:
          scan_mode: "diff"
          max_binary_size_kb: 200
```

Full workflow (diff mode, squash-merge repo):

```yaml
name: Repo Size Guardian
on:
  pull_request:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: technic960183/repo-size-guardian@v1
        with:
          scan_mode: "diff"
          max_binary_size_kb: 200
```

`fetch-depth: 0` is still required in `diff` mode: the action still needs
the merge-base and full history to resolve refs correctly, even though it
only evaluates the net diff.
