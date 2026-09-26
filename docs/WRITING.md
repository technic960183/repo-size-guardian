# Writing the docs

These docs are one path for one person: a repo owner who finds the action,
sets it up, and later comes back to look something up. Each page meets them
at one stage.

| Page | Reader | Their question |
|---|---|---|
| `README.md` | Owner deciding whether to use it | "Is this worth trying?" |
| `docs/quick-start.md` | Owner setting it up | "How do I get a first run working?" |
| `docs/policy-guide.md` | Owner tuning it | "How do I describe what my repo allows?" |
| `docs/reference/*.md` | Anyone with one specific question | "What exactly does X do?" |
| `CONTRIBUTING.md`, `docs/development.md` | Contributor | "How do I build and test this?" |

A PR author whose PR was blocked reads the action's output, not these pages.

## The test

For each sentence, ask: does it help this page's reader with their question,
at this point? If it helps a different reader, it goes on that reader's page.

## Where new information goes

- Needed for the first run → quick start.
- Needed while writing a policy → the policy guide, at the step where the
  reader meets it.
- Needed only when something surprises the reader → the reference page it
  belongs to:
  - `workflow.md`: what goes in the workflow file.
  - `policy.md`: what goes in the policy file, and how files are matched.
  - `output.md`: what the action reports, and what its errors mean.
  - `versioning.md`: which version to use.

The reference has the full detail. Other pages give enough to act, then link
to it. Reference headings are link targets, so keep them stable.

## Voice

Read the README and the policy guide before writing: an example first, short
sentences, and a reason only when it changes what the reader does. Reference
pages are for scanning: tables and one-line definitions.

Readers paste examples as they are, so every example works as written.

The README uses absolute links, because it is also shown on the Marketplace
and PyPI pages. Other pages link relatively.

## Images

Images live on the orphan `resource` branch and are linked by raw URL:
`https://raw.githubusercontent.com/technic960183/repo-size-guardian/resource/images/<file>`.

## Changing this file

Change this file when the readers or the pages change. When a page goes
wrong, fix the page; it's the example the next writer copies.
