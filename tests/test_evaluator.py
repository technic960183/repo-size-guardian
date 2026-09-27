"""
Test suite for the evaluator module.

Covers the per-blob rule walk (skip rules, AND/OR semantics delegated to
rule_engine, `stop` keeping earlier hits, one entry per file version with
the highest severity), the `(blob_sha, path)` dedupe behavior, the
unknown-size warning, hit message construction (both the quick-start-input
style and the generic policy-rule style, including the size parenthetical),
and `has_failing_violations`.
"""

import io
import unittest

from repo_size_guardian.evaluator import EvaluationConfig, evaluate_blobs, has_failing_violations
from repo_size_guardian.models import Blob
from repo_size_guardian.rule_engine import Rule, SizeCondition

KB = 1024


def make_blob(path='a.txt', blob_sha='sha1', commit_sha='c1', status='A',
              size_bytes=1024, is_binary=False, mime_type=None):
    """Build a Blob with convenient defaults for evaluator tests."""
    return Blob(
        path=path,
        blob_sha=blob_sha,
        commit_sha=commit_sha,
        status=status,
        size_bytes=size_bytes,
        is_binary=is_binary,
        mime_type=mime_type,
    )


def stop_rule(**kwargs):
    return Rule(name=kwargs.pop('name', 'stop'), action='stop', **kwargs)


class TestSkipDeletionsAndEmptySha(unittest.TestCase):
    """Deletions and blobs with no content are skipped entirely."""

    def test_deleted_blob_is_skipped(self):
        blob = make_blob(status='D', blob_sha='', size_bytes=10 * KB * KB)
        rule = Rule(name='r', match_size=SizeCondition('>', 1, 'KB'))
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(entries, [])

    def test_deleted_blob_with_nonempty_sha_is_still_skipped(self):
        # Defensive: is_deleted alone must be enough to skip, regardless of
        # whether a blob_sha happens to be present.
        blob = make_blob(status='D', blob_sha='deadbeef', size_bytes=10 * KB * KB)
        rule = Rule(name='r', match_size=SizeCondition('>', 1, 'KB'))
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(entries, [])

    def test_empty_blob_sha_is_skipped_even_if_not_marked_deleted(self):
        blob = make_blob(status='A', blob_sha='', size_bytes=10 * KB * KB)
        rule = Rule(name='r', match_size=SizeCondition('>', 1, 'KB'))
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(entries, [])

    def test_normal_blob_is_not_skipped(self):
        blob = make_blob(status='A', blob_sha='sha1', size_bytes=10 * KB * KB)
        rule = Rule(name='r', match_size=SizeCondition('>', 1, 'KB'))
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(len(entries), 1)


class TestRuleWalk(unittest.TestCase):
    """Every non-stop matching rule records a hit; the walk continues."""

    def test_no_rules_produces_no_entries(self):
        blob = make_blob()
        self.assertEqual(evaluate_blobs([blob], [], EvaluationConfig()), [])

    def test_non_matching_rule_produces_no_hit(self):
        rule = Rule(name='r', match_extensions=['exe'])
        entries = evaluate_blobs([make_blob(path='a.txt')], [rule], EvaluationConfig())
        self.assertEqual(entries, [])

    def test_single_matching_rule_produces_one_hit(self):
        rule = Rule(name='no-notebooks', match_extensions=['ipynb'], action='error')
        entries = evaluate_blobs([make_blob(path='a.ipynb')], [rule], EvaluationConfig())
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0].violations), 1)
        violation = entries[0].violations[0]
        self.assertEqual(violation.rule_name, 'no-notebooks')
        self.assertEqual(violation.severity, 'error')

    def test_two_matching_non_stop_rules_both_record_hits(self):
        rule1 = Rule(name='first', match_extensions=['bin'], action='warn')
        rule2 = Rule(name='second', match_extensions=['bin'], action='error')
        entries = evaluate_blobs([make_blob(path='a.bin')], [rule1, rule2], EvaluationConfig())
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual([v.rule_name for v in entry.violations], ['first', 'second'])
        # Highest severity among the hits.
        self.assertEqual(entry.severity, 'error')

    def test_hits_are_listed_in_rule_order(self):
        rule1 = Rule(name='a', match_extensions=['bin'], action='error')
        rule2 = Rule(name='b', match_extensions=['bin'], action='error')
        rule3 = Rule(name='c', match_extensions=['bin'], action='error')
        entries = evaluate_blobs(
            [make_blob(path='a.bin')], [rule1, rule2, rule3], EvaluationConfig())
        self.assertEqual([v.rule_name for v in entries[0].violations], ['a', 'b', 'c'])

    def test_only_warn_hits_have_warn_severity(self):
        rule = Rule(name='w', match_extensions=['bin'], action='warn')
        entries = evaluate_blobs([make_blob(path='a.bin')], [rule], EvaluationConfig())
        self.assertEqual(entries[0].severity, 'warn')


class TestStopAction(unittest.TestCase):
    """A matching `stop` rule ends the walk, keeping earlier hits."""

    def test_stop_rule_with_no_earlier_hits_produces_no_entry(self):
        rule = stop_rule(match_globs=['vendor/**'])
        blob = make_blob(path='vendor/a.exe')
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(entries, [])

    def test_stop_rule_excludes_rules_listed_after_it(self):
        stop = stop_rule(match_globs=['vendor/**'])
        disallow = Rule(name='no-exe', match_extensions=['exe'], action='error')
        blob = make_blob(path='vendor/a.exe')
        entries = evaluate_blobs([blob], [stop, disallow], EvaluationConfig())
        self.assertEqual(entries, [])

    def test_stop_rule_keeps_hits_recorded_before_it(self):
        earlier = Rule(name='no-exe', match_extensions=['exe'], action='error')
        stop = stop_rule(match_globs=['vendor/**'])
        later = Rule(name='never-reached', match_extensions=['exe'], action='warn')
        blob = make_blob(path='vendor/a.exe')
        entries = evaluate_blobs([blob], [earlier, stop, later], EvaluationConfig())
        self.assertEqual(len(entries), 1)
        self.assertEqual([v.rule_name for v in entries[0].violations], ['no-exe'])

    def test_non_matching_stop_rule_does_not_affect_the_walk(self):
        stop = stop_rule(match_globs=['vendor/**'])
        disallow = Rule(name='no-exe', match_extensions=['exe'], action='error')
        blob = make_blob(path='a.exe')  # not under vendor/
        entries = evaluate_blobs([blob], [stop, disallow], EvaluationConfig())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].violations[0].rule_name, 'no-exe')


class TestDedupe(unittest.TestCase):
    """Dedupe on (blob_sha, path), keep the first occurrence."""

    def test_same_sha_and_path_deduped_keeps_first_regardless_of_second(self):
        rule = Rule(name='big', match_size=SizeCondition('>', 10, 'KB'))
        first = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c1', size_bytes=1 * KB)
        second = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c2', size_bytes=100 * KB)
        entries = evaluate_blobs(
            [first, second], [rule], EvaluationConfig(dedupe_blobs=True))
        self.assertEqual(entries, [])

    def test_same_sha_different_path_yields_two_entries(self):
        rule = Rule(name='big', match_size=SizeCondition('>', 10, 'KB'))
        first = make_blob(path='a.txt', blob_sha='sha1', size_bytes=100 * KB)
        second = make_blob(path='b.txt', blob_sha='sha1', size_bytes=100 * KB)
        entries = evaluate_blobs(
            [first, second], [rule], EvaluationConfig(dedupe_blobs=True))
        self.assertEqual(len(entries), 2)
        self.assertEqual({e.path for e in entries}, {'a.txt', 'b.txt'})

    def test_dedupe_false_reports_every_occurrence(self):
        rule = Rule(name='big', match_size=SizeCondition('>', 10, 'KB'))
        first = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c1', size_bytes=100 * KB)
        second = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c2', size_bytes=100 * KB)
        entries = evaluate_blobs(
            [first, second], [rule], EvaluationConfig(dedupe_blobs=False))
        self.assertEqual(len(entries), 2)

    def test_dedupe_keeps_earliest_when_first_violates_and_second_would_not(self):
        rule = Rule(name='big', match_size=SizeCondition('>', 10, 'KB'))
        first = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c1', size_bytes=100 * KB)
        second = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c2', size_bytes=1 * KB)
        entries = evaluate_blobs(
            [first, second], [rule], EvaluationConfig(dedupe_blobs=True))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].commit_sha, 'c1')


class TestUnknownSizeWarning(unittest.TestCase):
    """The "could not read the size" warning fires at most once per file version."""

    def test_warns_when_size_condition_would_have_run(self):
        rule = Rule(name='big-log', match_extensions=['log'],
                    match_size=SizeCondition('>', 50, 'KB'))
        blob = make_blob(path='a.log', blob_sha='sha1', commit_sha='c1' * 20, size_bytes=None)
        stream = io.StringIO()
        entries = evaluate_blobs([blob], [rule], EvaluationConfig(), stream=stream)
        self.assertEqual(entries, [])  # unknown size never satisfies a size condition
        output = stream.getvalue()
        self.assertIn('::warning::', output)
        self.assertIn('could not read the size of a.log', output)
        self.assertIn('c1c1c1c', output)  # short sha

    def test_no_warning_when_rule_would_not_have_matched_anyway(self):
        rule = Rule(name='big-log', match_extensions=['log'],
                    match_size=SizeCondition('>', 50, 'KB'))
        blob = make_blob(path='a.txt', size_bytes=None)  # wrong extension
        stream = io.StringIO()
        evaluate_blobs([blob], [rule], EvaluationConfig(), stream=stream)
        self.assertEqual(stream.getvalue(), '')

    def test_no_warning_when_no_rule_has_a_size_condition(self):
        rule = Rule(name='r', match_extensions=['log'])
        blob = make_blob(path='a.log', size_bytes=None)
        stream = io.StringIO()
        evaluate_blobs([blob], [rule], EvaluationConfig(), stream=stream)
        self.assertEqual(stream.getvalue(), '')

    def test_warning_fires_at_most_once_per_file_version(self):
        rule1 = Rule(name='r1', match_size=SizeCondition('>', 1, 'KB'), action='warn')
        rule2 = Rule(name='r2', match_size=SizeCondition('>', 2, 'KB'), action='warn')
        blob = make_blob(path='a.log', size_bytes=None)
        stream = io.StringIO()
        evaluate_blobs([blob], [rule1, rule2], EvaluationConfig(), stream=stream)
        self.assertEqual(stream.getvalue().count('::warning::'), 1)

    def test_warning_fires_again_for_a_different_file_version(self):
        rule = Rule(name='r', match_size=SizeCondition('>', 1, 'KB'))
        first = make_blob(path='a.log', blob_sha='sha1', size_bytes=None)
        second = make_blob(path='b.log', blob_sha='sha2', size_bytes=None)
        stream = io.StringIO()
        evaluate_blobs([first, second], [rule], EvaluationConfig(), stream=stream)
        self.assertEqual(stream.getvalue().count('::warning::'), 2)


class TestInputRuleMessages(unittest.TestCase):
    """Hit messages for the three quick-start-input rules use today's established style."""

    def test_disallow_extensions_message(self):
        rule = Rule(name='disallow_extensions', match_extensions=['exe'],
                    action='error', is_input_rule=True)
        blob = make_blob(path='a/b.EXE')
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(entries[0].violations[0].message, "File extension '.EXE' is disallowed")

    def test_max_text_size_kb_message(self):
        rule = Rule(name='max_text_size_kb', match_binary=False,
                    match_size=SizeCondition('>', 1, 'KB'), action='error', is_input_rule=True)
        blob = make_blob(path='a.txt', size_bytes=int(2 * KB), is_binary=False)
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(
            entries[0].violations[0].message, "Text file size 2.0 KB exceeds 1 KB limit")

    def test_max_binary_size_kb_message(self):
        rule = Rule(name='max_binary_size_kb', match_binary=True,
                    match_size=SizeCondition('>', 1, 'KB'), action='error', is_input_rule=True)
        blob = make_blob(path='a.bin', size_bytes=int(2 * KB), is_binary=True)
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(
            entries[0].violations[0].message, "Binary file size 2.0 KB exceeds 1 KB limit")

    def test_input_rule_hits_are_flagged_as_such(self):
        rule = Rule(name='max_text_size_kb', match_binary=False,
                    match_size=SizeCondition('>', 1, 'KB'), action='error', is_input_rule=True)
        blob = make_blob(size_bytes=int(2 * KB))
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        violation = entries[0].violations[0]
        self.assertTrue(violation.is_input_rule)
        self.assertTrue(violation.has_size_condition)


class TestPolicyRuleMessages(unittest.TestCase):
    """Hit messages for a user-defined policy rule."""

    def test_uses_description_when_set(self):
        rule = Rule(name='big-log', description='Logs belong in the artifact store',
                    match_extensions=['log'], action='error')
        blob = make_blob(path='a.log')
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(entries[0].violations[0].message, 'Logs belong in the artifact store')

    def test_falls_back_to_matched_rule_wording_without_description(self):
        rule = Rule(name='big-log', match_extensions=['log'], action='error')
        blob = make_blob(path='a.log')
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(entries[0].violations[0].message, "Matched rule 'big-log'")

    def test_size_condition_appends_comparison_without_description(self):
        rule = Rule(name='big-log', match_extensions=['log'],
                    match_size=SizeCondition('>', 50, 'KB'), action='error')
        blob = make_blob(path='a.log', size_bytes=60000)  # ~58.6 KB
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(
            entries[0].violations[0].message, "Matched rule 'big-log' (58.6 KB > 50 KB)")

    def test_size_condition_appends_comparison_with_description(self):
        rule = Rule(name='big-log', description='Logs belong in the artifact store',
                    match_extensions=['log'], match_size=SizeCondition('>', 50, 'KB'),
                    action='error')
        blob = make_blob(path='a.log', size_bytes=60000)  # ~58.6 KB
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertEqual(
            entries[0].violations[0].message,
            "Logs belong in the artifact store (58.6 KB > 50 KB)")

    def test_size_operator_rendered_as_written(self):
        rule = Rule(name='r', match_extensions=['log'],
                    match_size=SizeCondition('<=', 6, 'MB'), action='error')
        blob = make_blob(path='a.log', size_bytes=1 * KB)
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertIn('<= 6 MB', entries[0].violations[0].message)

    def test_policy_rule_hits_are_not_flagged_as_input_rules(self):
        rule = Rule(name='r', match_extensions=['log'], action='error')
        blob = make_blob(path='a.log')
        entries = evaluate_blobs([blob], [rule], EvaluationConfig())
        self.assertFalse(entries[0].violations[0].is_input_rule)


class TestHasFailingViolations(unittest.TestCase):
    """fail_on semantics, including the empty-entries edge case."""

    def _entries(self, *severities):
        rules = [Rule(name=f'r{i}', match_extensions=['x'], action=severity)
                 for i, severity in enumerate(severities)]
        blob = make_blob(path='a.x')
        return evaluate_blobs([blob], rules, EvaluationConfig())

    def test_fail_on_error_true_with_error_violation(self):
        self.assertTrue(has_failing_violations(self._entries('error'), 'error'))

    def test_fail_on_error_false_with_only_warn_violations(self):
        self.assertFalse(has_failing_violations(self._entries('warn'), 'error'))

    def test_fail_on_error_false_with_empty_entries(self):
        self.assertFalse(has_failing_violations([], 'error'))

    def test_fail_on_any_true_with_warn_violation(self):
        self.assertTrue(has_failing_violations(self._entries('warn'), 'any'))

    def test_fail_on_any_true_with_error_violation(self):
        self.assertTrue(has_failing_violations(self._entries('error'), 'any'))

    def test_fail_on_any_false_with_empty_entries(self):
        self.assertFalse(has_failing_violations([], 'any'))

    def test_mixed_severities_in_one_entry_use_the_highest(self):
        entries = self._entries('warn', 'error')
        self.assertEqual(len(entries), 1)
        self.assertTrue(has_failing_violations(entries, 'error'))

    def test_invalid_fail_on_raises(self):
        with self.assertRaises(ValueError):
            has_failing_violations([], 'bogus')


class TestEvaluateBlobsAcceptsIterables(unittest.TestCase):
    """evaluate_blobs takes an Iterable, not necessarily a list, and preserves order."""

    def test_generator_input(self):
        rule = Rule(name='big', match_size=SizeCondition('>', 1, 'KB'))

        def gen():
            yield make_blob(path='a.txt', blob_sha='sha1', size_bytes=10 * KB)
            yield make_blob(path='b.txt', blob_sha='sha2', size_bytes=1)

        entries = evaluate_blobs(gen(), [rule], EvaluationConfig())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path, 'a.txt')


if __name__ == '__main__':
    unittest.main()
