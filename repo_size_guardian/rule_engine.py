"""
Policy rule engine for repo-size-guardian.

Loads and validates the optional YAML policy file -- a top-level mapping
whose only key is `rules`, an ordered list of rules -- and provides the
glob/extension/MIME/size matching primitives used to decide, for a given
`Blob`, which rules match it.

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
from pathspec import GitIgnoreSpec

from .formatting import format_number
from .models import Blob

# ---------------------------------------------------------------------------
# Schema constants (used both for validation and for building "accepted
# keys" hints in error messages).
# ---------------------------------------------------------------------------

_TOP_LEVEL_KEYS = frozenset({'rules'})
_RULE_KEYS = frozenset({'id', 'description', 'match', 'action'})
_RULE_MATCH_KEYS = frozenset({'globs', 'extensions', 'mime_types', 'binary', 'size'})
_VALID_ACTIONS = frozenset({'warn', 'error', 'stop'})

#: Grammar: optional whitespace, an operator, optional whitespace, a
#: non-negative integer or decimal, optional whitespace, a unit, optional
#: whitespace. Case-insensitive. The two-character operators are listed
#: before their one-character prefixes so a scanner trying alternatives in
#: order matches ">=" before falling back to ">".
_SIZE_CONDITION_RE = re.compile(
    r'^\s*(?P<op>>=|<=|>|<)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>B|KB|MB|GB)\s*$',
    re.IGNORECASE,
)
_UNIT_MULTIPLIERS = {'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3}

#: PyYAML `context` values that mean a scanner error came from an unquoted
#: value starting with a character YAML treats specially: `>`/`|` (block
#: scalar indicators, what an unquoted size like `>500KB` produces) or `*`
#: (alias indicator, what an unquoted glob like `*.md` produces).
_QUOTING_HINT_CONTEXTS = frozenset({
    'while scanning a block scalar',
    'while scanning an alias',
})
#: Start of the PyYAML `problem` for an unknown tag, what an unquoted glob
#: like `!keep.log` (a gitignore exclusion) produces.
_QUOTING_HINT_TAG_PROBLEM = 'could not determine a constructor for the tag'
_QUOTING_HINT = (
    ' Put quotes around size values and around globs that start with \'*\', '
    '\'!\' or \'#\', e.g. size: ">500KB" or globs: ["*.md"].'
)


class PolicyError(Exception):
    """Raised when a policy file is malformed: bad YAML, wrong types, or unknown keys."""


@dataclass(frozen=True)
class SizeCondition:
    """
    A parsed `match.size` condition, e.g. `">500KB"` or `"<=6MB"`.

    Attributes:
        operator: One of `'>'`, `'>='`, `'<'`, `'<='`.
        value: The numeric value as written (e.g. `500`, `1.5`, `0`).
        unit: One of `'B'`, `'KB'`, `'MB'`, `'GB'` (canonical uppercase,
            regardless of how it was cased in the policy file).
    """
    operator: str
    value: float
    unit: str

    @property
    def threshold_bytes(self) -> float:
        """The condition's threshold, converted to bytes."""
        return self.value * _UNIT_MULTIPLIERS[self.unit]

    def holds(self, size_bytes: Optional[int]) -> bool:
        """
        Check whether `size_bytes` satisfies this condition.

        An unknown size (`None`) never satisfies a size condition -- the
        caller (see `rule_matches`) is expected to treat that as "could not
        evaluate this rule's size condition" rather than "did not match".

        Args:
            size_bytes: The file version's size, or None if unknown.

        Returns:
            True if `size_bytes` is not None and satisfies the condition.
        """
        if size_bytes is None:
            return False
        threshold = self.threshold_bytes
        if self.operator == '>':
            return size_bytes > threshold
        if self.operator == '>=':
            return size_bytes >= threshold
        if self.operator == '<':
            return size_bytes < threshold
        return size_bytes <= threshold  # '<='

    def render_threshold(self) -> str:
        """Render the threshold the way it was written, e.g. `"50 KB"`."""
        return '{0} {1}'.format(format_number(self.value), self.unit)


def parse_size_condition(value: Any, context: str) -> SizeCondition:
    """
    Parse a policy `match.size` value into a `SizeCondition`.

    Args:
        value: The raw YAML value; only a string matching the size grammar
            (see the module's `_SIZE_CONDITION_RE`) is accepted.
        context: Description of where this value came from, used verbatim
            in the error message (e.g. `"'rules[2]'.match.size"`).

    Returns:
        The parsed condition.

    Raises:
        PolicyError: If `value` is not a string, or is a string that does
            not match the grammar (missing operator/unit, `=`/`==`, more
            than one condition, ...).
    """
    if isinstance(value, str):
        match = _SIZE_CONDITION_RE.match(value)
        if match:
            return SizeCondition(
                operator=match.group('op'),
                value=float(match.group('value')),
                unit=match.group('unit').upper(),
            )
    raise PolicyError(
        '{0} must be one condition such as ">500KB" or "<=6MB", got {1!r}'.format(context, value)
    )


@dataclass
class Rule:
    """
    A single rule: either a user-defined entry of the policy's `rules`, or
    one of the three quick-start inputs acting as a rule (see
    `main._quick_start_rules`).

    Attributes:
        name: The rule's name in reports: its `id` if it set one, else
            `rules[N]` (0-based, matching the convention validation
            messages already use). Always set.
        id: The rule's own `id`, if it set one; None for an anonymous
            policy rule. Always set (equal to `name`) for a quick-start
            rule.
        description: Free-form text shown in reports in place of the
            default `"Matched rule '<name>'"` message. Unused by a
            quick-start rule, which has its own fixed message.
        match_globs / match_extensions / match_mime_types: A blob matches
            on one of these keys if its path/extension/MIME type matches
            any entry (OR within the list). An empty list imposes no
            condition.
        match_binary: If not None, an additional condition requiring
            `blob.is_binary` to equal this value (`False` also matches an
            undetermined type -- see `_binary_condition_holds`).
        match_size: If not None, an additional condition on the blob's size.
        action: What happens when every condition holds: `'warn'` or
            `'error'` records a hit and evaluation continues to the next
            rule; `'stop'` ends evaluation for this file version, keeping
            whatever hits were already recorded.
        is_input_rule: True for a rule synthesized from a quick-start input
            rather than parsed from a policy file; affects only report
            wording (e.g. "the `<name>` input" rather than "the policy rule
            `<name>`").
    """
    name: str
    id: Optional[str] = None
    description: str = ""
    match_globs: List[str] = field(default_factory=list)
    match_extensions: List[str] = field(default_factory=list)
    match_mime_types: List[str] = field(default_factory=list)
    match_binary: Optional[bool] = None
    match_size: Optional[SizeCondition] = None
    action: str = "error"
    is_input_rule: bool = False


@dataclass
class Policy:
    """A fully parsed and validated policy: an ordered list of rules."""
    rules: List[Rule] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "Policy":
        """Return a Policy with no rules."""
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
                key, or `rules` contains an invalid entry (see `_parse_rule`).
        """
        if data is None:
            return cls.empty()
        if not isinstance(data, dict):
            raise PolicyError(
                f"The policy file must contain a mapping at the top level, "
                f"got {_type_name(data)}"
            )
        _check_known_keys(data, _TOP_LEVEL_KEYS, "the top level of the policy file")

        rules_data = data.get('rules')
        if rules_data is None:
            return cls.empty()
        if not isinstance(rules_data, list):
            raise PolicyError(f"'rules' must be a list, got {_type_name(rules_data)}")

        rules: List[Rule] = []
        seen_ids = set()
        for index, rule_data in enumerate(rules_data):
            rule = _parse_rule(rule_data, index)
            if rule.id is not None:
                if rule.id in seen_ids:
                    raise PolicyError(
                        f"Duplicate rule id {rule.id!r} in 'rules' "
                        f"(rule ids must be unique)"
                    )
                seen_ids.add(rule.id)
            rules.append(rule)
        return cls(rules=rules)

    def is_empty(self) -> bool:
        """Return True when this policy has no rules at all."""
        return not self.rules


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
        - the file exists, but is empty, contains only `null`, or sets
          `rules` to `null`/`[]`: `(Policy.empty(), True)`.
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
        message = f"Policy file '{path}' contains invalid YAML: {exc}"
        problem = getattr(exc, 'problem', None) or ''
        if (getattr(exc, 'context', None) in _QUOTING_HINT_CONTEXTS
                or problem.startswith(_QUOTING_HINT_TAG_PROBLEM)):
            message += _QUOTING_HINT
        raise PolicyError(message) from exc

    try:
        policy = Policy.from_dict(data)
    except PolicyError as exc:
        raise PolicyError(f"Policy file '{path}' is invalid: {exc}") from exc

    return policy, True


# ---------------------------------------------------------------------------
# Glob matching
# ---------------------------------------------------------------------------

def matches_path(path: str, patterns: Sequence[str]) -> bool:
    """
    Check whether `path` matches `patterns`.

    `patterns` are read as the lines of a `.gitignore` file, with the same
    syntax and semantics: a pattern with a `/` at the start or in the middle
    is anchored to the repository root, any other pattern matches at any
    depth, a pattern matching a folder also matches everything in it, a
    later `!pattern` excludes paths an earlier pattern matched, and a
    pattern starting with `#` is a comment. Matching is case-sensitive.

    Args:
        path: Repo-relative POSIX path to test.
        patterns: Glob patterns.

    Returns:
        True if `patterns` match `path`.
    """
    return _compile_patterns(tuple(patterns)).match_file(path)


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
    extension = extension_of(path)
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


def _binary_condition_holds(actual: Optional[bool], expected: bool) -> bool:
    """
    Check a `match.binary` condition.

    `expected=True` requires a definite `True`. `expected=False` accepts
    both a definite `False` and `None`: a file version whose type could not
    be determined is treated as text-like for this purpose, since the
    default (unmatched) case elsewhere in this package already does the
    same (e.g. the quick-start `max_text_size_kb` rule, whose `match.binary`
    is `False`).

    Args:
        actual: The blob's `is_binary` (`None` means undetermined).
        expected: The rule's `match.binary` value.

    Returns:
        True if the condition holds.
    """
    if expected:
        return actual is True
    return actual is not True


def rule_matches(rule: Rule, blob: Blob) -> Tuple[bool, bool]:
    """
    Check whether `rule` matches `blob`.

    Every condition present on `rule` must hold (AND across keys); a key
    with an empty list, or left at `None`, imposes no condition. Globs,
    extensions and MIME types each combine their own list with OR (see
    `matches_path`/`matches_extension`/`matches_mime`). Conditions are
    checked in a fixed order and short-circuit on the first one that fails,
    so a later condition is never even looked at once an earlier one has
    already ruled the rule out.

    Args:
        rule: The rule to test.
        blob: The file version to test.

    Returns:
        A `(matches, size_unknown)` tuple. `size_unknown` is True exactly
        when every other condition on `rule` held, `rule.match_size` is
        set, and `blob.size_bytes` is None: this rule's size condition
        could not be evaluated at all, which is what triggers the
        "could not read the size" warning (see `evaluator.py`).
    """
    if rule.match_globs and not matches_path(blob.path, rule.match_globs):
        return False, False
    if rule.match_extensions and not matches_extension(blob.path, rule.match_extensions):
        return False, False
    if rule.match_mime_types and not matches_mime(blob.mime_type, rule.match_mime_types):
        return False, False
    if rule.match_binary is not None and not _binary_condition_holds(blob.is_binary, rule.match_binary):
        return False, False
    if rule.match_size is not None:
        if blob.size_bytes is None:
            return False, True
        if not rule.match_size.holds(blob.size_bytes):
            return False, False
    return True, False


@lru_cache(maxsize=None)
def _compile_patterns(patterns: Tuple[str, ...]) -> GitIgnoreSpec:
    """Compile a list of glob patterns, memoized since policies re-use them."""
    return GitIgnoreSpec.from_lines(patterns)


def extension_of(path: str) -> Optional[str]:
    """Return the case-preserved extension of `path`'s basename, or None."""
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
        if item is None:
            raise PolicyError(
                f"{context} must be a list of strings, but found an empty entry; "
                f"a pattern that starts with '#' must be in quotes"
            )
        if not isinstance(item, str):
            raise PolicyError(
                f"{context} must be a list of strings, but found {_type_name(item)} ({item!r})"
            )
    return list(value)


def _parse_rule(rule_data: Any, index: int) -> Rule:
    """Validate and build a single `rules[]` entry."""
    context = f"'rules[{index}]'"
    if not isinstance(rule_data, dict):
        raise PolicyError(f"{context} must be a mapping, got {_type_name(rule_data)}")
    _check_known_keys(rule_data, _RULE_KEYS, context)

    raw_id = rule_data.get('id')
    if raw_id is not None and (not isinstance(raw_id, str) or not raw_id):
        raise PolicyError(f"{context}.id must be a non-empty string, got {raw_id!r}")
    name = raw_id if raw_id else f"rules[{index}]"

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
    for key in ('globs', 'extensions', 'mime_types'):
        if match.get(key) == []:
            raise PolicyError(f"{context}.match.{key} must list at least one entry")

    match_binary = match.get('binary')
    if match_binary is not None and not isinstance(match_binary, bool):
        raise PolicyError(
            f"{context}.match.binary must be true or false, got {_type_name(match_binary)}"
        )

    match_size = None
    if match.get('size') is not None:
        match_size = parse_size_condition(match['size'], f"{context}.match.size")

    if not (match_globs or match_extensions or match_mime_types
            or match_binary is not None or match_size is not None):
        raise PolicyError(
            f"{context}.match is required and must set at least one condition "
            f"(globs, extensions, mime_types, binary, or size)"
        )

    action = rule_data.get('action', 'error')
    if action not in _VALID_ACTIONS:
        accepted = ', '.join(sorted(_VALID_ACTIONS))
        raise PolicyError(f"{context}.action must be one of: {accepted}; got {action!r}")

    return Rule(
        name=name,
        id=raw_id,
        description=description,
        match_globs=match_globs,
        match_extensions=match_extensions,
        match_mime_types=match_mime_types,
        match_binary=match_binary,
        match_size=match_size,
        action=action,
    )
