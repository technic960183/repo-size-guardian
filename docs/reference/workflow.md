# Workflow reference

Everything that goes in the workflow file.

## Inputs

All inputs are optional. An input set to an empty string uses its default.

| Input | Default | Description |
|---|---|---|
| `max_text_size_kb` | no limit | Size limit for text files, in KB. The policy's `thresholds.max_text_size_kb` takes precedence. |
| `max_binary_size_kb` | no limit | Size limit for binary files, in KB. The policy's `thresholds.max_binary_size_kb` takes precedence. |
| `policy_path` | `.github/repo-size-guardian.yml` | Path to the [policy file](policy.md). If no file exists there, no policy is applied. |
| `fail_on` | `error` | Lowest severity that fails the job. `error`: only errors fail it. `warn`: any violation fails it. |
| `scan_mode` | `history` | `history` or `diff`. See [Scan modes](#scan-modes). |
| `dedupe_blobs` | `true` | Report each file content once per path, at the earliest commit that added it. `false` reports every commit that adds it. |
| `annotate_pr` | `true` | Add an [annotation](output.md#annotations) for each violation. |
| `max_annotations` | `10` | Maximum number of annotations. `0` means no limit. |
| `base_ref` | from the `pull_request` event | Commit or ref to compare from. See [Choosing the commits](#choosing-the-commits). |
| `head_ref` | from the `pull_request` event | Commit or ref to compare to. |

Sizes are in KB, where 1 KB = 1024 bytes. Numbers must be 0 or greater.
Booleans accept `true`/`false`, `yes`/`no` and `1`/`0`. An invalid value
stops the run with [exit code 2](output.md#exit-codes).

## Outputs

| Output | Description |
|---|---|
| `violations_found` | Number of violations, of any severity. |
| `summary` | One line, e.g. `2 violation(s) found (1 error, 1 warn)` or `No violations found`. |

## Triggers

The action runs on `pull_request` events. On an event without a pull request,
such as `push`, it stops with exit code 2 unless `base_ref` and `head_ref`
are set.

Pull requests from first-time contributors run only after a maintainer
approves the workflow run. This is a GitHub Actions setting that applies to
every action.

## Scan modes

| Mode | What is scanned | Use when pull requests are merged with |
|---|---|---|
| `history` | Every commit from the merge-base to the head. | A merge commit, or rebase |
| `diff` | The net change from the merge-base to the head, as on the "Files changed" tab. | Squash |

In `history` mode, a file added in one commit and deleted in a later one is
still reported, because both commits reach your history. A squash merge
keeps only the net change, so `diff` mode scans only that.

## Choosing the commits

The action compares a base and a head commit, then scans from their
merge-base to the head.

| Commit | Taken from, in order |
|---|---|
| Base | `base_ref` → the event's `pull_request.base.sha` → `origin/$GITHUB_BASE_REF` |
| Head | `head_ref` → the event's `pull_request.head.sha` → `HEAD` |

## Requirements

- **Full history.** Check out with `fetch-depth: 0`. A shallow clone stops
  the run with exit code 2.
- **The `file` command**, for `mime_types` matching only. See
  [MIME types](policy.md#mime-types).
- **Python.** The action sets up Python 3 with `actions/setup-python`, which
  stays on the `PATH` for later steps in the same job.
