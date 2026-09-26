# Output reference

What the action reports, and what its errors mean.

## Where results appear

| Place | Contains | Limit |
|---|---|---|
| [Job log](#job-log) | Every violation, plus the scan counts. | None |
| [Job summary](#job-summary) | A table of violations, with hints on how to fix them. | First 100 violations |
| [Annotations](#annotations) | One per violation, attached to the file. | `max_annotations`, and GitHub's own cap |
| [Step outputs](workflow.md#outputs) | `violations_found` and `summary`. | None |

## Scan counts

Every report starts with a line such as:

```
Commits scanned: 3  |  Blobs scanned: 3  |  Unique blobs: 2
```

| Count | Meaning |
|---|---|
| Commits scanned | Commits from the merge-base to the head, including commits that change no files. |
| Blobs scanned | File changes found in those commits, including deletions. |
| Unique blobs | Distinct file contents among them. |

`Commits scanned` should match the number of commits in the pull request.
A much smaller number means the wrong range was scanned: check
`fetch-depth: 0` and the [trigger](workflow.md#triggers).

## Job log

One line per violation:

```
ERROR  b1c5b3a  assets/demo.mp4  (1.4 MB)  Binary file size 1464.8 KB exceeds 200 KB limit  [rule: threshold.max_binary_size_kb]
```

The fields are severity, the commit that added the file, path, size,
reason, and the policy entry that matched. Entry names are listed in the
[evaluation order](policy.md#evaluation-order). In `diff` mode, the commit is
the head commit.

## Job summary

The summary shows the status, a table of violations (severity, file, size,
reason, policy entry) and one "How to fix" hint per kind of violation. It
lists the first 100 violations; the job log lists all of them.

## Annotations

Each violation becomes an `error` or `warning` annotation on its file, with
no line number.

- The action adds up to `max_annotations` (default 10), then one notice
  saying how many more were left out.
- GitHub shows at most 10 error and 10 warning annotations per step, whatever
  `max_annotations` says.
- An annotation appears on the "Files changed" tab only if the file is in
  the pull request's final diff. A file added and deleted within the pull
  request has its annotation on the Checks tab.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No violation at or above the `fail_on` severity. |
| `1` | At least one violation at or above the `fail_on` severity. |
| `2` | The scan didn't finish: a [configuration error](#error-messages) or an [internal error](#internal-errors). |

## Error messages

A configuration error prints `repo-size-guardian: error:` followed by one of
these messages, and adds it as an error annotation.

| Message starts with | Cause | Fix |
|---|---|---|
| `this checkout is a SHALLOW clone` | `actions/checkout` fetched only the latest commit. | Set `fetch-depth: 0`. |
| `repo-size-guardian only supports the 'pull_request' event` | The workflow ran on another event. | Use `on: pull_request`, or set `base_ref` and `head_ref`. |
| `Could not resolve a base ref` | No pull request and no `base_ref`. | Same as above. |
| `the PR head commit … is not present in this checkout` | GitHub's merge ref for the pull request is out of date, often after a push that conflicts with the base branch. | Re-run the workflow, or push to the pull request again. |
| `the PR base commit … is not present in this checkout` | The base branch was force-pushed. | Re-run the workflow, or set `base_ref`. |
| `Could not compute a merge base` | The base and head share no history in this checkout. | Set `fetch-depth: 0`; if the base branch was force-pushed, re-run the workflow. |
| `Could not resolve ref` | `base_ref` or `head_ref` names nothing in this checkout. | Check the value. |
| `Policy file '…' contains invalid YAML` | The policy file isn't valid YAML. | Fix the syntax. |
| `Policy file '…' is invalid` | The policy breaks a [validation](policy.md#validation) rule. | Fix the key the message names. |
| `Could not read policy file` | The file at `policy_path` can't be read. | Check its permissions. |
| `--max-annotations must be >= 0`, `--max-text-size-kb must be >= 0`, `--max-binary-size-kb must be >= 0` | A negative input. | Use 0 or more. |
| `Invalid boolean value` | `dedupe_blobs` or `annotate_pr` isn't a boolean. | Use `true` or `false`. |
| `Could not determine whether this is a shallow clone` | The working directory isn't a Git repository. | Add an `actions/checkout` step before this action. |
| `git command failed` | Git itself failed. | Read the Git error that follows. |

An input outside its allowed values, such as `fail_on: warning` or a size
that isn't a number, stops the run before the scan with a `usage:` block and
a line such as `error: argument --fail-on: invalid choice`. See
[Inputs](workflow.md#inputs) for the allowed values.

## Internal errors

A crash inside the action prints a Python traceback and a line starting
with `repo-size-guardian v<version>: internal error`. This is a bug in the
action. Please [open an issue](https://github.com/technic960183/repo-size-guardian/issues)
with the traceback and the version.

## Warnings

These print a warning annotation and don't change the exit code.

| Warning | Cause |
|---|---|
| `repo-size-guardian is not enforcing anything` | No policy settings and no size inputs, so nothing was checked. |
| ``your policy matches on MIME types, but the `file` command is not available`` | See [MIME types](policy.md#mime-types). |
