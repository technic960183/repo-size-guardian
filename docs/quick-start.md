# Quick start

Set up repo-size-guardian and read your first report.

## 1. Add the workflow

Create `.github/workflows/repo-size-guardian.yml`:

```yaml
on: pull_request

jobs:
  repo-size-guardian:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: technic960183/repo-size-guardian@v1
```

`fetch-depth: 0` checks out the full history, which the action needs to scan
every commit in a pull request.

## 2. Set your limits

Add inputs to the action step:

```yaml
      - uses: technic960183/repo-size-guardian@v1
        with:
          max_text_size_kb: 1000
          max_binary_size_kb: 200
```

The check now fails when any commit in a pull request adds a text file over
1000 KB or a binary file over 200 KB. A third input,
`disallow_extensions: "exe, zip"`, bans file types at any size. For anything
more, the [policy guide](policy-guide.md) covers moving to a policy file.

## 3. If your repository already has history

Let the check report without blocking while you tune your limits:

```yaml
      - uses: technic960183/repo-size-guardian@v1
        continue-on-error: true
```

Violations still appear in the report, but the check passes. Remove the line
when you're ready to enforce them.

## 4. Open a pull request

The job summary and the job log show what was found:

```
Repo Size Guardian scan report
================================
Commits scanned: 3  |  Blobs scanned: 3  |  Unique blobs: 2

  ERROR  b1c5b3a  assets/demo.mp4  (1.4 MB)  Binary file size 1464.8 KB exceeds 200 KB limit  [rule: max_binary_size_kb]

1 violation(s) in 1 file(s) (1 error)
```

Here, the pull request added `assets/demo.mp4` and deleted it again in the
next commit. The video is still reported, because the action checks every
commit, not only the final result.

On your first run, check that `Commits scanned` matches the number of commits
in the pull request.

## 5. Block merging (optional)

A failing check doesn't stop a merge on its own. To block pull requests that
fail it, make `repo-size-guardian` a required status check in a
[branch protection rule](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule#creating-a-branch-protection-rule).

## Next

- [Policy guide](policy-guide.md): write a policy that fits your repository.
- [Output reference](reference/output.md): every part of the report, and
  what each error means.
