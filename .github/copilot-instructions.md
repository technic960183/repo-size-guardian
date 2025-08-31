# GitHub Copilot Instructions

## Conventions
- First Read PRD.md to understand this project.
- PR title: `Step N: short-name`
- Definition of Done: All acceptance criteria met; tests pass locally.
- You can propose to change the design in the text of PR if you think the current design is bad. But only do this when necessary.
- Do not leave legacy notes or comments in files when making updates - edit directly as if the file was never modified.
- Commit message should have short informative title with details in the body of the message.
- When addressing the review comments, DO NOT erase the original PR description. And DO NOT leave the history of iteration in the description! Just change it minimally when needed.

## Testing
- Framework: `unittest`
- Use `pip install -e .` to install the package instead of using `sys.path` manipulation.
- Each .py corresponding to a test file (e.g., `tests/test_git_utils.py` for `repo_size_guardian/git_utils.py`).
- Each function has its own class.
- The test names are behavior-focused. (e.g., `test_text_file`, `test_single_commit`)
