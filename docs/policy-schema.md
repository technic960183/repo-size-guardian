# Policy File Schema

The policy file is an **optional** YAML file that gives you fine-grained control
over what repo-size-guardian flags, beyond the blunt `max_text_size_kb` /
`max_binary_size_kb` action inputs. Point the `policy_path` input at it
(default: `.github/repo-size-guardian.yml`). If the file doesn't exist, the
action runs with no policy at all — that's not an error, it just means only
the action-input thresholds (if any) apply.

If the file *does* exist but is malformed — a typo'd key, a duplicate rule id,
a string where a list is expected — **the run fails immediately with exit code
2**, before any file is scanned. This is deliberate: a policy is a safety net,
so a silently-ignored typo in it would be worse than a loud failure. See
[Validation errors](#validation-errors) below.

This document describes the schema as actually implemented in
`repo_size_guardian/rule_engine.py`. Where the schema is subtle (glob
semantics especially), it's backed by the test suite
(`tests/test_rule_engine.py`, `tests/test_evaluator.py`) — treat this doc and
those tests as the two sources of truth, in that order.

## Top-level keys

All five top-level keys are optional; an empty file (or one containing only
`null`) is a valid, empty policy. Any key not listed here is a hard error.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `ignore` | mapping | (none) | Paths to skip entirely — never checked, never reported. |
| `disallow` | mapping | (none) | Simple "these are never allowed" lists (extensions/globs/MIME types). |
| `thresholds` | mapping | (none) | Policy-level size limits, overriding the `max_text_size_kb` / `max_binary_size_kb` action inputs. |
| `rules` | list | `[]` | Ordered, structured rules with their own match criteria, size gates, and severity. |
| `overrides` | mapping | (none) | Currently just `allow_globs`, an explicit allowlist that beats everything else. |

### `ignore`

| Key | Type | Default | Notes |
|---|---|---|---|
| `globs` | list of strings | `[]` | Glob patterns (see [Glob semantics](#glob-semantics)). |
| `paths` | list of strings | `[]` | Same matching engine as `globs` — despite the name, these are glob patterns too, so an exact path like `vendor/exact.bin` works, but so does a pattern. Use this list purely for organization (e.g. "exact paths" vs. "patterns") if you like; the action doesn't distinguish them. |

A path matching anything in `ignore.globs` or `ignore.paths` is skipped
silently — no violation, not even a warning, regardless of size or any other
rule.

### `disallow`

| Key | Type | Default | Notes |
|---|---|---|---|
| `extensions` | list of strings | `[]` | e.g. `["ipynb", "exe"]`. Case-insensitive; leading `.` optional. |
| `globs` | list of strings | `[]` | Path glob patterns. |
| `mime_types` | list of strings | `[]` | e.g. `["application/x-dosexec", "application/*"]`. Case-insensitive; `/*` subtype wildcard supported. |

A match against any of these three lists produces an `error`-severity
violation with category `disallowed`. There is no per-entry severity control
here — if you need `warn` severity or a size gate on a specific pattern, use
`rules` instead.

### `thresholds`

| Key | Type | Default | Notes |
|---|---|---|---|
| `max_text_size_kb` | non-negative number | `null` (unlimited) | Overrides the `max_text_size_kb` action input when set. |
| `max_binary_size_kb` | non-negative number | `null` (unlimited) | Overrides the `max_binary_size_kb` action input when set. |

If a policy threshold is set, it wins outright over the corresponding action
input for every file (there's no per-file "whichever is smaller" merge). If
it's unset (or the key is absent), the action input's value is used instead;
if that's also unset, there's no limit for that file kind.

"Binary vs. text" is decided by the tool's own content detection
(`is_binary`); a file whose type couldn't be determined is treated as text.

### `rules`

A list of structured rules, evaluated **in the order they're declared** — the
first one that matches a given file wins (see
[Evaluation order](#evaluation-order)).

| Key | Type | Default | Notes |
|---|---|---|---|
| `id` | string | *(required)* | Must be non-empty and unique across all rules. Used in violation output (`rule_name`) and in error messages. |
| `description` | string | `""` | Free-form text, included in the violation message when the rule has no size gate. |
| `match` | mapping | `{}` | See below. An absent or empty `match` matches **every** file. |
| `match.globs` | list of strings | `[]` | |
| `match.extensions` | list of strings | `[]` | |
| `match.mime_types` | list of strings | `[]` | |
| `match.binary` | boolean | `null` (no filter) | |
| `size_over_kb` | non-negative number | `null` (no gate — see below) | |
| `action` | `"warn"` \| `"error"` | `"error"` | |

See [Rule match combination](#rule-match-combination) for exactly how
`match.*` fields combine, and [Evaluation order](#evaluation-order) for what
`size_over_kb` does to whether a match becomes a violation.

### `overrides`

| Key | Type | Default | Notes |
|---|---|---|---|
| `allow_globs` | list of strings | `[]` | Path globs that are always allowed — this beats `disallow`, every `rules` entry, and the global thresholds. It does **not** beat `ignore` (which already skips the file even earlier) — the practical difference only shows up if you also care about `ignore` vs. `allow_globs` being logged differently, which they currently aren't. |

Use this to exempt one specific known-large file (e.g. a reference dataset)
without loosening your policy for everything else.

## Evaluation order

For each candidate file (a blob introduced by the PR that hasn't been
deleted), repo-size-guardian applies these steps **in order**, and **stops at
the first one that applies**:

0. **Skip deletions and empty content.** A blob that represents a file
   deletion, or that otherwise has no blob SHA, is never evaluated.
1. **`ignore.globs` / `ignore.paths`.** If the path matches either list, skip
   silently. No violation.
2. **`overrides.allow_globs`.** If the path matches, skip silently. No
   violation.
3. **The first matching rule in `rules` (declaration order).** ⚠️ **This step
   is terminal, whether or not it produces a violation.** See below.
4. **`disallow` lists**, checked in this fixed order: `extensions`, then
   `globs`, then `mime_types`. The first list that matches produces an
   `error` violation and stops evaluation.
5. **Global thresholds** (`thresholds.max_text_size_kb` /
   `thresholds.max_binary_size_kb` from the policy, falling back to the
   `max_text_size_kb` / `max_binary_size_kb` action inputs). If the effective
   limit is exceeded, this produces an `error` violation.

If a file falls through every step without matching anything, it passes with
no violation.

All size comparisons are **strictly greater-than**: a file exactly at a limit
does not violate it. Sizes throughout the policy file (`size_over_kb`,
`max_text_size_kb`, `max_binary_size_kb`) are in **KB = 1024 bytes**, not
1000-byte kilobytes.

### The surprising part: a matched rule never falls through

Step 3 deserves emphasis because it is the single most counter-intuitive
piece of behavior in this tool. When a rule's `match` criteria hit a file:

- If the rule has **no** `size_over_kb`, it *always* produces a violation
  (content match alone is enough).
- If the rule **has** a `size_over_kb`, it produces a violation only when the
  file's size is strictly greater than that gate.

**Either way**, once a rule matches, evaluation stops right there for that
file. If the rule had a size gate and the file didn't exceed it, the file
passes — it does **not** then get checked against the `disallow` lists or the
global thresholds, even if it would have violated those.

#### Worked example

```yaml
rules:
  - id: notebooks-must-be-small
    description: "Notebooks are allowed, but must stay small"
    match:
      extensions: ["ipynb"]
    size_over_kb: 200
    action: error

thresholds:
  max_text_size_kb: 10
```

Given `analysis.ipynb` at 50 KB:

- The rule's `match.extensions: ["ipynb"]` matches the file — step 3 fires.
- 50 KB is not greater than the rule's `size_over_kb: 200` gate, so **no
  violation is produced**.
- Evaluation **stops here**. Step 5's `max_text_size_kb: 10` is never
  consulted for this file, even though 50 KB is five times over that global
  limit.

Verified directly against the evaluator:

```python
from repo_size_guardian.models import Blob
from repo_size_guardian.rule_engine import Policy, Rule
from repo_size_guardian.evaluator import EvaluationConfig, evaluate_blobs

policy = Policy(rules=[Rule(id='notebooks-must-be-small',
                             match_extensions=['ipynb'],
                             size_over_kb=200, action='error')])
config = EvaluationConfig(max_text_size_kb=10)
blob = Blob(path='analysis.ipynb', blob_sha='a' * 40, commit_sha='b' * 40,
            status='A', size_bytes=50 * 1024, is_binary=False)

evaluate_blobs([blob], policy, config)  # -> [] (no violations)
```

If you want notebooks to *also* be subject to the general text-size limit
once they're under the rule's own gate, you need to express that within the
rule itself (e.g. lower `size_over_kb`, or drop the rule and rely on
`disallow`/`thresholds` instead) — there is no "fall through if the rule
didn't fire" behavior.

A rule that simply **doesn't match** a file (its `match` criteria don't hit
at all) behaves as you'd expect: evaluation moves on to the next rule, then
to `disallow`, then to thresholds, normally.

## Glob semantics

**This is the #1 thing users get wrong**, because it deliberately does *not*
behave like `.gitignore`, shell globbing, or Python's `fnmatch`. Patterns are
hand-translated to an anchored regular expression
(`repo_size_guardian/rule_engine.py::_translate_glob`) and matched against the
**full repo-relative POSIX path**, anchored at both the start and the end.

Key rules:

- **`*` matches a run of characters, but never crosses `/`.** It's scoped to
  one path segment.
- **There is no implicit basename matching.** A pattern with no `/` in it
  only matches a path with no `/` in it either — i.e. a **top-level** file.
  `*.md` matches `README.md` but does **not** match `docs/README.md`. If you
  want "any `.md` file at any depth," you need `**/*.md`, not `*.md`. This is
  the single most likely mistake — many other glob tools (`.gitignore`,
  `find -name`) match a bare pattern against the basename at any depth; this
  engine does not.
- **`**` as a whole path segment spans zero or more segments, including the
  `/` separators.** `docs/**` matches `docs` itself, `docs/a.txt`, and
  `docs/x/y/a.txt`. A leading `**/` matches zero or more leading segments
  (`**/*.md` matches both `a.md` and `x/y/a.md`). A bare `**` matches
  anything.
- **A pattern ending in `/` is shorthand for `<pattern>/**`.** `docs/`
  behaves exactly like `docs/**`.
- **`?` matches exactly one character, never `/`.**
- **`[abc]` / `[!abc]`** character classes and ranges (e.g. `[0-9]`) are
  supported; `!` negates. An unclosed `[` is treated as a literal character.
- **All other characters are literal.** Regex metacharacters (`.`, `+`, `(`,
  `$`, etc.) in your pattern are escaped automatically — `a.txt` matches only
  the literal string `a.txt`, not `axtxt`.
- **Matching is case-sensitive.** `docs/**` does not match `Docs/a.txt` or
  `DOCS/A.TXT`.

### Pattern reference table

| Pattern | Path | Matches? | Why |
|---|---|:---:|---|
| `*.md` | `a.md` | Yes | `*` matches within one top-level segment |
| `*.md` | `docs/a.md` | **No** | `*` doesn't cross `/`; no implicit basename matching |
| `**/*.md` | `a.md` | Yes | leading `**/` also matches zero segments |
| `**/*.md` | `x/y/a.md` | Yes | leading `**/` matches any depth |
| `docs/**` | `docs` | Yes | trailing `**` matches zero segments too — the base itself matches |
| `docs/**` | `docs/a.txt` | Yes | one segment |
| `docs/**` | `docs/x/y/a.txt` | Yes | many segments |
| `docs/**` | `docsx/a.txt` | **No** | anchored on the `/` boundary; `docsx` ≠ `docs` |
| `docs/` | `docs`, `docs/a.txt`, `docs/x/y.txt` | Yes (all) | trailing `/` behaves as `docs/**` |
| `foo/*.txt` | `foo/bar.txt` | Yes | `*` stays within the `foo/` segment |
| `foo/*.txt` | `foo/baz/bar.txt` | **No** | `*` can't cross into `baz/` |
| `a/**/b` | `a/b` | Yes | middle `**` matches zero segments |
| `a/**/b` | `a/x/b` | Yes | one segment |
| `a/**/b` | `a/x/y/z/b` | Yes | many segments |
| `a/**/b` | `a/b/c` | **No** | pattern is fully anchored; no trailing suffix allowed |
| `**` | any path | Yes | matches everything |
| `file[0-9].txt` | `file5.txt` | Yes | character range |
| `file[!0-9].txt` | `fileA.txt` | Yes | negated class |
| `a?b.txt` | `axb.txt` | Yes | `?` = exactly one non-`/` character |
| `a?b.txt` | `a/b.txt` | **No** | `?` never matches `/` |

Every row above is exercised by `tests/test_rule_engine.py` and was
independently re-verified against `matches_path()` while writing this doc.

## Extension matching

`matches_extension()` looks at everything after the **last** `.` in the
path's basename (directories are stripped first).

- Matching is **case-insensitive** on both sides, and a leading `.` on an
  entry in your list is optional and ignored: `"ipynb"`, `".ipynb"`, and
  `"IPYNB"` are all equivalent, and all match `notebook.ipynb`.
- **Last-dot-wins.** `archive.tar.gz` has extension `gz`, not `tar.gz` and
  not `tar`. To catch `.tar.gz` specifically, disallow `gz` (which also
  catches any other `.gz`), or use a glob (`**/*.tar.gz`) instead.
- **Dotfiles with no further dot have no extension.** `.gitignore` has no
  extension and will never match any extension list (not even `["gitignore"]`
  or `[""]`). `.gitignore.bak` *does* have an extension: `bak`.
  A file with no `.` at all (`Makefile`) likewise has no extension.
- A trailing dot (`file.`) also has no extension (the part after the last
  `.` is empty).

## MIME matching

`matches_mime()` compares the blob's detected MIME type (from the `file
--mime` command, with a content-heuristic fallback) against your list.

- Matching is **case-insensitive**.
- An entry ending in `/*` matches any subtype of that type:
  `application/*` matches `application/zip`, `application/x-executable`,
  etc., but not `text/plain`.
- Anything else is an **exact** match (`application/x-dosexec` only matches
  that literal string).
- A blob whose MIME type couldn't be detected (`None`) never matches
  anything, including a `/*` wildcard.

### ⚠️ MIME matching requires the `file` command

A MIME type is only ever produced by running the `file --mime` command on
the blob's content. If `file` is not installed on the runner, MIME
detection falls back to a content heuristic that never reports a MIME type
at all — so every blob's MIME type is `None`, and per the rule above,
`None` never matches anything. **In that situation, `disallow.mime_types`
and every `match.mime_types` rule silently match nothing, and a policy that
looks like it's enforcing MIME-based rules is actually enforcing none of
them.** The scan still exits `0` on a "clean" PR — there is no way to
distinguish that from a genuinely clean PR by exit code alone.

GitHub-hosted runners (`ubuntu-latest`, etc.) have `file` preinstalled, so
this mostly affects self-hosted and minimal container runners.
repo-size-guardian detects this itself: if the loaded policy uses
`mime_types` anywhere and `file` is not on `PATH`, it prints an
`::warning::` naming the problem. That warning is easy to miss in a long
job log, though, so if you rely on `mime_types` matching, either confirm
`file` is present on your runner (`apt-get install -y file` on Debian/
Ubuntu-based images), or prefer `extensions`/`globs` matching, which has no
such dependency.

## Rule match combination

Within a single rule's `match` block:

- `globs`, `extensions`, and `mime_types` are combined with **OR**: the
  content criterion is satisfied if the path matches *any* populated one of
  the three lists.
- **If none of `globs`, `extensions`, or `mime_types` are set at all**, the
  content criterion is unconstrained — the rule matches **every** path. This
  is intentional, not a bug, and it's exactly what lets you write a rule
  based purely on size and binary-ness:

  ```yaml
  rules:
    - id: large-binaries
      description: "Block any binary file over 100 KB"
      match:
        binary: true
      size_over_kb: 100
      action: error
  ```

  With no `globs`/`extensions`/`mime_types`, every path passes the content
  check; `match.binary: true` then filters that down to binary files only.

- `match.binary`, if set (`true` or `false`), is applied **after** the
  content check, as an **AND** filter on the blob's detected `is_binary`.
  It's independent of the content criteria — you can combine
  `match.extensions: ["bin"]` with `match.binary: true` to mean "files named
  `*.bin` that were *also* detected as binary content."
- If `match` is omitted entirely (or is an empty mapping `{}`), the rule
  matches every file, exactly as if only `binary`/`size_over_kb` had been
  set with no content filters.

## Validation errors

A policy file is validated as a whole before any file is scanned. Any of the
following is a **hard error**: the run prints a message naming the file and
the specific problem, and exits with **code 2** (the same "configuration
error" bucket as a shallow clone or an unresolvable ref) — never a silent
no-op and never a violation-style exit code 1.

- An unknown key anywhere: at the top level, inside `ignore`/`disallow`/
  `thresholds`/`overrides`, inside a rule, or inside a rule's `match`. The
  error names the offending key and lists the accepted ones.
- The wrong type for any field — e.g. a string where a list of strings is
  expected, a list where a mapping is expected, a non-boolean for
  `match.binary`.
- A rule missing `id`, or with an empty or non-string `id`.
- **Duplicate rule `id` values** anywhere in `rules`.
- An `action` value other than `"warn"` or `"error"`.
- A **negative** size anywhere (`size_over_kb`, `max_text_size_kb`,
  `max_binary_size_kb`). `0` is valid (a zero-KB threshold, which every
  non-empty file exceeds).
- A non-mapping document at the top level (e.g. a YAML list or a bare
  string/number).
- Syntactically invalid YAML.

What is **not** an error:

- No policy file at the configured `policy_path` — the action just runs
  without one.
- An empty file, or a file containing only `null` — treated as an empty
  policy.
- Explicitly setting a section to `null` (e.g. `ignore: null`) — treated as
  if the key were absent.

If your policy configures nothing at all (no `ignore`/`disallow`/`rules`/
`overrides.allow_globs` entries and no thresholds) **and** neither
`max_text_size_kb` nor `max_binary_size_kb` was set as an action input, the
action still runs and exits `0`, but it prints a prominent
`::warning::`-level message pointing out that nothing was actually enforced.
A "clean" result in that state means nothing was checked, not that your repo
is fine.
