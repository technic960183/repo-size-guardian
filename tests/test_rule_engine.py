"""
Test suite for rule_engine module.

Covers the glob->regex translator, extension/MIME matching, rule matching
and rule precedence, policy schema validation (including every PolicyError
path), and the load_policy file-loading contract.
"""

import os
import shutil
import tempfile
import unittest

from repo_size_guardian.models import Blob
from repo_size_guardian.rule_engine import (
    Policy,
    PolicyError,
    Rule,
    find_matching_rule,
    load_policy,
    matches_extension,
    matches_mime,
    matches_path,
    rule_matches,
)


def make_blob(path, blob_sha='a' * 40, commit_sha='b' * 40, status='A',
              size_bytes=None, is_binary=None, mime_type=None):
    """Build a Blob for tests without needing a real git repo."""
    return Blob(
        path=path,
        blob_sha=blob_sha,
        commit_sha=commit_sha,
        status=status,
        size_bytes=size_bytes,
        is_binary=is_binary,
        mime_type=mime_type,
    )


# ---------------------------------------------------------------------------
# Glob semantics (matches_path)
# ---------------------------------------------------------------------------

class TestMatchesPathStar(unittest.TestCase):
    """`*` must match a run of characters but never cross a `/`."""

    def test_star_matches_simple_name(self):
        self.assertTrue(matches_path('a.md', ['*.md']))

    def test_star_does_not_cross_slash(self):
        self.assertFalse(matches_path('docs/a.md', ['*.md']))

    def test_star_matches_empty_run(self):
        self.assertTrue(matches_path('.md', ['*.md']))

    def test_star_matches_within_single_segment(self):
        self.assertTrue(matches_path('foo/bar.txt', ['foo/*.txt']))
        self.assertFalse(matches_path('foo/baz/bar.txt', ['foo/*.txt']))


class TestMatchesPathQuestion(unittest.TestCase):
    """`?` must match exactly one character, never `/`."""

    def test_question_matches_one_char(self):
        self.assertTrue(matches_path('a.txt', ['?.txt']))

    def test_question_does_not_match_two_chars(self):
        self.assertFalse(matches_path('ab.txt', ['?.txt']))

    def test_question_does_not_match_zero_chars(self):
        self.assertFalse(matches_path('.txt', ['?.txt']))

    def test_question_does_not_cross_slash(self):
        self.assertFalse(matches_path('a/b.txt', ['a?b.txt']))


class TestMatchesPathCharacterClass(unittest.TestCase):
    """`[abc]` / `[!abc]` character classes."""

    def test_positive_class_matches_member(self):
        self.assertTrue(matches_path('file1.txt', ['file[123].txt']))

    def test_positive_class_rejects_non_member(self):
        self.assertFalse(matches_path('file9.txt', ['file[123].txt']))

    def test_negated_class_rejects_member(self):
        self.assertFalse(matches_path('file1.txt', ['file[!123].txt']))

    def test_negated_class_matches_non_member(self):
        self.assertTrue(matches_path('file9.txt', ['file[!123].txt']))

    def test_range_class(self):
        self.assertTrue(matches_path('file5.txt', ['file[0-9].txt']))
        self.assertFalse(matches_path('fileA.txt', ['file[0-9].txt']))

    def test_unclosed_class_treated_as_literal(self):
        # "[" with no closing "]" should not raise; treated as a literal '['.
        self.assertTrue(matches_path('a[b.txt', ['a[b.txt']))


class TestMatchesPathGlobstar(unittest.TestCase):
    """`**` as a whole path segment spans zero or more segments, including `/`."""

    def test_dir_globstar_matches_direct_child(self):
        self.assertTrue(matches_path('docs/a.txt', ['docs/**']))

    def test_dir_globstar_matches_nested_child(self):
        self.assertTrue(matches_path('docs/x/y/a.txt', ['docs/**']))

    def test_dir_globstar_matches_base_itself(self):
        self.assertTrue(matches_path('docs', ['docs/**']))

    def test_dir_globstar_does_not_match_sibling(self):
        self.assertFalse(matches_path('other/a.txt', ['docs/**']))
        self.assertFalse(matches_path('docsx/a.txt', ['docs/**']))

    def test_leading_globstar_matches_top_level_file(self):
        self.assertTrue(matches_path('a.md', ['**/*.md']))

    def test_leading_globstar_matches_nested_file(self):
        self.assertTrue(matches_path('x/y/a.md', ['**/*.md']))

    def test_leading_globstar_rejects_wrong_extension(self):
        self.assertFalse(matches_path('x/y/a.txt', ['**/*.md']))

    def test_middle_globstar_matches_zero_segments(self):
        self.assertTrue(matches_path('a/b', ['a/**/b']))

    def test_middle_globstar_matches_one_segment(self):
        self.assertTrue(matches_path('a/x/b', ['a/**/b']))

    def test_middle_globstar_matches_many_segments(self):
        self.assertTrue(matches_path('a/x/y/z/b', ['a/**/b']))

    def test_middle_globstar_requires_prefix_and_suffix(self):
        self.assertFalse(matches_path('a/b/c', ['a/**/b']))
        self.assertFalse(matches_path('x/b', ['a/**/b']))

    def test_bare_globstar_matches_anything(self):
        self.assertTrue(matches_path('a.txt', ['**']))
        self.assertTrue(matches_path('a/b/c.txt', ['**']))

    def test_trailing_slash_pattern_behaves_like_globstar(self):
        # "docs/" is treated as "docs/**".
        self.assertTrue(matches_path('docs', ['docs/']))
        self.assertTrue(matches_path('docs/a.txt', ['docs/']))
        self.assertTrue(matches_path('docs/x/y.txt', ['docs/']))


class TestMatchesPathAnchoringAndEscaping(unittest.TestCase):
    """Full-path anchoring and literal-character escaping."""

    def test_no_implicit_basename_matching(self):
        self.assertFalse(matches_path('docs/a.md', ['*.md']))
        self.assertTrue(matches_path('a.md', ['*.md']))

    def test_literal_dot_is_escaped(self):
        # "a.txt" must not match "axtxt": '.' in the pattern is literal.
        self.assertFalse(matches_path('axtxt', ['a.txt']))
        self.assertTrue(matches_path('a.txt', ['a.txt']))

    def test_other_regex_metacharacters_are_escaped(self):
        for special_path in ['a+b.txt', 'a(b).txt', 'a$b.txt', 'a^b.txt']:
            with self.subTest(special_path=special_path):
                self.assertTrue(matches_path(special_path, [special_path]))
        self.assertFalse(matches_path('ab.txt', ['a+b.txt']))
        self.assertFalse(matches_path('ab).txt', ['a(b).txt']))

    def test_case_sensitive(self):
        self.assertTrue(matches_path('docs/a.txt', ['docs/**']))
        self.assertFalse(matches_path('Docs/a.txt', ['docs/**']))
        self.assertFalse(matches_path('DOCS/A.TXT', ['docs/**']))

    def test_no_match_against_empty_pattern_list(self):
        self.assertFalse(matches_path('a.txt', []))

    def test_first_match_of_several_patterns(self):
        self.assertTrue(matches_path('a.secret', ['*.md', '*.secret', '*.exe']))


# ---------------------------------------------------------------------------
# Extension matching
# ---------------------------------------------------------------------------

class TestMatchesExtension(unittest.TestCase):

    def test_bare_name_matches(self):
        self.assertTrue(matches_extension('notebook.ipynb', ['ipynb']))

    def test_dotted_name_matches(self):
        self.assertTrue(matches_extension('notebook.ipynb', ['.ipynb']))

    def test_case_insensitive_on_both_sides(self):
        self.assertTrue(matches_extension('notebook.IPYNB', ['ipynb']))
        self.assertTrue(matches_extension('notebook.ipynb', ['IPYNB']))

    def test_no_extension_never_matches(self):
        self.assertFalse(matches_extension('Makefile', ['txt']))

    def test_dotfile_with_no_further_dot_has_no_extension(self):
        self.assertFalse(matches_extension('.gitignore', ['gitignore']))

    def test_dotfile_with_further_dot_has_extension(self):
        self.assertTrue(matches_extension('.gitignore.bak', ['bak']))

    def test_multiple_dots_uses_last_segment(self):
        self.assertTrue(matches_extension('archive.tar.gz', ['gz']))
        self.assertFalse(matches_extension('archive.tar.gz', ['tar']))

    def test_trailing_dot_has_no_extension(self):
        self.assertFalse(matches_extension('file.', ['']))

    def test_path_with_directories_uses_basename(self):
        self.assertTrue(matches_extension('a/b/c.ipynb', ['ipynb']))

    def test_no_match_when_not_listed(self):
        self.assertFalse(matches_extension('a.py', ['ipynb', 'exe']))


# ---------------------------------------------------------------------------
# MIME matching
# ---------------------------------------------------------------------------

class TestMatchesMime(unittest.TestCase):

    def test_exact_match(self):
        self.assertTrue(matches_mime('application/x-dosexec', ['application/x-dosexec']))

    def test_case_insensitive(self):
        self.assertTrue(matches_mime('Application/X-Dosexec', ['application/x-dosexec']))
        self.assertTrue(matches_mime('application/x-dosexec', ['APPLICATION/X-DOSEXEC']))

    def test_subtype_wildcard(self):
        self.assertTrue(matches_mime('application/x-executable', ['application/*']))
        self.assertTrue(matches_mime('application/zip', ['application/*']))

    def test_wildcard_does_not_match_other_type(self):
        self.assertFalse(matches_mime('text/plain', ['application/*']))

    def test_none_mime_type_never_matches(self):
        self.assertFalse(matches_mime(None, ['application/*']))

    def test_empty_mime_type_never_matches(self):
        self.assertFalse(matches_mime('', ['application/*']))

    def test_no_match_when_not_listed(self):
        self.assertFalse(matches_mime('text/plain', ['application/x-dosexec']))


# ---------------------------------------------------------------------------
# rule_matches / find_matching_rule
# ---------------------------------------------------------------------------

class TestRuleMatches(unittest.TestCase):

    def test_glob_only_rule_matches(self):
        rule = Rule(id='r', match_globs=['**/*.secret'])
        self.assertTrue(rule_matches(rule, make_blob('a/b.secret')))
        self.assertFalse(rule_matches(rule, make_blob('a/b.txt')))

    def test_extension_only_rule_matches(self):
        rule = Rule(id='r', match_extensions=['ipynb'])
        self.assertTrue(rule_matches(rule, make_blob('notebook.ipynb')))
        self.assertFalse(rule_matches(rule, make_blob('notebook.py')))

    def test_mime_only_rule_matches(self):
        rule = Rule(id='r', match_mime_types=['application/*'])
        self.assertTrue(rule_matches(rule, make_blob('a.bin', mime_type='application/zip')))
        self.assertFalse(rule_matches(rule, make_blob('a.bin', mime_type='text/plain')))

    def test_content_criteria_combine_with_or(self):
        rule = Rule(id='r', match_globs=['**/*.secret'], match_extensions=['exe'])
        self.assertTrue(rule_matches(rule, make_blob('a.secret')))
        self.assertTrue(rule_matches(rule, make_blob('a.exe')))
        self.assertFalse(rule_matches(rule, make_blob('a.txt')))

    def test_no_content_filters_matches_every_path(self):
        rule = Rule(id='r', match_binary=True)
        self.assertTrue(rule_matches(rule, make_blob('anything.bin', is_binary=True)))
        self.assertTrue(rule_matches(rule, make_blob('other/thing.dat', is_binary=True)))

    def test_binary_filter_true_excludes_text(self):
        rule = Rule(id='r', match_binary=True)
        self.assertFalse(rule_matches(rule, make_blob('a.bin', is_binary=False)))

    def test_binary_filter_false_excludes_binary(self):
        rule = Rule(id='r', match_binary=False)
        self.assertFalse(rule_matches(rule, make_blob('a.bin', is_binary=True)))
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=False)))

    def test_binary_filter_is_and_with_content_match(self):
        rule = Rule(id='r', match_extensions=['bin'], match_binary=True)
        self.assertTrue(rule_matches(rule, make_blob('a.bin', is_binary=True)))
        self.assertFalse(rule_matches(rule, make_blob('a.bin', is_binary=False)))
        self.assertFalse(rule_matches(rule, make_blob('a.txt', is_binary=True)))

    def test_no_filters_at_all_matches_everything(self):
        rule = Rule(id='r')
        self.assertTrue(rule_matches(rule, make_blob('anything')))

    def test_binary_none_means_no_filter(self):
        rule = Rule(id='r', match_extensions=['txt'], match_binary=None)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=True)))
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=False)))
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=None)))


class TestFindMatchingRule(unittest.TestCase):

    def test_returns_none_when_no_rules(self):
        policy = Policy.empty()
        self.assertIsNone(find_matching_rule(policy, make_blob('a.txt')))

    def test_returns_none_when_nothing_matches(self):
        policy = Policy(rules=[Rule(id='r1', match_extensions=['ipynb'])])
        self.assertIsNone(find_matching_rule(policy, make_blob('a.txt')))

    def test_first_match_wins_in_declaration_order(self):
        rule_a = Rule(id='a', match_extensions=['bin'])
        rule_b = Rule(id='b', match_extensions=['bin'])
        policy = Policy(rules=[rule_a, rule_b])
        matched = find_matching_rule(policy, make_blob('x.bin'))
        self.assertIs(matched, rule_a)

    def test_skips_non_matching_earlier_rules(self):
        rule_a = Rule(id='a', match_extensions=['ipynb'])
        rule_b = Rule(id='b', match_extensions=['bin'])
        policy = Policy(rules=[rule_a, rule_b])
        matched = find_matching_rule(policy, make_blob('x.bin'))
        self.assertIs(matched, rule_b)


# ---------------------------------------------------------------------------
# Policy.is_empty
# ---------------------------------------------------------------------------

class TestPolicyIsEmpty(unittest.TestCase):

    def test_default_policy_is_empty(self):
        self.assertTrue(Policy.empty().is_empty())

    def test_ignore_globs_makes_non_empty(self):
        self.assertFalse(Policy(ignore_globs=['docs/**']).is_empty())

    def test_ignore_paths_makes_non_empty(self):
        self.assertFalse(Policy(ignore_paths=['a.txt']).is_empty())

    def test_disallow_extensions_makes_non_empty(self):
        self.assertFalse(Policy(disallow_extensions=['exe']).is_empty())

    def test_disallow_globs_makes_non_empty(self):
        self.assertFalse(Policy(disallow_globs=['**/*.secret']).is_empty())

    def test_disallow_mime_types_makes_non_empty(self):
        self.assertFalse(Policy(disallow_mime_types=['application/x-dosexec']).is_empty())

    def test_max_text_size_kb_makes_non_empty(self):
        self.assertFalse(Policy(max_text_size_kb=500).is_empty())

    def test_max_binary_size_kb_makes_non_empty(self):
        self.assertFalse(Policy(max_binary_size_kb=100).is_empty())

    def test_rules_makes_non_empty(self):
        self.assertFalse(Policy(rules=[Rule(id='r')]).is_empty())

    def test_allow_globs_makes_non_empty(self):
        self.assertFalse(Policy(allow_globs=['docs/**']).is_empty())


# ---------------------------------------------------------------------------
# Policy.from_dict: valid inputs
# ---------------------------------------------------------------------------

class TestPolicyFromDictValid(unittest.TestCase):

    def test_none_yields_empty_policy(self):
        policy = Policy.from_dict(None)
        self.assertTrue(policy.is_empty())

    def test_empty_dict_yields_empty_policy(self):
        policy = Policy.from_dict({})
        self.assertTrue(policy.is_empty())

    def test_full_policy_parses_all_fields(self):
        data = {
            'ignore': {
                'globs': ['docs/**', '**/*.md'],
                'paths': ['vendor/exact.bin'],
            },
            'disallow': {
                'extensions': ['ipynb', 'exe'],
                'globs': ['**/*.secret'],
                'mime_types': ['application/x-dosexec'],
            },
            'thresholds': {
                'max_text_size_kb': 500,
                'max_binary_size_kb': 100,
            },
            'rules': [
                {
                    'id': 'large-binaries',
                    'description': 'Block binaries over 100 KB',
                    'match': {'binary': True},
                    'size_over_kb': 100,
                    'action': 'error',
                },
                {
                    'id': 'large-text-warn',
                    'match': {'binary': False},
                    'size_over_kb': 500,
                    'action': 'warn',
                },
            ],
            'overrides': {
                'allow_globs': ['docs/allowed-large.bin'],
            },
        }
        policy = Policy.from_dict(data)
        self.assertEqual(policy.ignore_globs, ['docs/**', '**/*.md'])
        self.assertEqual(policy.ignore_paths, ['vendor/exact.bin'])
        self.assertEqual(policy.disallow_extensions, ['ipynb', 'exe'])
        self.assertEqual(policy.disallow_globs, ['**/*.secret'])
        self.assertEqual(policy.disallow_mime_types, ['application/x-dosexec'])
        self.assertEqual(policy.max_text_size_kb, 500.0)
        self.assertEqual(policy.max_binary_size_kb, 100.0)
        self.assertEqual(policy.allow_globs, ['docs/allowed-large.bin'])
        self.assertEqual(len(policy.rules), 2)
        self.assertEqual(policy.rules[0].id, 'large-binaries')
        self.assertEqual(policy.rules[0].match_binary, True)
        self.assertEqual(policy.rules[0].size_over_kb, 100.0)
        self.assertEqual(policy.rules[0].action, 'error')
        self.assertEqual(policy.rules[1].id, 'large-text-warn')
        self.assertEqual(policy.rules[1].action, 'warn')
        self.assertFalse(policy.is_empty())

    def test_rule_defaults(self):
        data = {'rules': [{'id': 'only-id'}]}
        policy = Policy.from_dict(data)
        rule = policy.rules[0]
        self.assertEqual(rule.description, '')
        self.assertEqual(rule.match_globs, [])
        self.assertEqual(rule.match_extensions, [])
        self.assertEqual(rule.match_mime_types, [])
        self.assertIsNone(rule.match_binary)
        self.assertIsNone(rule.size_over_kb)
        self.assertEqual(rule.action, 'error')

    def test_zero_size_is_valid(self):
        data = {'thresholds': {'max_text_size_kb': 0}}
        policy = Policy.from_dict(data)
        self.assertEqual(policy.max_text_size_kb, 0.0)

    def test_null_sections_are_treated_as_absent(self):
        data = {'ignore': None, 'disallow': None, 'thresholds': None,
                 'rules': None, 'overrides': None}
        policy = Policy.from_dict(data)
        self.assertTrue(policy.is_empty())


# ---------------------------------------------------------------------------
# Policy.from_dict: every PolicyError path
# ---------------------------------------------------------------------------

class TestPolicyFromDictErrors(unittest.TestCase):

    def assertPolicyErrorMentions(self, data, *expected_substrings):
        with self.assertRaises(PolicyError) as ctx:
            Policy.from_dict(data)
        message = str(ctx.exception)
        for substring in expected_substrings:
            self.assertIn(substring, message)
        return message

    def test_non_mapping_root_list(self):
        self.assertPolicyErrorMentions(['a', 'b'], 'mapping')

    def test_non_mapping_root_string(self):
        self.assertPolicyErrorMentions('just a string', 'mapping')

    def test_non_mapping_root_int(self):
        self.assertPolicyErrorMentions(42, 'mapping')

    def test_unknown_top_level_key(self):
        message = self.assertPolicyErrorMentions(
            {'triggers': []}, 'triggers')
        for key in ('ignore', 'disallow', 'thresholds', 'rules', 'overrides'):
            self.assertIn(key, message)

    def test_unknown_key_in_ignore(self):
        self.assertPolicyErrorMentions(
            {'ignore': {'globz': ['a']}}, 'globz', 'globs', 'paths')

    def test_unknown_key_in_disallow(self):
        self.assertPolicyErrorMentions(
            {'disallow': {'exts': ['a']}}, 'exts', 'extensions')

    def test_unknown_key_in_thresholds(self):
        self.assertPolicyErrorMentions(
            {'thresholds': {'max_size_kb': 1}}, 'max_size_kb')

    def test_unknown_key_in_overrides(self):
        self.assertPolicyErrorMentions(
            {'overrides': {'allowlist': []}}, 'allowlist', 'allow_globs')

    def test_unknown_key_in_rule(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'r', 'severity': 'error'}]}, 'severity', 'action')

    def test_unknown_key_in_rule_match(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'r', 'match': {'ext': ['py']}}]}, 'ext', 'extensions')

    def test_ignore_not_a_mapping(self):
        self.assertPolicyErrorMentions({'ignore': ['docs/**']}, 'mapping')

    def test_disallow_extensions_wrong_type_string_instead_of_list(self):
        self.assertPolicyErrorMentions(
            {'disallow': {'extensions': 'ipynb'}}, 'disallow.extensions', 'list')

    def test_ignore_globs_list_with_non_string_item(self):
        self.assertPolicyErrorMentions(
            {'ignore': {'globs': ['a', 123]}}, 'ignore.globs')

    def test_rules_not_a_list(self):
        self.assertPolicyErrorMentions({'rules': {'id': 'r'}}, 'rules', 'list')

    def test_rule_not_a_mapping(self):
        self.assertPolicyErrorMentions({'rules': ['just-a-string']}, 'rules[0]', 'mapping')

    def test_rule_missing_id(self):
        self.assertPolicyErrorMentions({'rules': [{'action': 'warn'}]}, 'id')

    def test_rule_empty_id(self):
        self.assertPolicyErrorMentions({'rules': [{'id': ''}]}, 'id')

    def test_rule_non_string_id(self):
        self.assertPolicyErrorMentions({'rules': [{'id': 123}]}, 'id')

    def test_duplicate_rule_ids(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'dup'}, {'id': 'dup'}]}, 'dup', 'Duplicate')

    def test_invalid_action_value(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'r', 'action': 'critical'}]}, 'action', 'critical')

    def test_negative_threshold_size(self):
        self.assertPolicyErrorMentions(
            {'thresholds': {'max_text_size_kb': -1}}, 'thresholds.max_text_size_kb')

    def test_negative_rule_size(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'r', 'size_over_kb': -5}]}, 'size_over_kb')

    def test_non_numeric_size(self):
        self.assertPolicyErrorMentions(
            {'thresholds': {'max_text_size_kb': 'big'}}, 'thresholds.max_text_size_kb')

    def test_boolean_size_rejected(self):
        # bool is a subclass of int in Python; must not silently pass as a size.
        self.assertPolicyErrorMentions(
            {'thresholds': {'max_text_size_kb': True}}, 'thresholds.max_text_size_kb')

    def test_rule_match_binary_wrong_type(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'r', 'match': {'binary': 'yes'}}]}, 'match.binary')

    def test_rule_match_not_a_mapping(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'r', 'match': ['binary']}]}, 'match', 'mapping')

    def test_rule_description_wrong_type(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 'r', 'description': 123}]}, 'description')


# ---------------------------------------------------------------------------
# load_policy
# ---------------------------------------------------------------------------

class TestLoadPolicy(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='rsg-policy-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, filename, content):
        full_path = os.path.join(self.tmp_dir, filename)
        with open(full_path, 'w', encoding='utf-8') as fh:
            fh.write(content)
        return full_path

    def test_none_path(self):
        policy, found = load_policy(None)
        self.assertTrue(policy.is_empty())
        self.assertFalse(found)

    def test_empty_string_path(self):
        policy, found = load_policy('')
        self.assertTrue(policy.is_empty())
        self.assertFalse(found)

    def test_missing_file(self):
        missing = os.path.join(self.tmp_dir, 'does-not-exist.yml')
        policy, found = load_policy(missing)
        self.assertTrue(policy.is_empty())
        self.assertFalse(found)

    def test_directory_path_treated_as_not_found(self):
        # A directory is not a policy file; load_policy treats it the same
        # as "does not exist" rather than raising, since it is not a
        # readable YAML mapping and this keeps the "optional file" contract
        # simple. (Documented deviation -- see final report.)
        policy, found = load_policy(self.tmp_dir)
        self.assertTrue(policy.is_empty())
        self.assertFalse(found)

    def test_empty_file(self):
        path = self._write('empty.yml', '')
        policy, found = load_policy(path)
        self.assertTrue(policy.is_empty())
        self.assertTrue(found)

    def test_file_containing_only_null(self):
        path = self._write('null.yml', 'null\n')
        policy, found = load_policy(path)
        self.assertTrue(policy.is_empty())
        self.assertTrue(found)

    def test_file_containing_only_whitespace_and_comments(self):
        path = self._write('comments.yml', '# just a comment\n\n')
        policy, found = load_policy(path)
        self.assertTrue(policy.is_empty())
        self.assertTrue(found)

    def test_valid_file(self):
        path = self._write('policy.yml', (
            "ignore:\n"
            "  globs:\n"
            "    - 'docs/**'\n"
            "disallow:\n"
            "  extensions: ['ipynb', 'exe']\n"
            "thresholds:\n"
            "  max_text_size_kb: 500\n"
            "  max_binary_size_kb: 100\n"
            "rules:\n"
            "  - id: large-binaries\n"
            "    match:\n"
            "      binary: true\n"
            "    size_over_kb: 100\n"
            "    action: error\n"
        ))
        policy, found = load_policy(path)
        self.assertTrue(found)
        self.assertEqual(policy.ignore_globs, ['docs/**'])
        self.assertEqual(policy.disallow_extensions, ['ipynb', 'exe'])
        self.assertEqual(policy.max_text_size_kb, 500.0)
        self.assertEqual(policy.max_binary_size_kb, 100.0)
        self.assertEqual(len(policy.rules), 1)
        self.assertEqual(policy.rules[0].id, 'large-binaries')

    def test_malformed_yaml_raises_policy_error_naming_path(self):
        path = self._write('bad.yml', 'ignore: [unclosed\n')
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        self.assertIn(path, str(ctx.exception))

    def test_invalid_shape_raises_policy_error_naming_path_and_problem(self):
        path = self._write('bad_shape.yml', 'not_a_real_key: true\n')
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        message = str(ctx.exception)
        self.assertIn(path, message)
        self.assertIn('not_a_real_key', message)

    def test_non_mapping_root_raises_policy_error_naming_path(self):
        path = self._write('list_root.yml', '- a\n- b\n')
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        self.assertIn(path, str(ctx.exception))

    def test_wrong_type_raises_policy_error_naming_path(self):
        path = self._write('wrong_type.yml', 'disallow:\n  extensions: "ipynb"\n')
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        message = str(ctx.exception)
        self.assertIn(path, message)
        self.assertIn('disallow.extensions', message)


if __name__ == '__main__':
    unittest.main()
