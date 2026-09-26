# CLAUDE.md

## Documentation

Before editing `README.md`, `CONTRIBUTING.md`, or anything in `docs/`, read
`docs/WRITING.md`. Code comments and docstrings follow the style of the code
around them.

When you change an action input, an output, an error message, or the policy
schema, update the matching page in `docs/reference/`.

## Code

- Python 3.8 is the minimum supported version.
- Install with `pip install -e .` and import the package normally, without
  editing `sys.path`.
- Run the formatters listed in `CONTRIBUTING.md` before committing; CI fails
  on any formatting diff.
- Edit files as if they were always written that way, with no notes about
  what changed.

## Tests

- Framework: `unittest`. Run `python -m unittest discover tests`.
- One test file per module: `repo_size_guardian/git_utils.py` is tested in
  `tests/test_git_utils.py`.
- One test class per function under test.
- Name tests after the behavior they check, e.g. `test_text_file`,
  `test_single_commit`.
- Tests that touch git run against real temporary repositories; see
  `tests/test_base.py`.

## Commits and pull requests

- Commit messages have a short, informative title, with details in the body.
- A PR description describes the change as it stands. When addressing review
  comments, keep the original description and adjust only what the change
  requires.
- If the current design looks wrong, say so in the PR description, only when
  it matters.
