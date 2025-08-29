# GitHub Copilot Instructions

## Conventions

- First Read PRD.md to understand this project.
- Branch naming (GitHub limitation): copilot/<step-number>-<short-name>
- PR title: Step <n>: <short-name>
- Definition of Done (DoD): All acceptance criteria met; tests pass locally and in CI; docs updated as specified.
- You can propose to change the design in the text of PR if you think the current design is bad. But only do this when necessary.
- Use `pip install -e .` to install the package in development mode for testing instead of using sys.path manipulation.
- Do not leave legacy notes or comments in files when making updates - edit directly as if the file was never modified.