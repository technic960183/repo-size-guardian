"""
Test suite for the evaluator module.

Covers the per-blob evaluation order from PRD §3.2 (skip rules, ignore/allow
precedence, rule terminality, disallow lists, global thresholds), the
`(blob_sha, path)` dedupe behavior from PRD §3.3, and `has_failing_violations`.
"""

import unittest

from repo_size_guardian.evaluator import EvaluationConfig, evaluate_blobs, has_failing_violations
from repo_size_guardian.models import Blob
from repo_size_guardian.rule_engine import Policy, Rule

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


class TestSkipDeletionsAndEmptySha(unittest.TestCase):
    """Step 0: deletions and blobs with no content are skipped entirely."""

    def test_deleted_blob_is_skipped(self):
        blob = make_blob(status='D', blob_sha='', size_bytes=10 * KB * KB)
        config = EvaluationConfig(max_text_size_kb=1)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(violations, [])

    def test_deleted_blob_with_nonempty_sha_is_still_skipped(self):
        # Defensive: is_deleted alone must be enough to skip, regardless of
        # whether a blob_sha happens to be present.
        blob = make_blob(status='D', blob_sha='deadbeef', size_bytes=10 * KB * KB)
        config = EvaluationConfig(max_text_size_kb=1)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(violations, [])

    def test_empty_blob_sha_is_skipped_even_if_not_marked_deleted(self):
        blob = make_blob(status='A', blob_sha='', size_bytes=10 * KB * KB)
        config = EvaluationConfig(max_text_size_kb=1)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(violations, [])

    def test_normal_blob_is_not_skipped(self):
        blob = make_blob(status='A', blob_sha='sha1', size_bytes=10 * KB * KB)
        config = EvaluationConfig(max_text_size_kb=1)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(len(violations), 1)


class TestIgnoreAndAllowPrecedence(unittest.TestCase):
    """Step 1/2: ignore beats everything; allow_globs beats rules and disallow."""

    def test_ignore_globs_skips_before_disallow(self):
        policy = Policy(ignore_globs=['*.secret'], disallow_extensions=['secret'])
        blob = make_blob(path='a.secret')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(violations, [])

    def test_ignore_paths_skips_exact_path(self):
        policy = Policy(ignore_paths=['data/big.bin'], disallow_extensions=['bin'])
        blob = make_blob(path='data/big.bin')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(violations, [])

    def test_allow_globs_beats_disallow_list(self):
        policy = Policy(disallow_extensions=['ipynb'], allow_globs=['notebooks/*.ipynb'])
        blob = make_blob(path='notebooks/a.ipynb')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(violations, [])

    def test_allow_globs_beats_matching_rule(self):
        rule = Rule(id='big-binary', match_binary=True, size_over_kb=None, action='error')
        policy = Policy(rules=[rule], allow_globs=['*.bin'])
        blob = make_blob(path='a.bin', is_binary=True)
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(violations, [])

    def test_allow_globs_beats_global_threshold(self):
        policy = Policy(allow_globs=['huge.txt'])
        blob = make_blob(path='huge.txt', size_bytes=1000 * KB)
        config = EvaluationConfig(max_text_size_kb=10)
        violations = evaluate_blobs([blob], policy, config)
        self.assertEqual(violations, [])


class TestRuleTerminality(unittest.TestCase):
    """Step 3: a matched rule is terminal, whether or not it violates."""

    def test_unconditional_rule_always_violates(self):
        rule = Rule(id='no-notebooks', match_extensions=['ipynb'], size_over_kb=None,
                    action='error')
        policy = Policy(rules=[rule])
        blob = make_blob(path='a.ipynb', size_bytes=1)
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.category, 'rule')
        self.assertEqual(v.severity, 'error')
        self.assertEqual(v.rule_name, 'no-notebooks')
        self.assertIsNone(v.threshold_kb)

    def test_gated_rule_under_gate_produces_no_violation_and_does_not_fall_through(self):
        # The critical terminality case: the blob matches a rule, is under
        # that rule's size gate, has a disallowed extension, AND exceeds the
        # global threshold. Because the rule matched, none of that matters:
        # the blob must produce NO violation at all.
        rule = Rule(id='big-bin', match_extensions=['bin'], size_over_kb=1000,
                    action='error')
        policy = Policy(
            rules=[rule],
            disallow_extensions=['bin'],
            max_binary_size_kb=1,
        )
        blob = make_blob(path='a.bin', size_bytes=10 * KB, is_binary=True)  # 10 KB < 1000 KB gate
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(violations, [], "matched rule under its gate must not fall through")

    def test_gated_rule_over_gate_violates_with_rule_category_only(self):
        rule = Rule(id='big-bin', match_extensions=['bin'], size_over_kb=5, action='warn')
        policy = Policy(rules=[rule], disallow_extensions=['bin'], max_binary_size_kb=1)
        blob = make_blob(path='a.bin', size_bytes=10 * KB, is_binary=True)  # 10 KB > 5 KB gate
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.category, 'rule')
        self.assertEqual(v.severity, 'warn')
        self.assertEqual(v.rule_name, 'big-bin')
        self.assertEqual(v.threshold_kb, 5)

    def test_first_matching_rule_wins_in_declaration_order(self):
        rule1 = Rule(id='first', match_extensions=['bin'], size_over_kb=None, action='warn')
        rule2 = Rule(id='second', match_extensions=['bin'], size_over_kb=None, action='error')
        policy = Policy(rules=[rule1, rule2])
        blob = make_blob(path='a.bin')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule_name, 'first')
        self.assertEqual(violations[0].severity, 'warn')

    def test_non_matching_rule_falls_through_to_later_rule(self):
        rule1 = Rule(id='only-md', match_extensions=['md'], size_over_kb=None, action='error')
        rule2 = Rule(id='catch-bin', match_extensions=['bin'], size_over_kb=None, action='warn')
        policy = Policy(rules=[rule1, rule2])
        blob = make_blob(path='a.bin')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule_name, 'catch-bin')

    def test_no_matching_rule_falls_through_to_disallow(self):
        rule = Rule(id='only-md', match_extensions=['md'], size_over_kb=None, action='error')
        policy = Policy(rules=[rule], disallow_extensions=['bin'])
        blob = make_blob(path='a.bin')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].category, 'disallowed')
        self.assertEqual(violations[0].rule_name, 'disallow.extensions')


class TestDisallowLists(unittest.TestCase):
    """Step 4: disallow lists are unconditional matches and terminal."""

    def test_disallow_extensions(self):
        policy = Policy(disallow_extensions=['ipynb'])
        blob = make_blob(path='notebook.ipynb')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.rule_name, 'disallow.extensions')
        self.assertEqual(v.category, 'disallowed')
        self.assertEqual(v.severity, 'error')

    def test_disallow_globs(self):
        policy = Policy(disallow_globs=['**/*.secret'])
        blob = make_blob(path='a/b/x.secret')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule_name, 'disallow.globs')
        self.assertEqual(violations[0].category, 'disallowed')

    def test_disallow_mime_types(self):
        policy = Policy(disallow_mime_types=['application/x-dosexec'])
        blob = make_blob(path='a.exe', mime_type='application/x-dosexec')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule_name, 'disallow.mime_types')
        self.assertEqual(violations[0].category, 'disallowed')

    def test_disallow_match_is_terminal_over_global_threshold(self):
        policy = Policy(disallow_extensions=['bin'], max_binary_size_kb=1000)
        blob = make_blob(path='a.bin', size_bytes=1, is_binary=True)  # tiny, well under threshold
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].category, 'disallowed')

    def test_no_disallow_match_falls_through_to_threshold(self):
        policy = Policy(disallow_extensions=['exe'], max_text_size_kb=1)
        blob = make_blob(path='a.txt', size_bytes=2 * KB)
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].category, 'size')

    def test_extension_precedence_over_mime_when_both_match(self):
        # Internal precedence choice (extensions checked before globs before
        # mime_types) since the contract does not otherwise order the three
        # disallow lists; pinned down here so behavior stays deterministic.
        policy = Policy(disallow_extensions=['exe'], disallow_mime_types=['application/x-dosexec'])
        blob = make_blob(path='a.exe', mime_type='application/x-dosexec')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule_name, 'disallow.extensions')


class TestGlobalThresholds(unittest.TestCase):
    """Step 5: policy thresholds override config; strictly greater-than."""

    def test_text_over_config_threshold_violates(self):
        config = EvaluationConfig(max_text_size_kb=10)
        blob = make_blob(path='a.txt', size_bytes=20 * KB, is_binary=False)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.category, 'size')
        self.assertEqual(v.severity, 'error')
        self.assertEqual(v.rule_name, 'threshold.max_text_size_kb')
        self.assertEqual(v.threshold_kb, 10)

    def test_binary_over_config_threshold_violates(self):
        config = EvaluationConfig(max_binary_size_kb=10)
        blob = make_blob(path='a.bin', size_bytes=20 * KB, is_binary=True)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule_name, 'threshold.max_binary_size_kb')

    def test_is_binary_none_is_treated_as_text(self):
        config = EvaluationConfig(max_text_size_kb=10, max_binary_size_kb=1000)
        blob = make_blob(path='a.dat', size_bytes=20 * KB, is_binary=None)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule_name, 'threshold.max_text_size_kb')

    def test_policy_threshold_overrides_config_threshold(self):
        policy = Policy(max_text_size_kb=10)
        config = EvaluationConfig(max_text_size_kb=1000)
        blob = make_blob(path='a.txt', size_bytes=20 * KB, is_binary=False)
        violations = evaluate_blobs([blob], policy, config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].threshold_kb, 10)

    def test_policy_threshold_none_falls_back_to_config(self):
        policy = Policy(max_text_size_kb=None)
        config = EvaluationConfig(max_text_size_kb=10)
        blob = make_blob(path='a.txt', size_bytes=20 * KB, is_binary=False)
        violations = evaluate_blobs([blob], policy, config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].threshold_kb, 10)

    def test_both_none_means_no_limit(self):
        policy = Policy(max_text_size_kb=None)
        config = EvaluationConfig(max_text_size_kb=None)
        blob = make_blob(path='a.txt', size_bytes=1000 * KB * KB, is_binary=False)
        violations = evaluate_blobs([blob], policy, config)
        self.assertEqual(violations, [])

    def test_exactly_at_limit_passes(self):
        config = EvaluationConfig(max_text_size_kb=10)
        blob = make_blob(path='a.txt', size_bytes=10 * KB, is_binary=False)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(violations, [])

    def test_one_byte_under_limit_passes(self):
        config = EvaluationConfig(max_text_size_kb=10)
        blob = make_blob(path='a.txt', size_bytes=10 * KB - 1, is_binary=False)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(violations, [])

    def test_one_byte_over_limit_violates(self):
        config = EvaluationConfig(max_text_size_kb=10)
        blob = make_blob(path='a.txt', size_bytes=10 * KB + 1, is_binary=False)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].threshold_kb, 10)

    def test_unknown_size_never_violates(self):
        config = EvaluationConfig(max_text_size_kb=1)
        blob = make_blob(path='a.txt', size_bytes=None, is_binary=False)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self.assertEqual(violations, [])


class TestDedupe(unittest.TestCase):
    """PRD §3.3: dedupe on (blob_sha, path), keep the first occurrence."""

    def test_same_sha_and_path_deduped_keeps_first_regardless_of_second(self):
        # First occurrence is small (no violation); second occurrence (same
        # sha+path) is large. Because dedupe skips the second entirely, the
        # result must show NO violation -- not "evaluate all, keep first
        # violation found".
        config = EvaluationConfig(max_text_size_kb=10, dedupe_blobs=True)
        first = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c1', size_bytes=1 * KB)
        second = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c2', size_bytes=100 * KB)
        violations = evaluate_blobs([first, second], Policy.empty(), config)
        self.assertEqual(violations, [])

    def test_same_sha_different_path_yields_two_evaluations(self):
        config = EvaluationConfig(max_text_size_kb=10, dedupe_blobs=True)
        first = make_blob(path='a.txt', blob_sha='sha1', size_bytes=100 * KB)
        second = make_blob(path='b.txt', blob_sha='sha1', size_bytes=100 * KB)
        violations = evaluate_blobs([first, second], Policy.empty(), config)
        self.assertEqual(len(violations), 2)
        self.assertEqual({v.path for v in violations}, {'a.txt', 'b.txt'})

    def test_dedupe_false_reports_every_occurrence(self):
        config = EvaluationConfig(max_text_size_kb=10, dedupe_blobs=False)
        first = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c1', size_bytes=100 * KB)
        second = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c2', size_bytes=100 * KB)
        violations = evaluate_blobs([first, second], Policy.empty(), config)
        self.assertEqual(len(violations), 2)

    def test_dedupe_keeps_earliest_when_first_violates_and_second_would_not(self):
        config = EvaluationConfig(max_text_size_kb=10, dedupe_blobs=True)
        first = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c1', size_bytes=100 * KB)
        second = make_blob(path='a.txt', blob_sha='sha1', commit_sha='c2', size_bytes=1 * KB)
        violations = evaluate_blobs([first, second], Policy.empty(), config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].commit_sha, 'c1')


class TestHasFailingViolations(unittest.TestCase):
    """fail_on semantics, including the empty-violations edge case."""

    def _violation(self, severity):
        blob = make_blob()
        from repo_size_guardian.models import Violation
        return Violation(blob=blob, rule_name='x', message='m', severity=severity)

    def test_fail_on_error_true_with_error_violation(self):
        self.assertTrue(has_failing_violations([self._violation('error')], 'error'))

    def test_fail_on_error_false_with_only_warn_violations(self):
        self.assertFalse(has_failing_violations([self._violation('warn')], 'error'))

    def test_fail_on_error_false_with_empty_violations(self):
        self.assertFalse(has_failing_violations([], 'error'))

    def test_fail_on_warn_true_with_warn_violation(self):
        self.assertTrue(has_failing_violations([self._violation('warn')], 'warn'))

    def test_fail_on_warn_true_with_error_violation(self):
        self.assertTrue(has_failing_violations([self._violation('error')], 'warn'))

    def test_fail_on_warn_false_with_empty_violations(self):
        self.assertFalse(has_failing_violations([], 'warn'))

    def test_mixed_violations_fail_on_error(self):
        violations = [self._violation('warn'), self._violation('warn'), self._violation('error')]
        self.assertTrue(has_failing_violations(violations, 'error'))

    def test_invalid_fail_on_raises(self):
        with self.assertRaises(ValueError):
            has_failing_violations([], 'bogus')


class TestViolationMessageStyle(unittest.TestCase):
    """Sanity checks on the models.Violation message contract (one line, no trailing period)."""

    def _assert_message_style(self, message):
        self.assertIsInstance(message, str)
        self.assertNotIn('\n', message)
        self.assertFalse(message.endswith('.'))

    def test_rule_violation_message_style(self):
        rule = Rule(id='r1', description='desc', match_extensions=['bin'], action='error')
        policy = Policy(rules=[rule])
        blob = make_blob(path='a.bin')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self._assert_message_style(violations[0].message)

    def test_disallow_violation_message_style(self):
        policy = Policy(disallow_extensions=['bin'])
        blob = make_blob(path='a.bin')
        violations = evaluate_blobs([blob], policy, EvaluationConfig())
        self._assert_message_style(violations[0].message)

    def test_threshold_violation_message_style(self):
        config = EvaluationConfig(max_text_size_kb=1)
        blob = make_blob(path='a.txt', size_bytes=10 * KB)
        violations = evaluate_blobs([blob], Policy.empty(), config)
        self._assert_message_style(violations[0].message)


class TestEvaluateBlobsAcceptsIterables(unittest.TestCase):
    """evaluate_blobs takes an Iterable, not necessarily a list, and preserves order."""

    def test_generator_input(self):
        config = EvaluationConfig(max_text_size_kb=1)

        def gen():
            yield make_blob(path='a.txt', blob_sha='sha1', size_bytes=10 * KB)
            yield make_blob(path='b.txt', blob_sha='sha2', size_bytes=1)

        violations = evaluate_blobs(gen(), Policy.empty(), config)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].path, 'a.txt')


if __name__ == '__main__':
    unittest.main()
