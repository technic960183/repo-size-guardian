# Policy reference

Everything that goes in the policy file, and how files are matched against it.

The policy file is YAML, read from the `policy_path` input
(default `.github/repo-size-guardian.yml`). It's optional: without it, the
`disallow_extensions`, `max_text_size_kb` and `max_binary_size_kb` inputs
apply instead, each acting as one rule (see the
[workflow reference](workflow.md#inputs)). A policy file and those inputs
can't be combined.

The top level is a mapping with one key, `rules`: an ordered list of rules.
An empty file, a file containing only `null`, and `rules` set to `null` or
`[]` are all valid and mean no rules.

## Rule keys

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | string | none | Name shown in reports. Must be unique among rules that set one. Without an `id`, the rule is named `rules[N]` (0-based). |
| `description` | string | none | Shown in the report in place of `Matched rule '<name>'`. |
| `match` | mapping | required | At least one condition from [Match conditions](#match-conditions). |
| `action` | `warn`, `error` or `stop` | `error` | What a match does. See [How rules are applied](#how-rules-are-applied). |

## Match conditions

| Key | Type | Holds when |
|---|---|---|
| `globs` | list of globs | the path matches any glob. See [Globs](#globs). |
| `extensions` | list of strings | the extension matches any entry. See [Extensions](#extensions). |
| `mime_types` | list of strings | the MIME type matches any entry. See [MIME types](#mime-types). |
| `binary` | boolean | `true`: the file is binary. `false`: the file is text, or its type couldn't be determined. |
| `size` | string | the size satisfies the condition. See [Sizes](#sizes). |
| `transient` | boolean | see [Transient files](#transient-files). |
| `transient_version` | boolean | see [Transient files](#transient-files). |

A rule matches a file when every condition set in its `match` holds; a key
left out imposes no condition. Within `globs`, `extensions` and
`mime_types`, matching any one entry is enough; the different keys must all
hold together. `match` must set at least one condition; a missing or empty
`match`, or an empty list such as `globs: []`, is a policy error.

## How rules are applied

Rules run top to bottom against every file version a pull request
introduces.

| A matching rule with action | Does |
|---|---|
| `warn` or `error` | Records a violation at that severity; checking continues to the next rule. |
| `stop` | Ends checking for this file version. Violations already recorded stay. |

A file version with at least one violation becomes one row in the report,
listing every rule it matched, at the highest severity among them (`error`
above `warn`). See the [output reference](output.md) for the report format.

## Sizes

- A `size` condition is one quoted string: an operator (`>`, `>=`, `<`,
  `<=`), a number, and a unit (`B`, `KB`, `MB`, `GB`), e.g. `">500KB"` or
  `"<=6 mb"`.
- The operator sets the strictness: `>` excludes a file exactly at the
  limit, `>=` includes it.
- 1 KB = 1024 B, 1 MB = 1024 KB, 1 GB = 1024 MB. Units are case-insensitive,
  and spaces and decimals are allowed, e.g. `">1.5 MB"`.
- Quote the value. An unquoted `>500KB` is invalid YAML — PyYAML reads the
  leading `>` as a block-scalar marker — and the error message tells you to
  add quotes.
- A file version whose size can't be read never matches a `size` condition.
  The run prints a warning naming the file:
  `repo-size-guardian: could not read the size of <path> (commit <short sha>), so size conditions can't match it. The checkout may be incomplete.`
- Reports show sizes in B, KB, MB or GB, each 1024 times the one before.
- A file tracked by Git LFS is checked at the size of its pointer file.

## Text or binary

A file's kind comes from its content, not its name.

- With the `file` command: MIME types `text/*`, `application/json`,
  `application/xml`, `application/javascript` and empty files are text.
  Everything else is binary; for example, an SVG is `image/svg+xml`, so it's
  binary. Run `file --mime <path>` to see a file's MIME type.
- Without it: a file containing a null byte, or mostly non-printable
  characters, is binary.
- A file whose kind can't be determined matches `binary: false`, not
  `binary: true`.

## Globs

Each list of globs is read like a `.gitignore` file. Matching is
case-sensitive.

- A pattern with a `/` at the start or in the middle matches from the
  repository root. Any other pattern matches at any depth.
- A pattern that matches a folder also matches everything in it.
- In YAML, put a pattern that starts with `*`, `!` or `#` in quotes.

| Syntax | Matches |
|---|---|
| `*` | Any characters within one path segment (never `/`). |
| `**` | As a whole segment: zero or more segments. |
| `?` | One character, never `/`. |
| `[abc]`, `[0-9]` | One character from the set or range. |
| `[!abc]` | One character not in the set. |
| `dir/` | Everything in the folder `dir`, but not a file named `dir`. |
| `/pattern` | `pattern`, from the repository root only. |
| `!pattern` | Nothing. Excludes paths that an earlier pattern in the same list matched. |
| `#comment` | Nothing. The pattern is a comment. |
| `\#`, `\!`, `\*` | The character itself. |

| Pattern | Path | Match |
|---|---|:---:|
| `*.md` | `README.md`, `docs/a.md` | Yes |
| `README.md` | `docs/README.md` | Yes |
| `/*.md` | `a.md` | Yes |
| `/*.md` | `docs/a.md` | No |
| `node_modules` | `node_modules/x.js`, `a/node_modules/x.js` | Yes |
| `build/` | `build/x`, `a/build/x` | Yes |
| `docs/**` | `docs/a.txt`, `docs/x/y/a.txt` | Yes |
| `docs/**` | `docsx/a.txt`, `x/docs/a.txt` | No |
| `foo/*.txt` | `foo/bar.txt` | Yes |
| `foo/*.txt` | `foo/baz/bar.txt` | No |
| `a/**/b` | `a/b`, `a/x/b`, `a/x/y/b` | Yes |
| `a/**/b` | `x/a/b` | No |
| `file[0-9].txt` | `file5.txt` | Yes |
| `a?b.txt` | `a/b.txt` | No |
| `"*.log"`, `"!keep.log"` | `a.log` | Yes |
| `"*.log"`, `"!keep.log"` | `keep.log`, `x/keep.log` | No |

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

## Transient files

`transient` and `transient_version` match on whether a file (or this exact
version of it) survives to both ends of the pull request. Both are booleans
with the same semantics as `binary`: set one to filter, leave it out for no
condition.

- `transient`: `true` when the file's path exists at neither the commit the
  pull request branched from, nor its head. A file added in one commit and
  deleted in a later one is transient.
- `transient_version`: `true` when this exact version — this path with this
  content — exists at neither end. A file whose path survives to the head
  but whose content changed along the way has a transient version for every
  commit before the last one; `transient` implies `transient_version`.
- Pair `transient_version` with a `size` condition. On its own it matches
  every intermediate edit to a file, not only an oversized one.

Both need `scan_mode: history`; see [Scan modes](workflow.md#scan-modes). In
`diff` mode, which only sees the final result, they're always `false`, and a
policy that matches on either gets one warning:
`repo-size-guardian: your policy matches on transient or transient_version, but scan_mode is 'diff', which only sees the final diff, so these conditions never match. Use scan_mode: history.`

A report row for a transient version adds this note to its reason:
`This version of <path> was removed or replaced later in this pull request, but it stays in the history.`

## Validation

The whole file is checked before scanning. Any of these stops the run with
[exit code 2](output.md#exit-codes) and a message naming the problem:

- Invalid YAML, or a top level that isn't a mapping.
- An unknown key, at any level, e.g. `Unknown key 'thresholds' in the top
  level of the policy file; accepted keys are: rules`.
- A value of the wrong type, e.g. a string where a list is expected.
- A rule's `match` missing, or with no condition set — a rule must set at
  least one.
- An empty list, such as `globs: []`.
- A `size` value that isn't one operator, number and unit in a quoted
  string, e.g. `'rules[2]'.match.size must be one condition such as
  ">500KB" or "<=6MB", got '500'`.
- Two rules with the same `id`.
- An `action` other than `warn`, `error` or `stop`.

When the YAML itself fails to parse because a `size` value or a glob
starting with `*`, `!` or `#` wasn't quoted, the message adds a reminder to
quote it.

An empty file, a file containing only `null`, and a key set to `null` are
all valid and mean "not set".
