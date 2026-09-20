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


if __name__ == '__main__':
    unittest.main()
