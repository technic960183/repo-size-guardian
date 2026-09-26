# repo-size-guardian

[![CI](https://github.com/technic960183/repo-size-guardian/actions/workflows/ci.yml/badge.svg)](https://github.com/technic960183/repo-size-guardian/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/technic960183/repo-size-guardian/graph/badge.svg)](https://codecov.io/gh/technic960183/repo-size-guardian)

Keep large files out of your Git history, including the ones a pull request
adds and then deletes.

A file committed and removed in a later commit still lives in your history,
and in every clone, for good. Most size checks only look at the final diff;
repo-size-guardian checks every commit a pull request brings in.

![Job summary of a pull request blocked by repo-size-guardian](https://raw.githubusercontent.com/technic960183/repo-size-guardian/resource/images/job-summary.png)

## Usage

```yaml
# .github/workflows/repo-size-guardian.yml
on: pull_request

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0    # full history, so every commit can be scanned
      - uses: technic960183/repo-size-guardian@v1
```

Then describe what your repository allows:

```yaml
# .github/repo-size-guardian.yml
thresholds:
  max_binary_size_kb: 500             # any binary over 500 KB
disallow:
  extensions: [exe, ipynb]            # never, at any size
rules:
  - id: large-csv
    match: { globs: ["**/*.csv"] }
    size_over_kb: 2000
    action: warn                      # report it, don't block
overrides:
  allow_globs: [data/baseline.h5]     # this one file is fine
```

- Checks every commit in the pull request, or only the net change if you
  squash-merge
- Rules by path, extension or file type, each set to warn or block
- Results in the job summary and as annotations on the pull request

**[Quick start →](https://github.com/technic960183/repo-size-guardian/blob/main/docs/quick-start.md)**
· [Policy guide](https://github.com/technic960183/repo-size-guardian/blob/main/docs/policy-guide.md)
· Reference:
[Workflow](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/workflow.md),
[Policy](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/policy.md),
[Output](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/output.md),
[Versioning](https://github.com/technic960183/repo-size-guardian/blob/main/docs/reference/versioning.md)

## License

[MIT](https://github.com/technic960183/repo-size-guardian/blob/main/LICENSE)
