# Policy reference

Everything that goes in the policy file, and how files are matched against it.

The policy file is YAML, read from the `policy_path` input
(default `.github/repo-size-guardian.yml`). It is optional: without it, only
the `max_text_size_kb` and `max_binary_size_kb` inputs apply.

## Keys

Every key is optional.

| Key | Type | Description |
|---|---|---|
| `ignore.globs` | list of globs | Paths that are never checked. |
| `ignore.paths` | list of globs | Same as `ignore.globs`. |
| `disallow.extensions` | list of strings | Extensions that are never allowed, e.g. `[exe, ipynb]`. |
| `disallow.globs` | list of globs | Paths that are never allowed. |
| `disallow.mime_types` | list of strings | MIME types that are never allowed, e.g. `application/x-dosexec`. |
| `thresholds.max_text_size_kb` | number | Size limit for text files. Replaces the `max_text_size_kb` input. |
| `thresholds.max_binary_size_kb` | number | Size limit for binary files. Replaces the `max_binary_size_kb` input. |
| `rules` | list of rules | Checked in order. See [Rules](#rules). |
| `overrides.allow_globs` | list of globs | Paths that pass every check. |

## Rules

Each entry in `rules`:

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | string | required | Unique name, shown in reports. |
| `description` | string | `""` | Shown in the report when the rule has no `size_over_kb`. |
| `match.globs` | list of globs | `[]` | Files whose path matches. |
| `match.extensions` | list of strings | `[]` | Files whose extension matches. |
| `match.mime_types` | list of strings | `[]` | Files whose MIME type matches. |
| `match.binary` | boolean | not set | `true`: binary files only. `false`: text files only. |
| `size_over_kb` | number | not set | Report only files larger than this. Not set: report every matching file. |
| `action` | `warn` or `error` | `error` | Severity of the violation. |

A file matches a rule when it matches any of `match.globs`,
`match.extensions` or `match.mime_types`, and also `match.binary` if set.
A rule with none of the three lists matches every file, so
`match: { binary: true }` with `size_over_kb: 100` means "binary files over
100 KB".

## Evaluation order

Each file is checked in this order. The first step that applies decides the
result.

| Step | Check | Result | Reported as |
|---|---|---|---|
| 1 | `ignore.globs`, `ignore.paths` | Pass | |
| 2 | `overrides.allow_globs` | Pass | |
| 3 | The first rule in `rules` that matches the file | Violation if the rule has no `size_over_kb` or the file is larger; otherwise pass. Steps 4 and 5 don't run. | The rule's `id` |
| 4 | `disallow.extensions`, then `disallow.globs`, then `disallow.mime_types` | Error | `disallow.extensions`, `disallow.globs`, `disallow.mime_types` |
| 5 | The size limit for the file's kind: the policy's `thresholds` value, else the input | Error if larger | `threshold.max_text_size_kb`, `threshold.max_binary_size_kb` |
| – | Nothing applied | Pass | |

Deleted files are not checked.

## Sizes

- Sizes are in KB, where 1 KB = 1024 bytes.
- "Larger" means strictly greater: a file exactly at a limit passes.
- A file tracked by Git LFS is checked at the size of its pointer file.

## Text or binary

A file's kind comes from its content, not its name.

- With the `file` command: MIME types `text/*`, `application/json`,
  `application/xml`, `application/javascript` and empty files are text.
  Everything else is binary; for example, an SVG is `image/svg+xml`, so it's
  binary. Run `file --mime <path>` to see a file's MIME type.
- Without it: a file containing a null byte, or mostly non-printable
  characters, is binary.
- A file whose kind can't be determined is checked against the text size
  limit.

## Globs

Globs match the whole path from the repository root. Matching is
case-sensitive.

| Syntax | Matches |
|---|---|
| `*` | Any characters within one path segment (never `/`). |
| `**` | As a whole segment: zero or more segments. |
| `?` | One character, never `/`. |
| `[abc]`, `[0-9]` | One character from the set or range. |
| `[!abc]` | One character not in the set. |
| `dir/` | Same as `dir/**`. |

| Pattern | Path | Match |
|---|---|:---:|
| `*.md` | `README.md` | Yes |
| `*.md` | `docs/a.md` | No |
| `**/*.md` | `a.md`, `x/y/a.md` | Yes |
| `docs/**` | `docs`, `docs/a.txt`, `docs/x/y/a.txt` | Yes |
| `docs/**` | `docsx/a.txt` | No |
| `foo/*.txt` | `foo/bar.txt` | Yes |
| `foo/*.txt` | `foo/baz/bar.txt` | No |
| `a/**/b` | `a/b`, `a/x/b`, `a/x/y/b` | Yes |
| `a/**/b` | `a/b/c` | No |
| `file[0-9].txt` | `file5.txt` | Yes |
| `a?b.txt` | `a/b.txt` | No |

## Extensions

- The extension is the text after the last `.` in the file name:
  `archive.tar.gz` has the extension `gz`.
- Matching is case-insensitive, and a leading `.` is optional: `ipynb`,
  `.ipynb` and `IPYNB` are the same.
- Files with no `.` in the name, and dotfiles such as `.gitignore`, have no
  extension.
- To match a multi-part extension, use a glob such as `**/*.tar.gz`.

## MIME types

- MIME types come from the `file --mime` command. GitHub-hosted runners have
  it installed.
- Matching is case-insensitive. `application/*` matches every `application`
  subtype. Anything else is an exact match.
- Without the `file` command, no file has a MIME type, so `mime_types`
  entries match nothing. The run prints a warning when the policy uses them.

## Validation

The whole file is checked before scanning. Any of these stops the run with
[exit code 2](output.md#exit-codes) and a message naming the problem:

- Invalid YAML, or a top level that isn't a mapping.
- An unknown key, at any level.
- A value of the wrong type, e.g. a string where a list is expected.
- A rule without an `id`, or two rules with the same `id`.
- An `action` other than `warn` or `error`.
- A negative size.

An empty file, a file containing only `null`, and a key set to `null` are
all valid and mean "not set".
