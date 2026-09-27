"""
Test suite for rule_engine module.

Covers glob matching, extension/MIME matching, the size condition grammar,
rule matching (AND across keys, OR within lists, the unknown-size signal),
policy schema validation (including every PolicyError path), and the
load_policy file-loading contract (including the YAML quoting hint).
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
    SizeCondition,
    extension_of,
    load_policy,
    matches_extension,
    matches_mime,
    matches_path,
    parse_size_condition,
    rule_matches,
)


def make_blob(path, blob_sha='a' * 40, commit_sha='b' * 40, status='A',
              size_bytes=None, is_binary=None, mime_type=None,
              is_transient=None, is_transient_version=None):
    """Build a Blob for tests without needing a real git repo."""
    return Blob(
        path=path,
        blob_sha=blob_sha,
        commit_sha=commit_sha,
        status=status,
        size_bytes=size_bytes,
        is_binary=is_binary,
        mime_type=mime_type,
        is_transient=is_transient,
        is_transient_version=is_transient_version,
    )


# ---------------------------------------------------------------------------
# Glob semantics (matches_path)
# ---------------------------------------------------------------------------

class TestMatchesPathStar(unittest.TestCase):
    """`*` must match a run of characters but never cross a `/`."""

    def test_star_matches_simple_name(self):
        self.assertTrue(matches_path('a.md', ['*.md']))

    def test_star_does_not_cross_slash(self):
        self.assertFalse(matches_path('docs/a.md', ['/*.md']))
        self.assertTrue(matches_path('a.md', ['/*.md']))

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

    def test_unclosed_class_does_not_raise(self):
        # A "[" with no closing "]" is not valid glob syntax. Whether the
        # pattern then matches depends on the pathspec version, but it
        # never raises.
        matches_path('a[b.txt', ['a[b.txt'])

    def test_escaped_bracket_is_literal(self):
        self.assertTrue(matches_path('a[b.txt', ['a\\[b.txt']))


class TestMatchesPathGlobstar(unittest.TestCase):
    """`**` as a whole path segment spans zero or more segments, including `/`."""

    def test_dir_globstar_matches_direct_child(self):
        self.assertTrue(matches_path('docs/a.txt', ['docs/**']))

    def test_dir_globstar_matches_nested_child(self):
        self.assertTrue(matches_path('docs/x/y/a.txt', ['docs/**']))

    def test_dir_globstar_does_not_match_file_named_like_base(self):
        # "docs/**" matches what is inside the folder "docs", not a file
        # named "docs".
        self.assertFalse(matches_path('docs', ['docs/**']))

    def test_dir_globstar_does_not_match_sibling(self):
        self.assertFalse(matches_path('other/a.txt', ['docs/**']))
        self.assertFalse(matches_path('docsx/a.txt', ['docs/**']))

    def test_dir_globstar_stays_anchored_to_root(self):
        # A pattern with a "/" is anchored: "docs/**" is not the same as
        # "**/docs/**".
        self.assertFalse(matches_path('x/docs/a.txt', ['docs/**']))

    def test_middle_globstar_stays_anchored_to_root(self):
        self.assertFalse(matches_path('x/a/b', ['a/**/b']))

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
        self.assertFalse(matches_path('a/x/bc', ['a/**/b']))
        self.assertFalse(matches_path('x/b', ['a/**/b']))

    def test_middle_globstar_matches_contents_of_matched_folder(self):
        # "a/b" is a folder here, so "a/**/b" matches everything in it.
        self.assertTrue(matches_path('a/b/c', ['a/**/b']))

    def test_bare_globstar_matches_anything(self):
        self.assertTrue(matches_path('a.txt', ['**']))
        self.assertTrue(matches_path('a/b/c.txt', ['**']))

    def test_consecutive_globstars_match_anything(self):
        self.assertTrue(matches_path('a', ['**/**']))
        self.assertTrue(matches_path('a/b', ['**/**']))

    def test_trailing_globstar_slash_matches_folder_contents(self):
        self.assertTrue(matches_path('a/b', ['**/']))
        self.assertTrue(matches_path('docs/a/b.txt', ['docs/**/']))

    def test_trailing_slash_pattern_matches_folder_contents(self):
        self.assertTrue(matches_path('docs/a.txt', ['docs/']))
        self.assertTrue(matches_path('docs/x/y.txt', ['docs/']))

    def test_trailing_slash_pattern_does_not_match_file(self):
        self.assertFalse(matches_path('docs', ['docs/']))


class TestMatchesPathAnyDepthMatching(unittest.TestCase):
    """
    A pattern with no `/`, or only a trailing `/`, matches at any depth
    (gitignore-style), and a leading `/` anchors it back to the root.
    """

    def test_bare_star_pattern_matches_at_depth(self):
        self.assertTrue(matches_path('a.md', ['*.md']))
        self.assertTrue(matches_path('docs/x/a.md', ['*.md']))

    def test_bare_exact_name_matches_at_depth(self):
        self.assertTrue(matches_path('README.md', ['README.md']))
        self.assertTrue(matches_path('docs/README.md', ['README.md']))

    def test_leading_slash_anchors_to_root(self):
        self.assertTrue(matches_path('a.md', ['/*.md']))
        self.assertFalse(matches_path('docs/a.md', ['/*.md']))

    def test_leading_slash_anchors_exact_name_to_root(self):
        self.assertTrue(matches_path('README.md', ['/README.md']))
        self.assertFalse(matches_path('docs/README.md', ['/README.md']))

    def test_trailing_slash_pattern_matches_at_depth(self):
        self.assertTrue(matches_path('build/x', ['build/']))
        self.assertTrue(matches_path('a/build/x/y', ['build/']))

    def test_bare_name_matches_folder_contents(self):
        self.assertTrue(matches_path('node_modules/x.js', ['node_modules']))
        self.assertTrue(matches_path('a/node_modules/x/y.js', ['node_modules']))


class TestMatchesPathNegationAndComments(unittest.TestCase):
    """`!pattern` and `#` comments, read as in a `.gitignore` file."""

    def test_negation_excludes_earlier_match(self):
        patterns = ['*.log', '!keep.log']
        self.assertTrue(matches_path('a.log', patterns))
        self.assertFalse(matches_path('keep.log', patterns))
        self.assertFalse(matches_path('x/keep.log', patterns))

    def test_negation_before_match_has_no_effect(self):
        self.assertTrue(matches_path('keep.log', ['!keep.log', '*.log']))

    def test_negation_alone_matches_nothing(self):
        self.assertFalse(matches_path('keep.log', ['!keep.log']))

    def test_escaped_exclamation_mark_is_literal(self):
        self.assertTrue(matches_path('!a.txt', ['\\!a.txt']))

    def test_comment_matches_nothing(self):
        self.assertFalse(matches_path('#a.txt', ['#a.txt']))

    def test_escaped_hash_is_literal(self):
        self.assertTrue(matches_path('#a.txt', ['\\#a.txt']))


class TestMatchesPathAnchoringAndEscaping(unittest.TestCase):
    """Root-anchoring for patterns containing a `/`, and literal-character escaping."""

    def test_pattern_with_slash_is_not_matched_at_depth(self):
        self.assertFalse(matches_path('x/docs/a.md', ['docs/a.md']))
        self.assertTrue(matches_path('docs/a.md', ['docs/a.md']))

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


class TestExtensionOf(unittest.TestCase):

    def test_simple_extension(self):
        self.assertEqual(extension_of('a.txt'), 'txt')

    def test_preserves_case(self):
        self.assertEqual(extension_of('a.TXT'), 'TXT')

    def test_no_extension(self):
        self.assertIsNone(extension_of('Makefile'))

    def test_dotfile_with_no_further_dot(self):
        self.assertIsNone(extension_of('.gitignore'))

    def test_last_segment_of_multiple_dots(self):
        self.assertEqual(extension_of('archive.tar.gz'), 'gz')

    def test_uses_basename_only(self):
        self.assertEqual(extension_of('a/b/c.ipynb'), 'ipynb')


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
# Size condition grammar (parse_size_condition / SizeCondition)
# ---------------------------------------------------------------------------

class TestParseSizeConditionValid(unittest.TestCase):

    def test_greater_than(self):
        condition = parse_size_condition('>500KB', 'ctx')
        self.assertEqual(condition.operator, '>')
        self.assertEqual(condition.value, 500.0)
        self.assertEqual(condition.unit, 'KB')

    def test_greater_or_equal(self):
        condition = parse_size_condition('>=0B', 'ctx')
        self.assertEqual(condition.operator, '>=')
        self.assertEqual(condition.value, 0.0)
        self.assertEqual(condition.unit, 'B')

    def test_less_than(self):
        condition = parse_size_condition('<6MB', 'ctx')
        self.assertEqual(condition.operator, '<')

    def test_less_or_equal_with_internal_whitespace(self):
        condition = parse_size_condition('<= 6 mb', 'ctx')
        self.assertEqual(condition.operator, '<=')
        self.assertEqual(condition.value, 6.0)
        self.assertEqual(condition.unit, 'MB')

    def test_leading_and_trailing_whitespace(self):
        condition = parse_size_condition('  >500KB  ', 'ctx')
        self.assertEqual(condition.operator, '>')

    def test_decimal_value(self):
        condition = parse_size_condition('>1.5MB', 'ctx')
        self.assertEqual(condition.value, 1.5)

    def test_case_insensitive_unit(self):
        for text in ('>500kb', '>500Kb', '>500KB'):
            with self.subTest(text=text):
                self.assertEqual(parse_size_condition(text, 'ctx').unit, 'KB')

    def test_gb_unit(self):
        self.assertEqual(parse_size_condition('>1GB', 'ctx').unit, 'GB')

    def test_zero_value(self):
        self.assertEqual(parse_size_condition('>=0B', 'ctx').value, 0.0)


class TestParseSizeConditionInvalid(unittest.TestCase):

    def _assert_raises_with_example(self, value):
        with self.assertRaises(PolicyError) as ctx:
            parse_size_condition(value, "'rules[2]'.match.size")
        message = str(ctx.exception)
        self.assertIn("'rules[2]'.match.size", message)
        self.assertIn('">500KB"', message)
        self.assertIn('"<=6MB"', message)
        self.assertIn(repr(value), message)
        return message

    def test_number_instead_of_string(self):
        self._assert_raises_with_example(500)

    def test_missing_unit(self):
        self._assert_raises_with_example('>500')

    def test_missing_operator(self):
        self._assert_raises_with_example('500KB')

    def test_equals_operator_rejected(self):
        self._assert_raises_with_example('=500KB')

    def test_double_equals_operator_rejected(self):
        self._assert_raises_with_example('==500KB')

    def test_two_conditions_rejected(self):
        self._assert_raises_with_example('>500KB,<600KB')

    def test_unknown_unit_rejected(self):
        self._assert_raises_with_example('>500TB')

    def test_boolean_rejected(self):
        self._assert_raises_with_example(True)

    def test_empty_string_rejected(self):
        self._assert_raises_with_example('')


class TestSizeConditionHolds(unittest.TestCase):

    def test_unknown_size_never_holds(self):
        condition = SizeCondition('>', 500, 'KB')
        self.assertFalse(condition.holds(None))

    def test_greater_than_boundary(self):
        condition = SizeCondition('>', 1, 'KB')
        self.assertFalse(condition.holds(1024))
        self.assertTrue(condition.holds(1025))

    def test_greater_or_equal_boundary(self):
        condition = SizeCondition('>=', 1, 'KB')
        self.assertTrue(condition.holds(1024))
        self.assertFalse(condition.holds(1023))

    def test_less_than_boundary(self):
        condition = SizeCondition('<', 1, 'KB')
        self.assertFalse(condition.holds(1024))
        self.assertTrue(condition.holds(1023))

    def test_less_or_equal_boundary(self):
        condition = SizeCondition('<=', 1, 'KB')
        self.assertTrue(condition.holds(1024))
        self.assertFalse(condition.holds(1025))

    def test_unit_conversion(self):
        self.assertEqual(SizeCondition('>', 1, 'KB').threshold_bytes, 1024)
        self.assertEqual(SizeCondition('>', 1, 'MB').threshold_bytes, 1024 ** 2)
        self.assertEqual(SizeCondition('>', 1, 'GB').threshold_bytes, 1024 ** 3)
        self.assertEqual(SizeCondition('>', 500, 'B').threshold_bytes, 500)

    def test_render_threshold(self):
        self.assertEqual(SizeCondition('>', 500, 'KB').render_threshold(), '500 KB')
        self.assertEqual(SizeCondition('>', 1.5, 'MB').render_threshold(), '1.5 MB')
        self.assertEqual(SizeCondition('>=', 0, 'B').render_threshold(), '0 B')
        self.assertEqual(SizeCondition('>', 1048576, 'KB').render_threshold(), '1048576 KB')


# ---------------------------------------------------------------------------
# rule_matches
# ---------------------------------------------------------------------------

class TestRuleMatchesContentKeys(unittest.TestCase):

    def test_glob_only_rule_matches(self):
        rule = Rule(name='r', match_globs=['**/*.secret'])
        self.assertEqual(rule_matches(rule, make_blob('a/b.secret')), (True, False))
        self.assertEqual(rule_matches(rule, make_blob('a/b.txt')), (False, False))

    def test_extension_only_rule_matches(self):
        rule = Rule(name='r', match_extensions=['ipynb'])
        self.assertEqual(rule_matches(rule, make_blob('notebook.ipynb'))[0], True)
        self.assertEqual(rule_matches(rule, make_blob('notebook.py'))[0], False)

    def test_mime_only_rule_matches(self):
        rule = Rule(name='r', match_mime_types=['application/*'])
        self.assertTrue(rule_matches(rule, make_blob('a.bin', mime_type='application/zip'))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.bin', mime_type='text/plain'))[0])

    def test_multiple_content_keys_combine_with_and(self):
        # Unlike a single key's own OR-within-list, two different keys set
        # on the same rule must BOTH hold.
        rule = Rule(name='r', match_globs=['**/*.secret'], match_extensions=['secret'])
        self.assertTrue(rule_matches(rule, make_blob('a.secret'))[0])

        rule2 = Rule(name='r2', match_globs=['docs/**'], match_extensions=['exe'])
        # Matches the glob but not the extension: AND across keys means no match.
        self.assertFalse(rule_matches(rule2, make_blob('docs/a.txt'))[0])
        # Matches the extension but not the glob: still no match.
        self.assertFalse(rule_matches(rule2, make_blob('other/a.exe'))[0])
        # Matches both.
        self.assertTrue(rule_matches(rule2, make_blob('docs/a.exe'))[0])

    def test_glob_list_combines_with_or(self):
        rule = Rule(name='r', match_globs=['*.md', '*.txt'])
        self.assertTrue(rule_matches(rule, make_blob('a.md'))[0])
        self.assertTrue(rule_matches(rule, make_blob('a.txt'))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.py'))[0])

    def test_empty_lists_impose_no_condition(self):
        rule = Rule(name='r', match_binary=True)
        self.assertTrue(rule_matches(rule, make_blob('anything.bin', is_binary=True))[0])
        self.assertTrue(rule_matches(rule, make_blob('other/thing.dat', is_binary=True))[0])

    def test_no_conditions_at_all_matches_everything(self):
        rule = Rule(name='r')
        self.assertTrue(rule_matches(rule, make_blob('anything'))[0])


class TestRuleMatchesBinary(unittest.TestCase):

    def test_true_requires_definite_binary(self):
        rule = Rule(name='r', match_binary=True)
        self.assertTrue(rule_matches(rule, make_blob('a.bin', is_binary=True))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.bin', is_binary=False))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.bin', is_binary=None))[0])

    def test_false_also_matches_undetermined(self):
        rule = Rule(name='r', match_binary=False)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=False))[0])
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=None))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.bin', is_binary=True))[0])

    def test_none_means_no_condition(self):
        rule = Rule(name='r', match_extensions=['txt'], match_binary=None)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=True))[0])
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=False))[0])
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_binary=None))[0])

    def test_binary_is_and_with_content_match(self):
        rule = Rule(name='r', match_extensions=['bin'], match_binary=True)
        self.assertTrue(rule_matches(rule, make_blob('a.bin', is_binary=True))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.bin', is_binary=False))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.txt', is_binary=True))[0])


class TestRuleMatchesSize(unittest.TestCase):

    def test_size_condition_must_hold(self):
        rule = Rule(name='r', match_size=SizeCondition('>', 50, 'KB'))
        self.assertEqual(rule_matches(rule, make_blob(
            'a.log', size_bytes=60 * 1024)), (True, False))
        self.assertEqual(rule_matches(rule, make_blob(
            'a.log', size_bytes=10 * 1024)), (False, False))

    def test_size_is_and_with_other_keys(self):
        rule = Rule(name='r', match_extensions=['log'], match_size=SizeCondition('>', 50, 'KB'))
        # Extension matches, size does not: no match, and size WAS evaluated
        # (blob.size_bytes is known), so this is not the "unknown size" case.
        self.assertEqual(
            rule_matches(rule, make_blob('a.log', size_bytes=10 * 1024)), (False, False))
        # Size matches, extension does not: never reaches the size check.
        self.assertEqual(
            rule_matches(rule, make_blob('a.txt', size_bytes=60 * 1024)), (False, False))

    def test_unknown_size_is_reported_only_when_size_would_be_checked(self):
        rule = Rule(name='r', match_extensions=['log'], match_size=SizeCondition('>', 50, 'KB'))
        # Every earlier condition holds and the size is unknown: reported.
        self.assertEqual(rule_matches(rule, make_blob('a.log', size_bytes=None)), (False, True))
        # The extension does not match, so the size condition is never
        # reached at all: not reported.
        self.assertEqual(rule_matches(rule, make_blob('a.txt', size_bytes=None)), (False, False))

    def test_no_size_condition_never_reports_unknown_size(self):
        rule = Rule(name='r', match_extensions=['log'])
        self.assertEqual(rule_matches(rule, make_blob('a.log', size_bytes=None)), (True, False))


class TestRuleMatchesTransient(unittest.TestCase):
    """`transient`/`transient_version` use plain equality, unlike `binary`."""

    def test_transient_true_requires_definite_true(self):
        rule = Rule(name='r', match_transient=True)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_transient=True))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.txt', is_transient=False))[0])

    def test_transient_false_does_not_also_match_undetermined(self):
        # Unlike match_binary=False, an undetermined (None) value does NOT
        # satisfy transient=False: transient values are always definite
        # booleans in history mode, so None only ever means "not computed".
        rule = Rule(name='r', match_transient=False)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_transient=False))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.txt', is_transient=None))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.txt', is_transient=True))[0])

    def test_transient_none_means_no_condition(self):
        rule = Rule(name='r', match_extensions=['txt'], match_transient=None)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_transient=True))[0])
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_transient=False))[0])
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_transient=None))[0])

    def test_transient_version_true_requires_definite_true(self):
        rule = Rule(name='r', match_transient_version=True)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_transient_version=True))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.txt', is_transient_version=False))[0])

    def test_transient_version_false_does_not_also_match_undetermined(self):
        rule = Rule(name='r', match_transient_version=False)
        self.assertTrue(rule_matches(rule, make_blob('a.txt', is_transient_version=False))[0])
        self.assertFalse(rule_matches(rule, make_blob('a.txt', is_transient_version=None))[0])

    def test_transient_and_transient_version_combine_with_and(self):
        rule = Rule(name='r', match_transient=True, match_transient_version=True)
        self.assertTrue(rule_matches(
            rule, make_blob('a.txt', is_transient=True, is_transient_version=True))[0])
        # transient implies transient_version in practice, but the engine
        # itself just ANDs the two conditions -- this combination cannot
        # occur from a real transience pass, but must still evaluate
        # correctly rather than assume it.
        self.assertFalse(rule_matches(
            rule, make_blob('a.txt', is_transient=True, is_transient_version=False))[0])

    def test_transient_is_and_with_other_keys(self):
        rule = Rule(name='r', match_extensions=['log'], match_transient=True)
        self.assertFalse(rule_matches(
            rule, make_blob('a.txt', is_transient=True))[0])  # wrong extension
        self.assertFalse(rule_matches(
            rule, make_blob('a.log', is_transient=False))[0])  # not transient


# ---------------------------------------------------------------------------
# Policy.is_empty
# ---------------------------------------------------------------------------

class TestPolicyIsEmpty(unittest.TestCase):

    def test_default_policy_is_empty(self):
        self.assertTrue(Policy.empty().is_empty())

    def test_rules_makes_non_empty(self):
        self.assertFalse(Policy(rules=[Rule(name='r')]).is_empty())


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

    def test_rules_null_yields_empty_policy(self):
        self.assertTrue(Policy.from_dict({'rules': None}).is_empty())

    def test_rules_empty_list_yields_empty_policy(self):
        self.assertTrue(Policy.from_dict({'rules': []}).is_empty())

    def test_full_policy_parses_all_fields(self):
        data = {
            'rules': [
                {
                    'id': 'large-binaries',
                    'description': 'Block binaries over 100 KB',
                    'match': {'binary': True, 'size': '>100KB'},
                    'action': 'error',
                },
                {
                    'id': 'large-text-warn',
                    'match': {'binary': False, 'size': '>500KB'},
                    'action': 'warn',
                },
                {
                    'match': {'globs': ['vendor/**']},
                    'action': 'stop',
                },
            ],
        }
        policy = Policy.from_dict(data)
        self.assertEqual(len(policy.rules), 3)

        first = policy.rules[0]
        self.assertEqual(first.name, 'large-binaries')
        self.assertEqual(first.id, 'large-binaries')
        self.assertEqual(first.description, 'Block binaries over 100 KB')
        self.assertEqual(first.match_binary, True)
        self.assertEqual(first.match_size.operator, '>')
        self.assertEqual(first.match_size.value, 100.0)
        self.assertEqual(first.match_size.unit, 'KB')
        self.assertEqual(first.action, 'error')

        second = policy.rules[1]
        self.assertEqual(second.name, 'large-text-warn')
        self.assertEqual(second.action, 'warn')

        third = policy.rules[2]
        self.assertIsNone(third.id)
        self.assertEqual(third.name, 'rules[2]')
        self.assertEqual(third.action, 'stop')
        self.assertFalse(policy.is_empty())

    def test_rule_defaults(self):
        data = {'rules': [{'id': 'only-id', 'match': {'globs': ['*.md']}}]}
        policy = Policy.from_dict(data)
        rule = policy.rules[0]
        self.assertEqual(rule.description, '')
        self.assertEqual(rule.match_globs, ['*.md'])
        self.assertEqual(rule.match_extensions, [])
        self.assertEqual(rule.match_mime_types, [])
        self.assertIsNone(rule.match_binary)
        self.assertIsNone(rule.match_size)
        self.assertIsNone(rule.match_transient)
        self.assertIsNone(rule.match_transient_version)
        self.assertEqual(rule.action, 'error')

    def test_transient_and_transient_version_parse_as_booleans(self):
        data = {'rules': [
            {'id': 'r1', 'match': {'transient': True}},
            {'id': 'r2', 'match': {'transient_version': False}},
            {'id': 'r3', 'match': {'transient': False, 'transient_version': True}},
        ]}
        policy = Policy.from_dict(data)
        by_id = {rule.id: rule for rule in policy.rules}
        self.assertIs(by_id['r1'].match_transient, True)
        self.assertIsNone(by_id['r1'].match_transient_version)
        self.assertIs(by_id['r2'].match_transient_version, False)
        self.assertIsNone(by_id['r2'].match_transient)
        self.assertIs(by_id['r3'].match_transient, False)
        self.assertIs(by_id['r3'].match_transient_version, True)

    def test_transient_alone_counts_as_a_condition(self):
        # Must not raise "match is required and must set at least one
        # condition" -- transient/transient_version count on their own.
        policy = Policy.from_dict({'rules': [{'match': {'transient': True}}]})
        self.assertEqual(len(policy.rules), 1)

    def test_transient_version_alone_counts_as_a_condition(self):
        policy = Policy.from_dict({'rules': [{'match': {'transient_version': False}}]})
        self.assertEqual(len(policy.rules), 1)

    def test_anonymous_rules_get_index_based_names(self):
        data = {'rules': [
            {'match': {'globs': ['*.a']}},
            {'match': {'globs': ['*.b']}},
        ]}
        policy = Policy.from_dict(data)
        self.assertEqual(policy.rules[0].name, 'rules[0]')
        self.assertEqual(policy.rules[1].name, 'rules[1]')

    def test_stop_action_is_valid(self):
        data = {'rules': [{'match': {'globs': ['vendor/**']}, 'action': 'stop'}]}
        policy = Policy.from_dict(data)
        self.assertEqual(policy.rules[0].action, 'stop')


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
        self.assertPolicyErrorMentions({'ignore': {}}, 'ignore', 'rules')

    def test_empty_list_condition(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'globs': []}}]}, "'rules[0]'.match.globs", 'at least one entry')

    def test_empty_list_beside_another_condition(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'extensions': [], 'binary': True}}]},
            "'rules[0]'.match.extensions", 'at least one entry')

    def test_match_with_only_null_conditions(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'binary': None, 'size': None}}]},
            "'rules[0]'.match", 'at least one condition')

    def test_match_with_only_null_transient_conditions(self):
        message = self.assertPolicyErrorMentions(
            {'rules': [{'match': {'transient': None, 'transient_version': None}}]},
            "'rules[0]'.match", 'at least one condition')
        self.assertIn('transient', message)
        self.assertIn('transient_version', message)

    def test_null_glob_entry_points_at_quoting(self):
        # An unquoted "#pattern" list item is a YAML comment, leaving null.
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'globs': ['*.log', None]}}]},
            "'rules[0]'.match.globs", "starts with '#' must be in quotes")

    def test_unknown_key_in_rule(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'globs': ['*.a']}, 'severity': 'error'}]}, 'severity', 'action')

    def test_unknown_key_in_rule_match(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'ext': ['py']}}]}, 'ext', 'extensions')

    def test_transient_match_wrong_type(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'transient': 'yes'}}]}, 'match.transient')

    def test_transient_version_match_wrong_type(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'transient_version': 'yes'}}]}, 'match.transient_version')

    def test_rules_not_a_list(self):
        self.assertPolicyErrorMentions({'rules': {'id': 'r'}}, 'rules', 'list')

    def test_rule_not_a_mapping(self):
        self.assertPolicyErrorMentions({'rules': ['just-a-string']}, 'rules[0]', 'mapping')

    def test_rule_missing_match(self):
        self.assertPolicyErrorMentions({'rules': [{'id': 'r'}]}, 'match')

    def test_rule_null_match(self):
        self.assertPolicyErrorMentions({'rules': [{'match': None}]}, 'match')

    def test_rule_empty_match(self):
        self.assertPolicyErrorMentions({'rules': [{'match': {}}]}, 'match')

    def test_rule_match_not_a_mapping(self):
        self.assertPolicyErrorMentions({'rules': [{'match': ['binary']}]}, 'match', 'mapping')

    def test_rule_empty_id(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': '', 'match': {'globs': ['*.a']}}]}, 'id')

    def test_rule_non_string_id(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'id': 123, 'match': {'globs': ['*.a']}}]}, 'id')

    def test_duplicate_rule_ids(self):
        self.assertPolicyErrorMentions(
            {'rules': [
                {'id': 'dup', 'match': {'globs': ['*.a']}},
                {'id': 'dup', 'match': {'globs': ['*.b']}},
            ]}, 'dup', 'Duplicate')

    def test_two_anonymous_rules_are_not_duplicates(self):
        # Only rules that SET an id participate in the uniqueness check.
        policy = Policy.from_dict({'rules': [
            {'match': {'globs': ['*.a']}},
            {'match': {'globs': ['*.b']}},
        ]})
        self.assertEqual(len(policy.rules), 2)

    def test_invalid_action_value(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'globs': ['*.a']}, 'action': 'critical'}]}, 'action', 'critical')

    def test_rule_match_binary_wrong_type(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'binary': 'yes'}}]}, 'match.binary')

    def test_rule_description_wrong_type(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'description': 123, 'match': {'globs': ['*.a']}}]}, 'description')

    def test_rule_size_number_instead_of_string(self):
        message = self.assertPolicyErrorMentions(
            {'rules': [{'match': {'size': 500}}]},
            "'rules[0]'.match.size", '">500KB"', '"<=6MB"')
        self.assertIn('500', message)

    def test_rule_size_missing_unit(self):
        self.assertPolicyErrorMentions({'rules': [{'match': {'size': '>500'}}]}, 'match.size')

    def test_rule_size_equals_operator_rejected(self):
        self.assertPolicyErrorMentions({'rules': [{'match': {'size': '=500KB'}}]}, 'match.size')

    def test_globs_list_with_non_string_item(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'globs': ['a', 123]}}]}, 'match.globs')

    def test_extensions_wrong_type_string_instead_of_list(self):
        self.assertPolicyErrorMentions(
            {'rules': [{'match': {'extensions': 'ipynb'}}]}, 'match.extensions', 'list')


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
            "rules:\n"
            "  - id: large-binaries\n"
            "    match:\n"
            "      binary: true\n"
            "      size: \">100KB\"\n"
            "    action: error\n"
        ))
        policy, found = load_policy(path)
        self.assertTrue(found)
        self.assertEqual(len(policy.rules), 1)
        self.assertEqual(policy.rules[0].id, 'large-binaries')
        self.assertEqual(policy.rules[0].match_size.value, 100.0)

    def test_malformed_yaml_raises_policy_error_naming_path(self):
        path = self._write('bad.yml', 'rules: [unclosed\n')
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
        path = self._write('wrong_type.yml', 'rules:\n  - match: {extensions: "ipynb"}\n')
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        message = str(ctx.exception)
        self.assertIn(path, message)
        self.assertIn('match.extensions', message)

    def test_quoting_hint_for_unquoted_size(self):
        # An unquoted ">500KB" is parsed by YAML as an (invalid) block
        # scalar header, not a plain string.
        path = self._write('unquoted_size.yml', (
            "rules:\n"
            "  - match:\n"
            "      size: >500KB\n"
        ))
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        message = str(ctx.exception)
        self.assertIn('contains invalid YAML', message)
        self.assertIn('Put quotes around size values', message)
        self.assertIn('size: ">500KB"', message)
        self.assertIn('globs: ["*.md"]', message)

    def test_quoting_hint_for_unquoted_glob_star(self):
        # An unquoted "*.md" is parsed by YAML as an alias reference.
        path = self._write('unquoted_glob.yml', (
            "rules:\n"
            "  - match: {globs: [*.md]}\n"
        ))
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        self.assertIn('Put quotes around size values', str(ctx.exception))

    def test_quoting_hint_for_unquoted_glob_exclamation(self):
        # An unquoted "!keep.log" is parsed by YAML as a tag.
        path = self._write('unquoted_negation.yml', (
            "rules:\n"
            "  - match:\n"
            "      globs:\n"
            "        - '*.log'\n"
            "        - !keep.log\n"
        ))
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        self.assertIn("start with '*', '!' or '#'", str(ctx.exception))

    def test_no_quoting_hint_for_an_unrelated_yaml_error(self):
        path = self._write('other_bad.yml', 'rules: [unclosed\n')
        with self.assertRaises(PolicyError) as ctx:
            load_policy(path)
        self.assertNotIn('Put quotes around size values', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
