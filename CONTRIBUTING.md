# Contributing to repo-size-guardian

Thanks for considering a contribution. This project is a small, focused
GitHub Action, and the bar for a change is simple: it should have tests,
and it should not break anything the existing test suite already covers.

## Development setup and running tests

See [docs/development.md](docs/development.md) for cloning the repo,
installing it in editable mode, and running the test suite
(`python -m unittest discover tests -v` — this project uses only the
Python standard library's `unittest`; `pytest` is not used and is not a
dependency).

A few things worth knowing before you dig in:

- **Python >= 3.8 is the supported floor.** Don't introduce 3.9+-only
  syntax (e.g. structural pattern matching, `X | Y` union types outside of
  `from __future__ import annotations` contexts), and don't change
  `python_requires` in `setup.py`.
- **Don't weaken or delete an existing test** to make your change pass.
  If a test's expectation genuinely needs to change, say so explicitly in
  the PR description and explain why.
- Add tests for new behavior. This codebase's tests run against real
  temporary git repositories (see `tests/test_base.py`) rather than mocking
  git itself wherever practical — follow that pattern for anything that
  touches git plumbing.

## Formatting

The codebase is formatted with pinned versions of
[`autopep8`](https://pypi.org/project/autopep8/) and
[`isort`](https://pypi.org/project/isort/); their options are pinned in
[`setup.cfg`](setup.cfg) so "formatted correctly" has one objective answer
regardless of whose machine runs it. The `lint` job in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) **blocks the build**
on any formatting diff — it does not auto-fix anything, so run the
formatters yourself before opening a PR:

```bash
pip install autopep8==2.3.2 pycodestyle==2.14.0 isort==5.13.2

python -m isort repo_size_guardian tests setup.py
python -m autopep8 --in-place --recursive repo_size_guardian tests setup.py
```

To check without changing anything (what CI runs):

```bash
python -m isort --check-only --diff repo_size_guardian tests setup.py
python -m autopep8 --diff --recursive --exit-code repo_size_guardian tests setup.py
```

`pycodestyle` is pinned alongside them because it is what `autopep8`
actually evaluates the code with, and `autopep8` itself only requires
`pycodestyle >= 2.11.0` — leaving it floating would let a new `pycodestyle`
release introduce a new check and fail the `lint` job with no change to
this repository.

If you use a different `autopep8`/`isort` version locally and see a diff
CI doesn't (or vice versa), install the pinned versions above — formatter
output can drift slightly between releases.

## Reporting a bug

Please [open an issue](https://github.com/technic960183/repo-size-guardian/issues)
and include:

- The version you're running (`python -m repo_size_guardian --version`, or
  the `technic960183/repo-size-guardian@<ref>` you pin in your workflow).
- Your policy file, if you're using one, and the relevant action inputs.
- What you expected vs. what happened.

If the action exited with **code 2** and the log contains a line starting
with `repo-size-guardian: error:` (also shown as a GitHub `::error::`
annotation), that's a **configuration problem** — this is on your side,
so no need to file a bug (the message explains what to fix). But if the
log instead shows a Python traceback and a line that says **"This is a
BUG in repo-size-guardian itself"**, please do file an issue with that
full traceback and the version it names — that message exists specifically
to make this case easy to report.

## Pull requests

- Keep changes focused; unrelated formatting-only diffs make a PR harder
  to review (see [Formatting](#formatting) above for how to format before
  you commit, not as part of an unrelated change).
- Make sure `python -m unittest discover tests` passes and the formatters
  report a clean diff before opening the PR — the same two things CI
  checks.
