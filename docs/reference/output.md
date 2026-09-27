# Output reference

What the action reports, and what its errors mean.

## Where results appear

| Place | Contains | Limit |
|---|---|---|
| [Job log](#job-log) | Every violating file, plus the scan counts. | None |
| [Job summary](#job-summary) | A table of violating files, with hints on how to fix them. | First 100 files |
| [Annotations](#annotations) | One per violating file, attached to it. | `max_annotations`, and GitHub's own cap |
| [Step outputs](workflow.md#outputs) | `violating_file_count`, `violation_count` and `summary`. | None |

A file can hit more than one rule; it still appears once, at its highest
severity, listing every rule it hit. The same path can appear twice if a
pull request replaces one violating version of a file with another.

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

One line per violating file (see [above](#where-results-appear) for when a
path can appear twice):

```
  WARN   7dfd83b  big.log  (58.6 KB)  Logs belong in the artifact store (58.6 KB > 50 KB)  [rule: big-log]
  ERROR  7dfd83b  notebook.ipynb  (3 B)  Matched rule 'no-notebooks'  [rule: no-notebooks]
```

The fields are severity, the commit that introduced this version of the
file, path, [size](policy.md#sizes), the violation message(s) (joined by
`; ` when a file hit more than one rule), and the rule(s) that matched —
`[rule: name]`, or `[rules: name, name]` for several. See
[How rules are applied](policy.md#how-rules-are-applied) for why a file can
hit more than one rule. In `diff` mode, the commit is the head commit.

## Job summary

The summary shows the status, a table of violating files (severity, file,
size, reason, rules) and one "How to fix" hint per kind of violation. It
lists the first 100 rows; the job log lists all of them.

## Annotations

Each violating file becomes an `error` or `warning` annotation, listing
every rule it hit, with no line number.

- The action adds up to `max_annotations` (default 10) annotations, then one
  notice saying how many more files were left out.
- GitHub shows at most 10 error, 10 warning and 10 notice annotations per
  step, whatever `max_annotations` says.
- An annotation appears on the "Files changed" tab only if the file is in
  the pull request's final diff. A file added and deleted within the pull
  request has its annotation on the Checks tab.

## Exit codes

| Code | Meaning | Whose to fix |
|---|---|---|
| `0` | Scan completed; nothing fails the job. | — |
| `1` | Scan completed; a violation fails the job under `fail_on`. | The pull request's author |
| `2` | Configuration error: the policy file, the inputs, a policy file combined with quick-start inputs, or the workflow/checkout. No violations are reported. | The repository's maintainers |
| `3` | Internal error: a bug in the action. | The action's maintainers |

A run that exits with 2 or 3 reports no violations. The policy file and the
inputs are checked before anything is scanned, so a broken setup fails right
away.

## Error messages

A configuration error prints `repo-size-guardian: error:` followed by one of
these messages, and adds it as an error annotation and a job summary saying
this is the repository's setup to fix, not the pull request.

| Message starts with | Cause | Fix |
|---|---|---|
| `this checkout is a SHALLOW clone` | `actions/checkout` fetched only the latest commit. | Set `fetch-depth: 0`. |
| `repo-size-guardian only supports the 'pull_request' event` | The workflow ran on another event. | Use `on: pull_request`, or set `base_ref` and `head_ref`. |
| `Could not resolve a base ref` | No pull request and no `base_ref`. | Same as above. |
| `the PR head commit … is not present in this checkout` | GitHub's merge ref for the pull request is out of date, often after a push that conflicts with the base branch. | Re-run the workflow, or push to the pull request again. |
| `the PR base commit … is not present in this checkout` | The base branch was force-pushed. | Re-run the workflow, or set `base_ref`. |
| `Could not compute a merge base` | The base and head share no history in this checkout. | Set `fetch-depth: 0`; if the base branch was force-pushed, re-run the workflow. |
| `Could not resolve ref` | `base_ref` or `head_ref` names nothing in this checkout. | Check the value. |
| `Policy file '…' contains invalid YAML` | The policy file isn't valid YAML. An unquoted `size` value or glob gets an added hint to quote it. | Fix the syntax. |
| `Policy file '…' is invalid` | The policy breaks a [validation](policy.md#validation) rule. | Fix the key the message names. |
| `Could not read policy file` | The file at `policy_path` can't be read. | Check its permissions. |
| `Policy file '…' can't be combined with the … input(s)` | A policy file exists and `disallow_extensions`/`max_text_size_kb`/`max_binary_size_kb` is also set. | Remove the input(s), or paste the printed `rules:` block into the policy file. |
| `--disallow-extensions '…' contains no extensions` | The value had nothing left after splitting on commas and whitespace. | List at least one extension. |
| `--max-annotations must be >= 0`, `--max-text-size-kb must be >= 0`, `--max-binary-size-kb must be >= 0` | A negative input. | Use 0 or more. |
| `Invalid boolean value` | `dedupe_blobs` or `annotate_pr` isn't a boolean. | Use `true` or `false`. |
| `Could not determine whether this is a shallow clone` | The working directory isn't a Git repository. | Add an `actions/checkout` step before this action. |
| `git command failed` | Git itself failed. | Read the Git error that follows. |

An input outside its allowed values, such as `fail_on: warning` or a size
that isn't a number, stops the run before the scan with a `usage:` block and
a line such as `error: argument --fail-on: invalid choice`. See
[Inputs](workflow.md#inputs) for the allowed values.

## Internal errors

A crash inside the action prints a Python traceback, a line starting with
`repo-size-guardian v<version>: internal error`, and exits 3. This is a bug
in repo-size-guardian, not a problem with the pull request or with the
repository's setup; a job summary says so too. Please
[open an issue](https://github.com/technic960183/repo-size-guardian/issues)
with the traceback and the version.

## Warnings

These print a warning annotation and don't change the exit code.

| Warning | Cause |
|---|---|
| `repo-size-guardian: no policy rules and none of disallow_extensions, max_text_size_kb, max_binary_size_kb are configured` | No policy file with rules, and none of the three quick-start inputs is set, so nothing was checked. |
| ``your policy matches on MIME types, but the `file` command is not available`` | See [MIME types](policy.md#mime-types). |
| `could not read the size of <path>` | A rule has a `size` condition and this file version's size couldn't be read from the checkout. See [Sizes](policy.md#sizes). |
| `your policy matches on transient or transient_version, but scan_mode is 'diff'` | See [Transient files](policy.md#transient-files). |
