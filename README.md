# repo-size-guardian

[![codecov](https://codecov.io/gh/technic960183/repo-size-guardian/graph/badge.svg?token=CODECOV_TOKEN)](https://codecov.io/gh/technic960183/repo-size-guardian)

repo-size-guardian is a GitHub Action that blocks large or disallowed files
from entering your repository's Git history through a pull request. Unlike a
simple diff check, it scans **every commit the PR introduces, not just the
final diff** — because a large file that gets added and then deleted again in
a later commit is still permanently stored in your repo's history, and
purging it after the fact means rewriting history for everyone who has
cloned the repo.

Marketplace categories: Code Quality, Continuous Integration, Utility.

## Quick start

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
          fetch-depth: 0        # required -- see below
      - uses: technic960183/repo-size-guardian@v1
        with:
          max_text_size_kb: 1000
          max_binary_size_kb: 200
```

This fails the PR if it introduces a text file over 1000 KB or a binary file
over 200 KB, anywhere in its commit history. For pattern-based rules
(disallowed extensions, ignored paths, per-rule thresholds), add a policy
file — see [docs/policy-schema.md](docs/policy-schema.md) and
[docs/examples.md](docs/examples.md).

## `fetch-depth: 0` is required

**This is the single most likely setup mistake.** repo-size-guardian needs to
compute the merge-base between your PR's base and head, and then walk every
commit in that range. `actions/checkout`'s default (`fetch-depth: 1`) only
fetches the single tip commit, so there's no history to walk or merge-base to
compute.

The action detects this itself and **fails fast with exit code 2** rather
than silently scanning nothing:

```
repo-size-guardian requires full commit history to compute the merge-base
and walk the commits a PR introduces, but this checkout is a SHALLOW clone.

Fix: set `fetch-depth: 0` on your `actions/checkout` step, e.g.:

    - uses: actions/checkout@v4
      with:
        fetch-depth: 0
```

If you see this, add `fetch-depth: 0` to your `actions/checkout` step, as
shown in every example on this page.

## Inputs

All inputs are optional.

| Name | Type | Default | Description |
|---|---|---|---|
| `max_text_size_kb` | number (KB) | *(unset — unlimited)* | Maximum size for text files. Overridden by a policy file's `thresholds.max_text_size_kb` when set. |
| `max_binary_size_kb` | number (KB) | *(unset — unlimited)* | Maximum size for binary files. Overridden by a policy file's `thresholds.max_binary_size_kb` when set. |
| `policy_path` | string | `.github/repo-size-guardian.yml` | Path to the optional policy file. See [docs/policy-schema.md](docs/policy-schema.md). |
| `fail_on` | `warn` \| `error` | `error` | Minimum violation severity that fails the job. `error`: only `error`-severity violations fail the job (`warn` ones are still reported). `warn`: any violation fails the job. |
| `scan_mode` | `history` \| `diff` | `history` | `history` walks every commit the PR introduces; `diff` looks only at the net change between base and head. See [squash-merge caveat](#scan_mode-and-squash-merges) below. |
| `dedupe_blobs` | boolean (`"true"`/`"false"`) | `"true"` | Evaluate each unique `(blob SHA, path)` pair once, keeping the earliest commit that introduced it, instead of reporting every occurrence. |
| `annotate_pr` | boolean (`"true"`/`"false"`) | `"true"` | Emit GitHub workflow-command annotations (`::error::`/`::warning::`) for each violation, in addition to the console log and job summary. |
| `max_annotations` | number | `50` | Cap on how many annotations to emit (`0` = unlimited). The job summary always lists everything regardless of this cap. |
| `base_ref` | string | *(auto-detected)* | Explicit base ref/SHA to diff from, overriding auto-detection from the `pull_request` event. |
| `head_ref` | string | *(auto-detected)* | Explicit head ref/SHA to diff to, overriding auto-detection. |

## Outputs

| Name | Description |
|---|---|
| `violations_found` | Number of violations found. |
| `summary` | One-line human-readable summary of the scan result. |

## `scan_mode` and squash-merges

`scan_mode: history` (the default) walks every commit between the merge-base
and the PR head. This is correct — and is the entire point of this tool —
for repositories that merge PRs with a real merge commit, or that
rebase-and-fast-forward: in both cases, the PR's individual commits actually
land in the default branch's history, so a large file that was added and
then deleted within the PR is still permanently there.

**If your repository squash-merges PRs**, the intermediate commits are
discarded at merge time and never enter the default branch's history at all.
In that setup, `history` mode can flag a blob that would never actually have
been permanently stored, which is a false positive for your workflow.

**Decision rule:** merge commits or rebase → `scan_mode: history` (default).
Squash-merge → `scan_mode: diff`.

```yaml
      - uses: technic960183/repo-size-guardian@v1
        with:
          scan_mode: "diff"
```

See [docs/examples.md](docs/examples.md#6-scan-modes-history-vs-diff) for
full workflow examples of both modes.

## My PR was blocked — what do I do?

Start with the job log, the PR's annotations, or the job summary — each
violation names the file, its size, and which rule or threshold it hit.
Then:

**Why deleting the file in a later commit usually does NOT fix it.** In the
default `history` scan mode, the offending blob is already part of a commit
that's reachable in the range the action scans (merge-base..head). Deleting
the file in a *new* commit changes the final tree, but the earlier commit
that introduced the large/disallowed file is still there — the scan will
keep finding it. (The exception: if you're running `scan_mode: diff`, only
the *net* difference between base and head is scanned, so deleting the file
before the PR is merged genuinely does fix it there.)

**To actually remove the file from history (`history` mode), you need to
rewrite the commit(s) that introduced it:**

- **Interactive rebase**, to drop or amend the specific offending commit:
  ```bash
  git rebase -i <base-branch>
  # mark the offending commit "drop" (remove it entirely), or "edit" it to
  # amend out the large file, then continue the rebase
  git push --force-with-lease
  ```
- **Squash the branch into one commit**, if you don't need to preserve
  individual commits:
  ```bash
  git reset --soft <base-branch>
  git commit -m "Your squashed commit message"
  git push --force-with-lease
  ```
  Either way, you must **force-push** — a normal push won't rewrite what's
  already on the PR branch upstream.

**If the file is actually supposed to be there** (a legitimate reference
dataset, a golden fixture, something that's simply expected to be large or
of a normally-disallowed type), you don't need to touch history at all —
exempt it in your policy file instead:

```yaml
overrides:
  allow_globs:
    - "path/to/that/exact-file.bin"
```

or, to skip a whole directory from scanning entirely, add it to
`ignore.globs`. See [docs/policy-schema.md](docs/policy-schema.md) and
[docs/examples.md](docs/examples.md#5-exempting-one-known-large-file).

**Which one do you need?**
- Accidental commit, forgotten `.gitignore` entry, generated artifact that
  shouldn't be tracked at all → rewrite history (rebase/squash above).
- Legitimate large/disallowed file that should permanently exist at this
  size or type → add a policy exemption (no history rewrite needed).
- You're rolling this tool out to an existing repo and don't want to block
  anyone yet → see the warn-only rollout example in
  [docs/examples.md](docs/examples.md#3-warn-only-rollout-mode--introducing-the-tool-without-blocking-anyone)
  instead of fixing every PR at once.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean run — no violation at or above the configured `fail_on` severity. |
| `1` | One or more violations at or above `fail_on` were found. |
| `2` | Configuration/usage error — shallow clone, a malformed policy file, an unresolvable base/head ref, or an invalid input value. Nothing was scanned. |

## Versioning

- `uses: technic960183/repo-size-guardian@v1` tracks the latest `v1.x`
  release — recommended for most users, so you get bug fixes automatically.
- `uses: technic960183/repo-size-guardian@v1.0.0` pins an exact release —
  use this if your CI needs to be fully reproducible and you'd rather
  upgrade explicitly.

## Limitations

- **No Git LFS pointer validation.** A file tracked by Git LFS is, from this
  tool's point of view, just its small pointer text file — the actual object
  size on the LFS remote is not checked.
- **No automatic remediation.** The action only detects and reports; it does
  not remove files or rewrite history for you.
- **No SARIF or JSON output.** Results are available as console log lines,
  GitHub PR annotations, and a Markdown job summary only.

## Documentation

- [docs/policy-schema.md](docs/policy-schema.md) — full policy file
  reference: every key, the exact evaluation order, and detailed glob/
  extension/MIME matching semantics.
- [docs/examples.md](docs/examples.md) — copy-pasteable workflows and
  policy files, including the recommended warn-only rollout path.

## License

[MIT](LICENSE)
