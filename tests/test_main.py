"""
Integration tests for the main.py CLI pipeline, over real temporary git
repositories (see tests/test_base.py).

These exercise the full wiring: ref resolution -> merge-base -> blob
enumeration -> size/type augmentation -> policy evaluation -> reporting ->
exit code. Lower-level behavior of each stage (glob matching, evaluation
order, report formatting, ...) is already covered by
test_rule_engine.py/test_evaluator.py/test_reporting.py; these tests focus
on whether main.py wires the pieces together correctly.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from repo_size_guardian import main as main_module
from tests.test_base import GitRepoTestBase


def run_cli(argv):
    """
    Run the CLI's `main()` with the given args, capturing output.

    Args:
        argv: CLI arguments, excluding the program name.

    Returns:
        A `(exit_code, stdout, stderr)` tuple.
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    with mock.patch('sys.argv', ['repo-size-guardian'] + argv):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main_module.main()
    return exit_code, stdout.getvalue(), stderr.getvalue()


class TestCleanAndViolatingPR(GitRepoTestBase):
    """A clean PR passes; a PR introducing an oversized binary fails."""

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')

    def test_clean_pr_exits_zero(self):
        self.helper.commit_file('notes.txt', 'just some notes', 'Add notes')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '1000', '--max-binary-size-kb', '1000',
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)

    def test_oversized_binary_exits_one(self):
        self.helper.create_and_commit_file(
            'big.bin', b'\x00' * (300 * 1024), 'Add oversized binary')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-binary-size-kb', '100',
        ])

        self.assertEqual(exit_code, 1)
        self.assertIn('big.bin', stdout)
        self.assertIn('ERROR', stdout)


class TestFailOnSeverity(GitRepoTestBase):
    """fail_on=warn vs fail_on=error changes the exit code for a warn-only violation."""

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.create_file('policy.yml', (
            "rules:\n"
            "  - id: warn-on-warnme\n"
            "    match:\n"
            "      extensions: [\"warnme\"]\n"
            "    action: \"warn\"\n"
        ))
        self.helper.commit_file('thing.warnme', 'content', 'Add a .warnme file')

    def test_fail_on_error_does_not_fail_on_warn_violation(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml', '--fail-on', 'error',
        ])
        self.assertEqual(exit_code, 0)
        self.assertIn('WARN', stdout)

    def test_fail_on_warn_fails_on_warn_violation(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml', '--fail-on', 'warn',
        ])
        self.assertEqual(exit_code, 1)
        self.assertIn('WARN', stdout)


class TestScanModeHistoryVsDiff(GitRepoTestBase):
    """
    history mode catches a blob added and then deleted within the PR;
    diff mode does not. This is the tool's entire reason to exist.
    """

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.create_and_commit_file(
            'big.bin', b'\x00' * (300 * 1024), 'Add oversized binary')
        self.helper.delete_file('big.bin', 'Remove it again')

    def test_history_mode_catches_transient_blob(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'history', '--max-binary-size-kb', '100',
        ])
        self.assertEqual(exit_code, 1)
        self.assertIn('big.bin', stdout)

    def test_diff_mode_misses_transient_blob(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--max-binary-size-kb', '100',
        ])
        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)


class TestOldestFirstDedupeOrdering(GitRepoTestBase):
    """
    evaluate_blobs keeps the FIRST occurrence of a (blob_sha, path) pair and
    does not sort; main.py must feed it blobs oldest-commit-first (list_commits
    is newest-first) so "first occurrence" means "earliest in history".
    """

    def test_dedupe_keeps_earliest_commit(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')

        oversized = 'X' * 2000  # ~1.95 KB, over a 1 KB threshold
        small = 'y'

        first_sha = self.helper.commit_file('file.txt', oversized, 'Introduce oversized content')
        self.helper.commit_file('file.txt', small, 'Shrink it')
        self.helper.commit_file('file.txt', oversized, 'Restore the exact same oversized content')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '1',
        ])

        self.assertEqual(exit_code, 1)
        # Exactly one violation should be reported (deduped on (blob_sha,
        # path)), and it must be attributed to the EARLIEST commit that
        # introduced that content, not the later one that happens to
        # restore identical (and therefore identically-SHA'd) content.
        violation_lines = [
            line for line in stdout.splitlines()
            if 'file.txt' in line and line.strip().startswith(('ERROR', 'WARN'))
        ]
        self.assertEqual(len(violation_lines), 1)
        self.assertIn(first_sha[:7], violation_lines[0])

    def test_dedupe_disabled_reports_every_occurrence(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')

        oversized = 'X' * 2000
        small = 'y'

        self.helper.commit_file('file.txt', oversized, 'Introduce oversized content')
        self.helper.commit_file('file.txt', small, 'Shrink it')
        self.helper.commit_file('file.txt', oversized, 'Restore the exact same oversized content')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '1', '--dedupe-blobs', 'false',
        ])

        self.assertEqual(exit_code, 1)
        violation_lines = [
            line for line in stdout.splitlines()
            if 'file.txt' in line and line.strip().startswith(('ERROR', 'WARN'))
        ]
        self.assertEqual(len(violation_lines), 2)


class TestShallowClonePreflight(unittest.TestCase):
    """A shallow clone fails fast with exit code 2 and an actionable message."""

    def setUp(self):
        self.original_cwd = os.getcwd()
        self.source_dir = tempfile.mkdtemp()
        self.shallow_dir = tempfile.mkdtemp()

        def run_git(cwd, *args):
            subprocess.run(['git'] + list(args), cwd=cwd, capture_output=True,
                           text=True, check=True)

        run_git(self.source_dir, 'init', '-q', '--initial-branch=main')
        run_git(self.source_dir, 'config', 'user.name', 'Test User')
        run_git(self.source_dir, 'config', 'user.email', 'test@example.com')
        with open(os.path.join(self.source_dir, 'a.txt'), 'w') as handle:
            handle.write('one')
        run_git(self.source_dir, 'add', 'a.txt')
        run_git(self.source_dir, 'commit', '-q', '-m', 'one')
        with open(os.path.join(self.source_dir, 'a.txt'), 'w') as handle:
            handle.write('two')
        run_git(self.source_dir, 'add', 'a.txt')
        run_git(self.source_dir, 'commit', '-q', '-m', 'two')

        subprocess.run(
            ['git', 'clone', '-q', '--depth', '1', 'file://' + self.source_dir, self.shallow_dir],
            capture_output=True, text=True, check=True,
        )
        os.chdir(self.shallow_dir)

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.source_dir, ignore_errors=True)
        shutil.rmtree(self.shallow_dir, ignore_errors=True)

    def test_shallow_clone_exits_two_with_actionable_message(self):
        exit_code, _stdout, stderr = run_cli(['--base-ref', 'HEAD~1', '--head-ref', 'HEAD'])
        self.assertEqual(exit_code, 2)
        self.assertIn('fetch-depth: 0', stderr)
        self.assertIn('shallow', stderr.lower())

    def test_shallow_clone_emits_error_annotation_without_bug_wording(self):
        # A shallow clone is a configuration mistake (missing
        # fetch-depth: 0), not a bug in this tool -- it must get the
        # ::error:: annotation treatment but not the internal-error wording.
        exit_code, stdout, _stderr = run_cli(['--base-ref', 'HEAD~1', '--head-ref', 'HEAD'])
        self.assertEqual(exit_code, 2)
        self.assertIn('::error::', stdout)
        annotation_line = next(
            line for line in stdout.splitlines() if line.startswith('::error::'))
        self.assertIn('fetch-depth', annotation_line)
        self.assertNotIn('bug', annotation_line.lower())
        self.assertNotIn('repo-size-guardian/issues', annotation_line)


class TestMalformedPolicy(GitRepoTestBase):
    """A malformed policy file is a configuration error: exit code 2."""

    def test_malformed_policy_exits_two(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')
        self.helper.create_file('bad_policy.yml', 'not_a_real_key: [unterminated\n')

        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'bad_policy.yml',
        ])

        self.assertEqual(exit_code, 2)
        self.assertIn('bad_policy.yml', stderr)


class TestEmptyConfigWarning(GitRepoTestBase):
    """No policy and no thresholds set -> a prominent warning, but exit 0."""

    def test_empty_config_warns_but_does_not_fail(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn('::warning::', stdout)
        self.assertIn('nothing is being enforced', stdout.lower())

    def test_configured_thresholds_suppress_the_warning(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '1000',
        ])

        self.assertEqual(exit_code, 0)
        self.assertNotIn('::warning::', stdout)


class TestResolveRefs(unittest.TestCase):
    """Unit tests for the ref-resolution priority chain (no git repo needed)."""

    def _args(self, base_ref=None, head_ref=None):
        return argparse.Namespace(base_ref=base_ref, head_ref=head_ref)

    def test_explicit_cli_args_win(self):
        event = {'pull_request': {'base': {'sha': 'eventbase'}, 'head': {'sha': 'eventhead'}}}
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump(event, handle)
            event_path = handle.name
        try:
            env = {'GITHUB_EVENT_PATH': event_path, 'GITHUB_BASE_REF': 'main'}
            with mock.patch.dict(os.environ, env, clear=True):
                base, head = main_module.resolve_refs(
                    self._args(base_ref='cli-base', head_ref='cli-head'))
            self.assertEqual(base, 'cli-base')
            self.assertEqual(head, 'cli-head')
        finally:
            os.unlink(event_path)

    def test_event_path_used_when_no_cli_args(self):
        event = {'pull_request': {'base': {'sha': 'eventbase'}, 'head': {'sha': 'eventhead'}}}
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump(event, handle)
            event_path = handle.name
        try:
            env = {'GITHUB_EVENT_PATH': event_path}
            with mock.patch.dict(os.environ, env, clear=True):
                base, head = main_module.resolve_refs(self._args())
            self.assertEqual(base, 'eventbase')
            self.assertEqual(head, 'eventhead')
        finally:
            os.unlink(event_path)

    def test_malformed_event_file_falls_through(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            handle.write('{not valid json')
            event_path = handle.name
        try:
            env = {'GITHUB_EVENT_PATH': event_path, 'GITHUB_BASE_REF': 'develop'}
            with mock.patch.dict(os.environ, env, clear=True):
                base, head = main_module.resolve_refs(self._args())
            self.assertEqual(base, 'origin/develop')
            self.assertEqual(head, 'HEAD')
        finally:
            os.unlink(event_path)

    def test_missing_event_path_falls_through_to_origin_base_ref(self):
        with mock.patch.dict(os.environ, {'GITHUB_BASE_REF': 'develop'}, clear=True):
            base, head = main_module.resolve_refs(self._args())
        self.assertEqual(base, 'origin/develop')
        self.assertEqual(head, 'HEAD')

    def test_unresolvable_base_ref_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(main_module.ConfigError):
                main_module.resolve_refs(self._args())

    def test_non_pull_request_event_names_the_event_in_the_error(self):
        # This tool is PR-only by design; the most common way to hit this
        # path is `on: push` (or another trigger) instead of
        # `on: pull_request`. The error should say so explicitly rather
        # than a generic "couldn't resolve a ref" message.
        with mock.patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'push'}, clear=True):
            with self.assertRaises(main_module.ConfigError) as caught:
                main_module.resolve_refs(self._args())
        message = str(caught.exception)
        self.assertIn('pull_request', message)
        self.assertIn("'push'", message)
        self.assertIn('on: pull_request', message)

    def test_unknown_event_name_falls_back_to_a_generic_message(self):
        # No $GITHUB_EVENT_NAME at all (e.g. a bare local/CI invocation with
        # no other resolution source available) must not crash building the
        # message, and should still mention that only pull_request works.
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(main_module.ConfigError) as caught:
                main_module.resolve_refs(self._args())
        self.assertIn('pull_request', str(caught.exception))


class TestNonPullRequestEventCli(GitRepoTestBase):
    """
    End-to-end: running the CLI with no explicit refs on a non-pull_request
    event must exit 2 with a message naming the actual triggering event,
    not a generic "couldn't resolve a ref" failure.
    """

    def test_push_event_exits_two_with_actionable_message(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')

        env = dict(os.environ)
        # A real push-triggered run would not have these set; strip them so
        # the CLI actually falls through to the new error path instead of
        # accidentally resolving a ref from the *test process's* own
        # environment.
        env.pop('GITHUB_EVENT_PATH', None)
        env.pop('GITHUB_BASE_REF', None)
        env['GITHUB_EVENT_NAME'] = 'push'

        with mock.patch.dict(os.environ, env, clear=True):
            exit_code, _stdout, stderr = run_cli(['--max-text-size-kb', '1000'])

        self.assertEqual(exit_code, 2)
        self.assertIn('pull_request', stderr)
        self.assertIn("'push'", stderr)


class TestBooleanParsing(unittest.TestCase):
    """--dedupe-blobs/--annotate-pr arrive as strings and must be parsed as such."""

    def test_true_like_values(self):
        for value in ('true', 'True', 'TRUE', '1', 'yes', 'YES'):
            self.assertTrue(main_module._parse_bool(value, '--x'), msg=value)

    def test_false_like_values(self):
        for value in ('false', 'False', 'FALSE', '0', 'no', 'NO'):
            self.assertFalse(main_module._parse_bool(value, '--x'), msg=value)

    def test_invalid_value_raises_config_error(self):
        with self.assertRaises(main_module.ConfigError):
            main_module._parse_bool('nope', '--dedupe-blobs')


class TestDiffModeUsesMergeBase(GitRepoTestBase):
    """
    diff mode must diff MERGE-BASE..head, not base-tip..head.

    A two-tree diff of the base branch tip against the PR head also reports
    every file the base branch changed after the PR branched: those files
    differ between the two trees even though the PR never touched them, and
    the PR-side (older) blob would be attributed to the PR. That is a false
    positive that blocks somebody's PR over a file they did not write --
    the most expensive possible failure mode for this tool. GitHub's own
    "Files changed" view uses the three-dot/merge-base diff for the same
    reason.
    """

    def setUp(self):
        super().setUp()
        # Base commit carries a large binary.
        self.helper.create_and_commit_file(
            'data.bin', b'\x00' * (300 * 1024), 'Base commit with big data.bin')
        self.helper.create_branch('feature')
        # The PR touches only notes.txt.
        self.helper.commit_file('notes.txt', 'just some notes', 'PR adds notes')
        # Meanwhile the base branch moves on and shrinks data.bin.
        self.helper.checkout('main')
        self.helper.create_and_commit_file('data.bin', b'\x00', 'Base shrinks data.bin')
        self.helper.checkout('feature')

    def test_diff_mode_does_not_report_a_file_only_base_changed(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--max-binary-size-kb', '100',
        ])
        self.assertNotIn('data.bin', stdout)
        self.assertEqual(exit_code, 0)

    def test_diff_mode_does_not_report_a_file_only_added_on_base(self):
        self.helper.checkout('main')
        self.helper.create_and_commit_file(
            'base_only.bin', b'\x01' * (400 * 1024), 'Base adds another big file')
        self.helper.checkout('feature')
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--max-binary-size-kb', '100',
        ])
        self.assertNotIn('base_only.bin', stdout)
        self.assertEqual(exit_code, 0)

    def test_diff_mode_still_reports_a_file_the_pr_added(self):
        # Guard against over-correcting the fix into a false negative.
        self.helper.create_and_commit_file(
            'pr_added.bin', b'\x02' * (500 * 1024), 'PR adds a big file')
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--max-binary-size-kb', '100',
        ])
        self.assertEqual(exit_code, 1)
        self.assertIn('pr_added.bin', stdout)

    def test_history_mode_is_unaffected_by_an_advanced_base(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'history', '--max-binary-size-kb', '100',
        ])
        self.assertNotIn('data.bin', stdout)
        self.assertEqual(exit_code, 0)


class TestCommitsScannedCount(GitRepoTestBase):
    """
    "Commits scanned" is the user's main sanity check that the action looked
    at the range they expected, so it must count the commits in the range,
    not just the ones that happened to touch a file.
    """

    def test_counts_commits_with_no_file_changes(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('a.txt', 'a', 'Commit 1')
        self.helper.run_git('commit', '--allow-empty', '-m', 'Empty commit')
        self.helper.commit_file('b.txt', 'b', 'Commit 3')

        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '1000',
        ])
        self.assertIn('Commits scanned: 3', stdout)

    def test_diff_mode_reports_the_real_commit_count(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('a.txt', 'a', 'Commit 1')
        self.helper.commit_file('b.txt', 'b', 'Commit 2')

        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--max-text-size-kb', '1000',
        ])
        self.assertIn('Commits scanned: 2', stdout)


class TestReportOrdering(GitRepoTestBase):
    """
    Blobs are fed oldest-commit-first for dedupe, but the files *within* one
    commit must keep their natural (git diff, path-sorted) order rather than
    being reversed along with the commits.
    """

    def test_files_within_a_commit_keep_their_order(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.create_file('a.txt', 'X' * 2000)
        self.helper.create_file('b.txt', 'X' * 3000)
        self.helper.run_git('add', 'a.txt', 'b.txt')
        self.helper.run_git('commit', '-m', 'Add both in one commit')
        self.helper.commit_file('c.txt', 'X' * 4000, 'Add c later')

        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '1',
        ])
        order = [line.split()[2] for line in stdout.splitlines()
                 if line.strip().startswith(('ERROR', 'WARN'))]
        self.assertEqual(order, ['a.txt', 'b.txt', 'c.txt'])


class TestNoMergeBase(GitRepoTestBase):
    """Unrelated histories produce a config error with an actionable message."""

    def test_unrelated_histories_exit_two_with_clear_message(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_orphan_branch('unrelated')
        self.helper.commit_file('other.txt', 'other', 'Unrelated root commit')

        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'unrelated',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn('merge base', stderr.lower())
        self.assertIn('fetch-depth: 0', stderr)


class TestNumericInputValidation(GitRepoTestBase):
    """Negative numeric inputs are typos, and must not degrade silently."""

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

    def test_negative_max_annotations_is_a_config_error(self):
        # `0 means unlimited` is implemented as `limit > 0`, so a negative
        # value would silently also mean unlimited.
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-annotations', '-1',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn('--max-annotations', stderr)

    def test_negative_size_threshold_is_a_config_error(self):
        # A negative threshold would make every single file a violation.
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '-5',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn('--max-text-size-kb', stderr)

    def test_zero_max_annotations_is_accepted_as_unlimited(self):
        exit_code, _stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-annotations', '0', '--max-text-size-kb', '1000',
        ])
        self.assertEqual(exit_code, 0)

    def test_non_integer_max_annotations_exits_two(self):
        with self.assertRaises(SystemExit) as caught:
            run_cli(['--base-ref', 'main', '--head-ref', 'feature',
                     '--max-annotations', 'lots'])
        self.assertEqual(caught.exception.code, 2)


class TestUnexpectedExceptionExitCode(GitRepoTestBase):
    """
    An internal crash must not be reported as exit code 1.

    Exit 1 means "violations found", so an uncaught exception escaping
    main() would make a workflow -- and a human reading the log -- treat a
    bug in this tool as a policy violation in the PR.
    """

    def test_internal_error_exits_two_not_one(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        with mock.patch.object(main_module, 'evaluate_blobs',
                               side_effect=RuntimeError('boom')):
            exit_code, _stdout, stderr = run_cli([
                '--base-ref', 'main', '--head-ref', 'feature',
                '--max-text-size-kb', '1000',
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn('internal error', stderr)
        self.assertIn('RuntimeError', stderr)

    def test_internal_error_emits_error_annotation_with_version_and_issue_link(self):
        # A plain log line inside a (typically collapsed) step is easy to
        # miss; an internal crash must also surface as a GitHub ::error::
        # annotation, carrying the version (for an actionable bug report)
        # and a direct link to the issue tracker.
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        with mock.patch.object(main_module, 'evaluate_blobs',
                               side_effect=RuntimeError('boom')):
            exit_code, stdout, _stderr = run_cli([
                '--base-ref', 'main', '--head-ref', 'feature',
                '--max-text-size-kb', '1000',
            ])

        self.assertEqual(exit_code, 2)
        self.assertIn('::error::', stdout)
        annotation_line = next(
            line for line in stdout.splitlines() if line.startswith('::error::'))
        self.assertIn(main_module.__version__, annotation_line)
        self.assertIn(
            'https://github.com/technic960183/repo-size-guardian/issues',
            annotation_line,
        )
        self.assertIn('RuntimeError', annotation_line)
        self.assertIn('bug', annotation_line.lower())

    def test_internal_error_does_not_leave_partial_github_output_or_summary(self):
        # report() is only ever reached after the pipeline succeeds, so a
        # crash earlier in the pipeline (as simulated here) must leave
        # GITHUB_OUTPUT / GITHUB_STEP_SUMMARY exactly as it found them --
        # never a partially-written entry a downstream step (e.g. one
        # gated on `if: always()`) could misread as real scan results.
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, 'github_output')
            summary_path = os.path.join(tmp_dir, 'github_step_summary')
            # Pre-seed both files the way a real job would leave them
            # before this step runs (empty, but present).
            open(output_path, 'w', encoding='utf-8').close()
            open(summary_path, 'w', encoding='utf-8').close()

            env = dict(os.environ)
            env['GITHUB_OUTPUT'] = output_path
            env['GITHUB_STEP_SUMMARY'] = summary_path

            with mock.patch.object(main_module, 'evaluate_blobs',
                                   side_effect=RuntimeError('boom')):
                with mock.patch.dict(os.environ, env, clear=True):
                    exit_code, _stdout, _stderr = run_cli([
                        '--base-ref', 'main', '--head-ref', 'feature',
                        '--max-text-size-kb', '1000',
                    ])

            self.assertEqual(exit_code, 2)
            with open(output_path, encoding='utf-8') as handle:
                self.assertEqual(handle.read(), '')
            with open(summary_path, encoding='utf-8') as handle:
                self.assertEqual(handle.read(), '')


class TestConfigErrorAnnotation(GitRepoTestBase):
    """
    A configuration error (the user's own mistake) is just as easy to miss
    inside a collapsed step as an internal crash, so it also gets a
    ::error:: annotation -- but it must never use the "bug"/issue-tracker
    wording reserved for a genuine internal error.
    """

    def test_malformed_policy_emits_error_annotation_without_bug_wording(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')
        self.helper.create_file('bad_policy.yml', 'not_a_real_key: [unterminated\n')

        exit_code, stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'bad_policy.yml',
        ])

        self.assertEqual(exit_code, 2)
        self.assertIn('::error::', stdout)
        annotation_line = next(
            line for line in stdout.splitlines() if line.startswith('::error::'))
        self.assertIn('bad_policy.yml', annotation_line)
        # Must not steer the user toward filing a bug report -- this is
        # their configuration to fix, not ours.
        self.assertNotIn('bug', annotation_line.lower())
        self.assertNotIn('repo-size-guardian/issues', annotation_line)
        self.assertNotIn('bug', stderr.lower())

    def test_negative_threshold_emits_error_annotation_without_bug_wording(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '-5',
        ])

        self.assertEqual(exit_code, 2)
        self.assertIn('::error::', stdout)
        annotation_line = next(
            line for line in stdout.splitlines() if line.startswith('::error::'))
        self.assertIn('--max-text-size-kb', annotation_line)
        self.assertNotIn('bug', annotation_line.lower())
        self.assertNotIn('repo-size-guardian/issues', annotation_line)


class TestMimeMatchingUnavailableWarning(GitRepoTestBase):
    """
    MIME matching silently matches nothing without the `file` command.

    `detect_blob_type` only ever produces a mime_type from `file --mime`;
    the content-heuristic fallback reports None, and matches_mime(None, ...)
    is always False. On a runner without `file`, a policy built around
    `disallow.mime_types` would therefore pass every PR clean -- a false
    negative indistinguishable from a genuinely clean run.
    """

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

    def _run_without_file_command(self, policy_text):
        self.helper.create_file('policy.yml', policy_text)
        with mock.patch.object(main_module.shutil, 'which', return_value=None):
            return run_cli(['--base-ref', 'main', '--head-ref', 'feature',
                            '--policy-path', 'policy.yml'])

    def test_warns_when_policy_uses_disallow_mime_types(self):
        _exit_code, stdout, _stderr = self._run_without_file_command(
            "disallow:\n  mime_types: [\"application/x-dosexec\"]\n")
        self.assertIn('::warning::', stdout)
        self.assertIn('`file` command', stdout)

    def test_warns_when_a_rule_matches_on_mime_types(self):
        _exit_code, stdout, _stderr = self._run_without_file_command(
            "rules:\n"
            "  - id: no-executables\n"
            "    match:\n"
            "      mime_types: [\"application/x-executable\"]\n")
        self.assertIn('::warning::', stdout)

    def test_no_warning_when_the_policy_does_not_use_mime(self):
        _exit_code, stdout, _stderr = self._run_without_file_command(
            "disallow:\n  extensions: [\"exe\"]\n")
        self.assertNotIn('`file` command', stdout)

    def test_no_warning_when_the_file_command_is_present(self):
        self.helper.create_file(
            'policy.yml', "disallow:\n  mime_types: [\"application/x-dosexec\"]\n")
        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml'])
        self.assertNotIn('`file` command', stdout)


if __name__ == '__main__':
    unittest.main()
