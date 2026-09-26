# repo-size-guardian

[![CI](https://github.com/technic960183/repo-size-guardian/actions/workflows/ci.yml/badge.svg)](https://github.com/technic960183/repo-size-guardian/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/technic960183/repo-size-guardian/graph/badge.svg)](https://codecov.io/gh/technic960183/repo-size-guardian)

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

**`on: pull_request` is required.** This action only supports `pull_request`
events — it has no base/head to diff on `push` or any other trigger. Running
it on a different event fails fast with exit code 2, naming the event you
used; see [Limitations](#limitations).

## `fetch-depth: 0` is required

**This is the single most likely setup mistake.** repo-size-guardian needs to
compute the merge-base between your PR's base and head, and then walk every
commit in that range. `actions/checkout`'s default (`fetch-depth: 1`) only
fetches the single tip commit, so there's no history to walk or merge-base to
compute.

The action detects this itself and **fails fast with exit code 2** rather
than silently scanning nothing:

```
repo-size-guardian: error: this checkout is a SHALLOW clone, but
repo-size-guardian requires full commit history to compute the merge-base
and walk the commits a PR introduces.

Fix: set `fetch-depth: 0` on your `actions/checkout` step, e.g.:

    - uses: actions/checkout@v4
      with:
        fetch-depth: 0
```

If you see this, add `fetch-depth: 0` to your `actions/checkout` step, as
shown in every example on this page.

**Sanity-check your first run: look at "Commits scanned."** Every report —
the job log, the job summary, and the console output — starts with a line
like `Commits scanned: 3  |  Blobs scanned: 7  |  Unique blobs: 6`.
`Commits scanned` is the number of commits git found between the merge-base
and head; it counts every commit in that range (including ones that changed
no files), so it's an honest measure of how much history was actually
scanned. If your PR has, say, 10 commits but the report says `Commits
scanned: 1` (or another implausibly small number), ref resolution picked the
wrong range — the most common causes are a missing `fetch-depth: 0` or a
workflow trigger that isn't `pull_request`. Catching this on your first run
is worth the extra look: a scan that silently covers only the last commit
still exits 0 on a clean report, which looks identical to a correctly
scoped scan that found nothing wrong.

## Inputs

All inputs are optional. Explicitly setting an input to an empty string
(`""`) is the same as leaving it unset — it falls back to the default below
rather than being passed through and rejected.

| Name | Type | Default | Description |
|---|---|---|---|
| `max_text_size_kb` | number (KB) | *(unset — unlimited)* | Maximum size for text files. Overridden by a policy file's `thresholds.max_text_size_kb` when set. Must be non-negative — a negative value exits with code 2. |
| `max_binary_size_kb` | number (KB) | *(unset — unlimited)* | Maximum size for binary files. Overridden by a policy file's `thresholds.max_binary_size_kb` when set. Must be non-negative — a negative value exits with code 2. |
| `policy_path` | string | `.github/repo-size-guardian.yml` | Path to the optional policy file. See [docs/policy-schema.md](docs/policy-schema.md). |
| `fail_on` | `warn` \| `error` | `error` | Minimum violation severity that fails the job. `error`: only `error`-severity violations fail the job (`warn` ones are still reported). `warn`: any violation fails the job. See [Adopting this in an existing repo](#adopting-this-in-an-existing-repo) for why `warn` is recommended for a first rollout. |
| `scan_mode` | `history` \| `diff` | `history` | `history` walks every commit the PR introduces; `diff` looks only at the net change between the **merge-base** and head. See [squash-merge caveat](#scan_mode-and-squash-merges) below. |
| `dedupe_blobs` | boolean (`"true"`/`"false"`) | `"true"` | Evaluate each unique `(blob SHA, path)` pair once, keeping the earliest commit that introduced it, instead of reporting every occurrence. |
| `annotate_pr` | boolean (`"true"`/`"false"`) | `"true"` | Emit GitHub workflow-command annotations (`::error::`/`::warning::`) for each violation, in addition to the console log and job summary. |
| `max_annotations` | number | `10` | Cap on how many annotations to emit (`0` = unlimited). The job summary always lists everything regardless of this cap. Must be non-negative — a negative value exits with code 2 (it is not read as "unlimited"). See [Annotation limits](#annotation-limits) below — GitHub enforces its own, lower cap regardless of this setting. |
| `base_ref` | string | *(auto-detected)* | Explicit base ref/SHA to diff from, overriding auto-detection from the `pull_request` event. Also the only way to run this action on a non-`pull_request` event — see [Limitations](#limitations). |
| `head_ref` | string | *(auto-detected)* | Explicit head ref/SHA to diff to, overriding auto-detection. |

## Annotation limits

GitHub caps PR annotations to **10 warnings + 10 errors + 10 notices per
workflow step** (see `actions/toolkit`'s own problem-matchers
documentation) — and this action emits every annotation from a single
step, so that per-step cap is the real limit no matter what you set
`max_annotations` to. Past that cap, GitHub silently drops the rest,
reportedly non-deterministically as to which ones. The `::notice::` line
this action prints when the list is truncated (`N more violation(s)
suppressed...`) competes for that same notice budget too.

**The job summary is not subject to this cap and always lists every
violation found** — treat it as the complete, authoritative view, and
the annotations as a convenience for jumping to a violation from the PR
itself.

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

`scan_mode: diff` looks only at the **net** change the PR introduces: it
diffs the **merge-base** (where the PR branched off) against the head, the
same comparison as GitHub's own three-dot "Files changed" view — not the
current base branch tip against head. This matters because the base branch
keeps moving after a PR branches: diffing tip-to-head directly would also
report every file the base branch itself changed in the meantime, which the
PR never touched, and block the PR over somebody else's file. Diffing from
the merge-base avoids that, and is also what a squash-merge will actually
commit as long as the base hasn't advanced further by the time it's merged.

**Decision rule:** merge commits or rebase → `scan_mode: history` (default).
Squash-merge → `scan_mode: diff`.

```yaml
      - uses: technic960183/repo-size-guardian@v1
        with:
          scan_mode: "diff"
```

See [docs/examples.md](docs/examples.md#6-scan-modes-history-vs-diff) for
full workflow examples of both modes.

## Adopting this in an existing repo

**Start with `fail_on: "warn"`** (optionally combined with
`continue-on-error: true` on the step, so the check shows up but can't block
merges at all), and only switch to the default `fail_on: "error"` after a
grace period. See
[docs/examples.md#3](docs/examples.md#3-warn-only-rollout-mode--introducing-the-tool-without-blocking-anyone)
for a full example, including how `fail_on: "warn"` (any violation fails)
differs from giving individual rules `action: warn` (violations are reported
but don't fail the job) — the two are easy to mix up.

Why bother with a rollout period instead of turning on `error` from day one:
an existing repository almost always already has some large or borderline
files in its history and open PRs, so a first run at full strictness is
likely to produce at least one false positive or an unwelcome surprise. A
single bad first impression — a PR blocked by a check nobody asked for,
right when the team is deciding whether to trust it — tends to cost that
trust permanently, and it's very hard to earn back. A quiet warn-only period
lets people see what the tool would have blocked, tune the policy against
real PRs, and only start enforcing once the policy actually fits the repo.

## My PR was blocked — what do I do?

Start with the job log, the PR's annotations, or the job summary — each
violation names the file, its size, and which rule or threshold it hit.

**If you don't see an annotation on the "Files changed" tab, check the job
summary instead.** GitHub only attaches an annotation to that tab when the
annotated file is part of the diff GitHub itself computed for the PR. In
the default `history` scan mode, a very common violation is a file that
was added in one commit and deleted again in a later commit within the
same PR — exactly the kind of file that nets out to no change and so
never appears on "Files changed". Those annotations still show up on the
PR's **Checks** tab and in the job log, but the job summary is the one
place guaranteed to list every violation regardless of this. Then:

**Why deleting the file in a later commit usually does NOT fix it.** In the
default `history` scan mode, the offending blob is already part of a commit
that's reachable in the range the action scans (merge-base..head). Deleting
the file in a *new* commit changes the final tree, but the earlier commit
that introduced the large/disallowed file is still there — the scan will
keep finding it. (The exception: if you're running `scan_mode: diff`, only
the *net* difference between the merge-base and head is scanned, so deleting
the file before the PR is merged genuinely does fix it there.)

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
| `2` | The scan did not complete. Two different situations share this code — see below for how to tell them apart. |

Exit code `2` covers two distinct cases. Both also emit a GitHub `::error::`
annotation (in addition to the plain log line), so either one surfaces in
the Actions UI even if the step's log is collapsed — but the wording tells
you which one you hit:

- **Configuration/usage error** — a shallow clone, a malformed policy file,
  an unresolvable base/head ref (including running on a non-`pull_request`
  event, see [Limitations](#limitations)), or an invalid input value (e.g. a
  negative size threshold). Nothing was scanned. This is on your side: fix
  the workflow, the policy file, or the inputs, and re-run. The message
  looks like `repo-size-guardian: error: <what's wrong>` and never mentions
  a "bug" or the issue tracker — there's nothing to report here.
- **An internal error** — an unexpected crash inside repo-size-guardian
  itself. The log shows a Python traceback followed by a line like
  `repo-size-guardian v1.0.0: internal error (RuntimeError: ...). This is a
  BUG in repo-size-guardian itself, not a policy violation in your PR --
  see the traceback above for details, and please report it (with that
  traceback) at https://github.com/technic960183/repo-size-guardian/issues`.
  The message always names the installed version and links straight to the
  issue tracker, so if you see it, please
  [open an issue](https://github.com/technic960183/repo-size-guardian/issues)
  with the traceback and the version shown.

## Versioning

- `uses: technic960183/repo-size-guardian@v1` tracks the latest `v1.x`
  release — recommended for most users, so you get bug fixes automatically.
- `uses: technic960183/repo-size-guardian@v1.0.0` pins an exact release —
  use this if your CI needs to be fully reproducible and you'd rather
  upgrade explicitly.

## Limitations

- **`pull_request` events only.** repo-size-guardian needs a base and a head
  to diff, so it only supports the `pull_request` event — there is no
  `push`/commit-scanning mode. Trigger your workflow with `on: pull_request`,
  as every example on this page does. Running it on any other event fails
  fast with exit code 2 and a message naming the event you used; the only
  way around this is supplying `base_ref`/`head_ref` explicitly (e.g. for a
  local/CLI invocation, or a non-standard workflow trigger where you compute
  the range yourself).
- **No Git LFS pointer validation.** A file tracked by Git LFS is, from this
  tool's point of view, just its small pointer text file — the actual object
  size on the LFS remote is not checked.
- **No automatic remediation.** The action only detects and reports; it does
  not remove files or rewrite history for you.
- **No SARIF or JSON output.** Results are available as console log lines,
  GitHub PR annotations, and a Markdown job summary only.
- **First-time contributors need a maintainer's approval before the check
  even runs.** On an outside contributor's first pull request to your repo,
  GitHub requires a maintainer to click "Approve and run workflows" before
  any workflow — including this one — executes at all, so the size check
  simply won't appear until that happens. This is a GitHub Actions security
  feature that applies to every action, not a sign that this one is broken.

## Documentation

- [docs/policy-schema.md](docs/policy-schema.md) — full policy file
  reference: every key, the exact evaluation order, and detailed glob/
  extension/MIME matching semantics.
- [docs/examples.md](docs/examples.md) — copy-pasteable workflows and
  policy files, including the recommended warn-only rollout path.
- [docs/development.md](docs/development.md) — local dev setup and running
  the test suite.

## Contributing

Bug reports and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, running the tests, the
formatting requirement enforced by CI, and how to report a bug (including
how to tell a configuration error you need to fix apart from an actual bug
in this tool — see [Exit codes](#exit-codes) above).

## License

[MIT](LICENSE)
