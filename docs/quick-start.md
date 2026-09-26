# Quick start

Set up repo-size-guardian and read your first report.

## 1. Add the workflow

Create `.github/workflows/repo-size-guardian.yml`:

```yaml
on: pull_request

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: technic960183/repo-size-guardian@v1
```

`fetch-depth: 0` checks out the full history, which the action needs to scan
every commit in a pull request.

## 2. Add a policy

Create `.github/repo-size-guardian.yml`:

```yaml
thresholds:
  max_text_size_kb: 1000
  max_binary_size_kb: 200
```

The check now fails when any commit in a pull request adds a text file over
1000 KB or a binary file over 200 KB. The [policy guide](policy-guide.md)
covers everything else a policy can do.

## 3. If you squash-merge

A squash merge keeps only a pull request's net change, so scan only that:

```yaml
      - uses: technic960183/repo-size-guardian@v1
        with:
          scan_mode: diff
```

## 4. If your repository already has history

Let the check report without blocking while you tune the policy:

```yaml
      - uses: technic960183/repo-size-guardian@v1
        continue-on-error: true
```

Violations still appear in the report, but the check passes. Remove the line
when you're ready to enforce the policy.

## 5. Open a pull request

The job summary and the job log show what was found:

```
Repo Size Guardian scan report
================================
Commits scanned: 3  |  Blobs scanned: 3  |  Unique blobs: 2

Violations (1):
  ERROR  b1c5b3a  assets/demo.mp4  (1.4 MB)  Binary file size 1464.8 KB exceeds 200 KB limit  [rule: threshold.max_binary_size_kb]
```

Here, the pull request added `assets/demo.mp4` and deleted it again in the
next commit. The video is still reported, because the commit that added it
would still be in your history.

On your first run, check that `Commits scanned` matches the number of commits
in the pull request.

## Next

- [Policy guide](policy-guide.md): write a policy that fits your repository.
- [Output reference](reference/output.md): every part of the report, and
  what each error means.
