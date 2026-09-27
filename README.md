# repo-size-guardian

[![CI](https://github.com/technic960183/repo-size-guardian/actions/workflows/ci.yml/badge.svg)](https://github.com/technic960183/repo-size-guardian/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/technic960183/repo-size-guardian/graph/badge.svg)](https://codecov.io/gh/technic960183/repo-size-guardian)

Keep large files out of your Git history, including the ones a pull request
adds in one commit and deletes in a later one.

Such a file still lives in your history, and in every clone, for good. Most
size checks only look at the final diff; repo-size-guardian checks every
commit a pull request brings in, and blocks the files you don't want before
they enter your repository.

![Job summary of a pull request blocked by repo-size-guardian](https://raw.githubusercontent.com/technic960183/repo-size-guardian/resource/images/job-summary.png)

## Usage

```yaml
# .github/workflows/repo-size-guardian.yml
on: pull_request

jobs:
  repo-size-guardian:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0    # full history, so every commit can be scanned
      - uses: technic960183/repo-size-guardian@v1
        with:
          max_text_size_kb: 1000
          max_binary_size_kb: 200
          disallow_extensions: "exe, dll, zip"
```

That's the whole setup: it blocks a commit that adds a text file over
1000 KB, a binary file over 200 KB, or a file with a banned extension, at
any size.

For more control, replace those inputs with a policy file:

```yaml
# .github/repo-size-guardian.yml
rules:
  - id: skip-vendor
    match: { globs: ["vendor/**"] }
    action: stop
  - id: no-executables
    match: { extensions: [exe, dll, zip] }
  - id: large-assets
    match: { size: ">5MB" }
  - id: large-csv
    description: CSV files belong in the data bucket
    match: { globs: ["**/*.csv"], size: ">2MB" }
    action: warn
  - id: added-then-removed
    description: Files that don't survive to the end of this pull request
    match: { transient: true, size: ">1MB" }
    action: warn
```

Rules run top to bottom: `stop` skips every later rule for a matching file,
so nothing under `vendor/` ever reaches the checks below it. `warn` reports
a file without failing the job. The last rule catches a file added and
deleted within the same pull request — the kind of change a check on the
final diff alone would never see.

- Rules by path, extension, type or size, each set to warn, block, or stop
  checking further rules
- Catches a file even if a later commit in the same pull request removes or
  shrinks it
- Results in the job summary and as annotations on the pull request
- Needs no token or extra permissions

**[Quick start →](https://github.com/technic960183/repo-size-guardian/blob/main/docs/quick-start.md)**
· [Policy guide](https://github.com/technic960183/repo-size-guardian/blob/main/docs/policy-guide.md)
· Reference:
[Workflow](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/workflow.md),
[Policy](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/policy.md),
[Output](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/output.md),
[Versioning](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/versioning.md)

## License

[MIT](https://github.com/technic960183/repo-size-guardian/blob/main/LICENSE)
