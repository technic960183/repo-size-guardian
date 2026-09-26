"""
Policy rule engine for repo-size-guardian.

Loads and validates the optional YAML policy file (PRD 3.2), and provides the
glob/extension/MIME matching primitives used to decide, for a given `Blob`,
whether it is ignored, explicitly allowed, matched by a user-defined rule, or
left to the disallow lists / global size thresholds.

The policy file is entirely optional: a missing or empty file is not an
error, but a malformed one (bad YAML, wrong types, unknown keys) is, since a
silently-ignored typo in a policy file would defeat the whole point of the
tool.
"""

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from .models import Blob

# ---------------------------------------------------------------------------
# Schema constants (used both for validation and for building "accepted
# keys" hints in error messages).
# ---------------------------------------------------------------------------

_TOP_LEVEL_KEYS = frozenset({'ignore', 'disallow', 'thresholds', 'rules', 'overrides'})
_IGNORE_KEYS = frozenset({'globs', 'paths'})
_DISALLOW_KEYS = frozenset({'extensions', 'globs', 'mime_types'})
_THRESHOLDS_KEYS = frozenset({'max_text_size_kb', 'max_binary_size_kb'})
_OVERRIDES_KEYS = frozenset({'allow_globs'})
_RULE_KEYS = frozenset({'id', 'description', 'match', 'size_over_kb', 'action'})
_RULE_MATCH_KEYS = frozenset({'globs', 'extensions', 'mime_types', 'binary'})
_VALID_ACTIONS = frozenset({'warn', 'error'})


class PolicyError(Exception):
    """Raised when a policy file is malformed: bad YAML, wrong types, or unknown keys."""


@dataclass
class Rule:
    """
    A single user-defined policy rule (PRD 3.2 `rules[]`).

    Attributes:
        id: Unique rule identifier. Required, non-empty.
        description: Free-form human-readable description.
        match_globs: Path globs; a blob matches if its path matches any of these.
        match_extensions: File extensions (with or without leading dot); a
            blob matches if its extension matches any of these.
        match_mime_types: MIME types (subtype `*` wildcard allowed); a blob
            matches if its detected MIME type matches any of these.
        match_binary: If not None, an additional filter requiring
            `blob.is_binary` to equal this value.
        size_over_kb: If not None, the rule only produces a violation when
            the blob's size in KB is strictly greater than this value. If
            None, a content-match is unconditionally a violation.
        action: Severity to report when this rule matches: 'warn' or 'error'.
    """
    id: str
    description: str = ""
    match_globs: List[str] = field(default_factory=list)
    match_extensions: List[str] = field(default_factory=list)
    match_mime_types: List[str] = field(default_factory=list)
    match_binary: Optional[bool] = None
    size_over_kb: Optional[float] = None
    action: str = "error"


@dataclass
class Policy:
    """
    A fully parsed and validated policy (PRD 3.2).

    See module docstring and `from_dict` for validation rules.
    """
    ignore_globs: List[str] = field(default_factory=list)
    ignore_paths: List[str] = field(default_factory=list)
    disallow_extensions: List[str] = field(default_factory=list)
    disallow_globs: List[str] = field(default_factory=list)
    disallow_mime_types: List[str] = field(default_factory=list)
    max_text_size_kb: Optional[float] = None
    max_binary_size_kb: Optional[float] = None
    rules: List[Rule] = field(default_factory=list)
    allow_globs: List[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "Policy":
        """Return a Policy that imposes nothing (all defaults)."""
        return cls()

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Policy":
        """
        Build and validate a Policy from a parsed-YAML mapping.

        Args:
            data: The result of `yaml.safe_load` on a policy file. `None`
                (an empty file, or a file containing only `null`) yields an
                empty policy.

        Returns:
            A validated Policy.

        Raises:
            PolicyError: If `data` is not a mapping, contains an unknown
                top-level or nested key, has a field of the wrong type, or
                contains an invalid rule (missing/duplicate id, bad action,
                negative size).
        """
        if data is None:
            return cls.empty()
        if not isinstance(data, dict):
            raise PolicyError(
                f"The policy file must contain a mapping at the top level, "
                f"got {_type_name(data)}"
            )
        _check_known_keys(data, _TOP_LEVEL_KEYS, "the top level of the policy file")

        policy = cls.empty()

        ignore = _expect_mapping_or_none(data.get('ignore'), 'ignore')
        if ignore is not None:
            _check_known_keys(ignore, _IGNORE_KEYS, "'ignore'")
            policy.ignore_globs = _expect_str_list(ignore.get('globs'), 'ignore.globs')
            policy.ignore_paths = _expect_str_list(ignore.get('paths'), 'ignore.paths')

        disallow = _expect_mapping_or_none(data.get('disallow'), 'disallow')
        if disallow is not None:
            _check_known_keys(disallow, _DISALLOW_KEYS, "'disallow'")
            policy.disallow_extensions = _expect_str_list(
                disallow.get('extensions'), 'disallow.extensions')
            policy.disallow_globs = _expect_str_list(disallow.get('globs'), 'disallow.globs')
            policy.disallow_mime_types = _expect_str_list(
                disallow.get('mime_types'), 'disallow.mime_types')

        thresholds = _expect_mapping_or_none(data.get('thresholds'), 'thresholds')
        if thresholds is not None:
            _check_known_keys(thresholds, _THRESHOLDS_KEYS, "'thresholds'")
            policy.max_text_size_kb = _expect_nonneg_number_or_none(
                thresholds.get('max_text_size_kb'), 'thresholds.max_text_size_kb')
            policy.max_binary_size_kb = _expect_nonneg_number_or_none(
                thresholds.get('max_binary_size_kb'), 'thresholds.max_binary_size_kb')

        overrides = _expect_mapping_or_none(data.get('overrides'), 'overrides')
        if overrides is not None:
            _check_known_keys(overrides, _OVERRIDES_KEYS, "'overrides'")
            policy.allow_globs = _expect_str_list(
                overrides.get('allow_globs'), 'overrides.allow_globs')

        rules_data = data.get('rules')
        if rules_data is not None:
            if not isinstance(rules_data, list):
                raise PolicyError(
                    f"'rules' must be a list, got {_type_name(rules_data)}"
                )
            rules: List[Rule] = []
            seen_ids = set()
            for index, rule_data in enumerate(rules_data):
                rule = _parse_rule(rule_data, index)
                if rule.id in seen_ids:
                    raise PolicyError(
                        f"Duplicate rule id {rule.id!r} in 'rules' "
                        f"(rule ids must be unique)"
                    )
                seen_ids.add(rule.id)
                rules.append(rule)
            policy.rules = rules

        return policy

    def is_empty(self) -> bool:
        """
        Return True when this policy imposes nothing at all.

        True iff there are no ignore/disallow/rules/allow_globs entries and
        both global size thresholds are None. Used to warn the user that
        "nothing is being enforced".
        """
        return (
            not self.ignore_globs
            and not self.ignore_paths
            and not self.disallow_extensions
            and not self.disallow_globs
            and not self.disallow_mime_types
            and not self.rules
            and not self.allow_globs
            and self.max_text_size_kb is None
            and self.max_binary_size_kb is None
        )


def load_policy(path: Optional[str]) -> Tuple[Policy, bool]:
    """
    Load and validate a policy file.

    Args:
        path: Path to the (optional) policy YAML file. May be None or empty.

    Returns:
        A `(policy, was_found)` tuple:
        - `path` is None/empty, or does not name an existing regular file:
          `(Policy.empty(), False)`. This is not an error -- the policy file
          is optional.
        - the file exists, but is empty or contains only `null`:
          `(Policy.empty(), True)`.
        - the file exists and parses to a valid policy: `(policy, True)`.

    Raises:
        PolicyError: If the file exists but cannot be read, is not valid
            YAML, or does not describe a valid policy. The message names
            `path` and the specific problem.
    """
    if not path or not os.path.isfile(path):
        return Policy.empty(), False

    try:
        with open(path, 'r', encoding='utf-8') as policy_file:
            data = yaml.safe_load(policy_file)
    except OSError as exc:
        raise PolicyError(f"Could not read policy file '{path}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"Policy file '{path}' contains invalid YAML: {exc}") from exc

    try:
        policy = Policy.from_dict(data)
    except PolicyError as exc:
        raise PolicyError(f"Policy file '{path}' is invalid: {exc}") from exc

    return policy, True


# ---------------------------------------------------------------------------
# Glob matching
#
# We deliberately do not use `fnmatch`: its `*` crosses `/`, which is wrong
# for repo-relative path patterns. Patterns are translated to an anchored
# regex by hand instead. See PRD 3.2 / contract for the exact semantics.
# ---------------------------------------------------------------------------

def matches_path(path: str, patterns: Sequence[str]) -> bool:
    """
    Check whether `path` matches any of `patterns`.

    Patterns are matched against the full repo-relative POSIX path, anchored
    at both ends (no implicit basename matching). Matching is case-sensitive.
    See `_translate_glob` for the supported glob syntax.

    Args:
        path: Repo-relative POSIX path to test.
        patterns: Glob patterns.

    Returns:
        True if `path` matches at least one pattern.
    """
    return any(_compile_glob(pattern).match(path) is not None for pattern in patterns)


def matches_extension(path: str, extensions: Sequence[str]) -> bool:
    """
    Check whether `path`'s extension is in `extensions`.

    A path's extension is everything after the last `.` in its basename; a
    basename with no `.`, or a dotfile with no further `.` (e.g.
    `.gitignore`), has no extension and never matches. Matching is
    case-insensitive, and a leading `.` on an entry in `extensions` is
    ignored (`"ipynb"`, `".ipynb"`, and `"IPYNB"` are equivalent).

    Args:
        path: Repo-relative path to test.
        extensions: Extensions to match against.

    Returns:
        True if `path` has an extension matching one of `extensions`.
    """
    extension = _extension_of(path)
    if extension is None:
        return False
    extension_lower = extension.lower()
    for candidate in extensions:
        normalized = candidate.lower()
        if normalized.startswith('.'):
            normalized = normalized[1:]
        if extension_lower == normalized:
            return True
    return False


def matches_mime(mime_type: Optional[str], mime_types: Sequence[str]) -> bool:
    """
    Check whether `mime_type` is in `mime_types`.

    Matching is case-insensitive. An entry ending in `/*` matches any
    subtype of that type (e.g. `application/*` matches
    `application/x-executable`).

    Args:
        mime_type: The blob's detected MIME type, if any.
        mime_types: MIME types (optionally with a `/*` wildcard subtype) to
            match against.

    Returns:
        True if `mime_type` is not None and matches one of `mime_types`.
    """
    if not mime_type:
        return False
    mime_lower = mime_type.lower()
    for candidate in mime_types:
        candidate_lower = candidate.lower()
        if candidate_lower.endswith('/*'):
            if mime_lower.startswith(candidate_lower[:-1]):
                return True
        elif mime_lower == candidate_lower:
            return True
    return False


def rule_matches(rule: Rule, blob: Blob) -> bool:
    """
    Check whether `rule` matches `blob`.

    A rule's `match_globs` / `match_extensions` / `match_mime_types` combine
    with OR: the blob matches on content if it matches any populated one of
    these three lists. If none of the three are populated at all, the
    content criterion is unconstrained (matches every blob) -- this is what
    lets a rule like "all binaries over 100 KB" be expressed with only
    `match_binary` and `size_over_kb` set. `match_binary`, if not None, is
    then applied as an additional AND filter on `blob.is_binary`.

    Args:
        rule: The rule to test.
        blob: The blob to test.

    Returns:
        True if the rule matches the blob.
    """
    has_content_filters = bool(
        rule.match_globs or rule.match_extensions or rule.match_mime_types
    )
    if has_content_filters:
        content_match = (
            (bool(rule.match_globs) and matches_path(blob.path, rule.match_globs))
            or (bool(rule.match_extensions) and matches_extension(blob.path, rule.match_extensions))
            or (bool(rule.match_mime_types) and matches_mime(blob.mime_type, rule.match_mime_types))
        )
        if not content_match:
            return False

    if rule.match_binary is not None and blob.is_binary != rule.match_binary:
        return False

    return True


def find_matching_rule(policy: Policy, blob: Blob) -> Optional[Rule]:
    """
    Find the first rule in `policy.rules` (declaration order) matching `blob`.

    Args:
        policy: The policy whose rules to search.
        blob: The blob to test.

    Returns:
        The first matching Rule, or None if no rule matches.
    """
    for rule in policy.rules:
        if rule_matches(rule, blob):
            return rule
    return None


@lru_cache(maxsize=None)
def _compile_glob(pattern: str) -> "re.Pattern":
    """Translate and compile a glob pattern, memoized since policies re-use patterns."""
    return re.compile(_translate_glob(pattern))


def _translate_glob(pattern: str) -> str:
    """
    Translate one glob pattern into an anchored regex pattern string.

    Rules (see contract / PRD 3.2):
    - `*` matches any run of characters except `/`.
    - `?` matches exactly one character except `/`.
    - `[abc]` / `[!abc]` character classes are passed through to the regex.
    - `**` as a whole path segment matches zero or more path segments,
      including the `/` separators (`docs/**` matches `docs`, `docs/a.txt`,
      and `docs/x/y/a.txt`; `**/*.md` matches `a.md` and `x/y/a.md`).
    - A pattern ending in `/` is treated as `<pattern>**`.
    - All other characters are matched literally (regex metacharacters are
      escaped).
    """
    if pattern.endswith('/'):
        pattern = pattern + '**'

    length = len(pattern)
    index = 0
    out: List[str] = []
    literal_buffer: List[str] = []

    def flush_literal() -> None:
        if literal_buffer:
            out.append(re.escape(''.join(literal_buffer)))
            literal_buffer.clear()

    while index < length:
        char = pattern[index]

        if char == '*' and pattern[index:index + 2] == '**':
            prev_is_boundary = index == 0 or pattern[index - 1] == '/'
            after = index + 2
            next_is_boundary = after == length or pattern[after] == '/'

            if prev_is_boundary and next_is_boundary:
                flush_literal()
                if index == 0 and after == length:
                    # The whole pattern is "**": matches anything.
                    out.append('.*')
                    index = after
                elif index == 0:
                    # "**/" at the start: zero or more leading segments.
                    out.append('(?:.*/)?')
                    index = after + 1  # also consume the following '/'
                elif after == length:
                    # "/**" at the end: zero or more trailing segments,
                    # including matching the base path with none at all.
                    if out and out[-1] == '/':
                        out.pop()
                    out.append('(?:/.*)?')
                    index = after
                else:
                    # "/**/"  in the middle: zero or more whole segments.
                    out.append('(?:.*/)?')
                    index = after + 1  # also consume the following '/'
                continue
            # Not a whole path segment on its own (e.g. "a**b"): fall
            # through and treat this '*' like a single-character wildcard;
            # the next iteration handles the following '*' the same way.

        if char == '*':
            flush_literal()
            out.append('[^/]*')
            index += 1
        elif char == '?':
            flush_literal()
            out.append('[^/]')
            index += 1
        elif char == '[':
            end = _find_char_class_end(pattern, index)
            if end is None:
                # No closing ']': treat '[' as a literal character.
                literal_buffer.append(char)
                index += 1
            else:
                flush_literal()
                class_body = pattern[index + 1:end]
                if class_body.startswith('!'):
                    class_body = '^' + class_body[1:]
                class_body = class_body.replace('\\', '\\\\')
                out.append('[' + class_body + ']')
                index = end + 1
        elif char == '/':
            flush_literal()
            out.append('/')
            index += 1
        else:
            literal_buffer.append(char)
            index += 1

    flush_literal()
    return '^' + ''.join(out) + '$'


def _find_char_class_end(pattern: str, start: int) -> Optional[int]:
    """
    Find the index of the `]` closing the character class opened at `start`.

    Handles the glob convention that a `]` appearing immediately (or
    immediately after a leading `!`) is a literal member of the class rather
    than its closing bracket.

    Returns:
        The index of the closing `]`, or None if the class is never closed.
    """
    length = len(pattern)
    cursor = start + 1
    if cursor < length and pattern[cursor] == '!':
        cursor += 1
    if cursor < length and pattern[cursor] == ']':
        cursor += 1
    while cursor < length and pattern[cursor] != ']':
        cursor += 1
    return cursor if cursor < length else None


def _extension_of(path: str) -> Optional[str]:
    """Return the lowercase-preserving extension of `path`'s basename, or None."""
    basename = path.rsplit('/', 1)[-1]
    dot_index = basename.rfind('.')
    if dot_index <= 0:
        # No dot at all, or a dotfile like ".gitignore" with no further dot.
        return None
    extension = basename[dot_index + 1:]
    return extension or None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _type_name(value: Any) -> str:
    """Human-readable type name for error messages (None -> 'null')."""
    if value is None:
        return 'null'
    return type(value).__name__


def _check_known_keys(mapping: Dict[str, Any], allowed: Iterable[str], context: str) -> None:
    """Raise PolicyError naming the offending key and the accepted keys, if any is unknown."""
    unknown = sorted(set(mapping.keys()) - set(allowed))
    if unknown:
        accepted = ', '.join(sorted(allowed))
        raise PolicyError(
            f"Unknown key {unknown[0]!r} in {context}; accepted keys are: {accepted}"
        )


def _expect_mapping_or_none(value: Any, context: str) -> Optional[Dict[str, Any]]:
    """Validate that `value` is a mapping or None (key absent / explicit null)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PolicyError(f"{context!r} must be a mapping, got {_type_name(value)}")
    return value


def _expect_str_list(value: Any, context: str) -> List[str]:
    """Validate that `value` is a list of strings (or None/absent -> [])."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise PolicyError(f"{context} must be a list of strings, got {_type_name(value)}")
    for item in value:
        if not isinstance(item, str):
            raise PolicyError(
                f"{context} must be a list of strings, but found {_type_name(item)} ({item!r})"
            )
    return list(value)


def _expect_nonneg_number_or_none(value: Any, context: str) -> Optional[float]:
    """Validate that `value` is a non-negative number, or None/absent."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{context} must be a non-negative number, got {_type_name(value)}")
    if value < 0:
        raise PolicyError(f"{context} must be non-negative, got {value}")
    return float(value)


def _parse_rule(rule_data: Any, index: int) -> Rule:
    """Validate and build a single `rules[]` entry."""
    context = f"'rules[{index}]'"
    if not isinstance(rule_data, dict):
        raise PolicyError(f"{context} must be a mapping, got {_type_name(rule_data)}")
    _check_known_keys(rule_data, _RULE_KEYS, context)

    rule_id = rule_data.get('id')
    if not isinstance(rule_id, str) or not rule_id:
        raise PolicyError(f"{context} is missing a required non-empty 'id' (string)")

    description = rule_data.get('description', "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise PolicyError(
            f"{context}.description must be a string, got {_type_name(description)}"
        )

    match = _expect_mapping_or_none(rule_data.get('match'), f"{context}.match") or {}
    _check_known_keys(match, _RULE_MATCH_KEYS, f"{context}.match")
    match_globs = _expect_str_list(match.get('globs'), f"{context}.match.globs")
    match_extensions = _expect_str_list(match.get('extensions'), f"{context}.match.extensions")
    match_mime_types = _expect_str_list(match.get('mime_types'), f"{context}.match.mime_types")

    match_binary = match.get('binary')
    if match_binary is not None and not isinstance(match_binary, bool):
        raise PolicyError(
            f"{context}.match.binary must be true or false, got {_type_name(match_binary)}"
        )

    size_over_kb = _expect_nonneg_number_or_none(
        rule_data.get('size_over_kb'), f"{context}.size_over_kb")

    action = rule_data.get('action', 'error')
    if action not in _VALID_ACTIONS:
        accepted = ', '.join(sorted(_VALID_ACTIONS))
        raise PolicyError(f"{context}.action must be one of: {accepted}; got {action!r}")

    return Rule(
        id=rule_id,
        description=description,
        match_globs=match_globs,
        match_extensions=match_extensions,
        match_mime_types=match_mime_types,
        match_binary=match_binary,
        size_over_kb=size_over_kb,
        action=action,
    )
