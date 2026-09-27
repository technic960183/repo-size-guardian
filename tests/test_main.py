"""
Integration tests for the main.py CLI pipeline, over real temporary git
repositories (see tests/test_base.py).

These exercise the full wiring: input/policy validation -> ref resolution ->
merge-base -> blob enumeration -> size/type augmentation -> rule evaluation
-> reporting -> exit code. Lower-level behavior of each stage (glob
matching, evaluation order, report formatting, ...) is already covered by
test_rule_engine.py/test_evaluator.py/test_reporting.py; these tests focus
on whether main.py wires the pieces together correctly, including the
policy/quick-start-input exclusivity and the exit code table (0/1/2/3).
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

import yaml

from repo_size_guardian import main as main_module
from tests.test_base import GitRepoTestBase, isolate_github_environment


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
        self.assertIn('Binary file size', stdout)


class TestDisallowExtensionsInput(GitRepoTestBase):
    """The `--disallow-extensions` quick-start input acts as a rule."""

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')

    def test_disallowed_extension_fails_the_job(self):
        self.helper.commit_file('a.exe', 'binary-ish', 'Add an exe')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--disallow-extensions', 'exe, dll',
        ])

        self.assertEqual(exit_code, 1)
        self.assertIn("File extension '.exe' is disallowed", stdout)

    def test_extension_not_in_the_list_passes(self):
        self.helper.commit_file('a.txt', 'just text', 'Add a text file')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--disallow-extensions', 'exe, dll',
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)

    def test_leading_dots_and_extra_whitespace_are_tolerated(self):
        self.helper.commit_file('a.DLL', 'binary-ish', 'Add a dll')

        exit_code, _stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--disallow-extensions', '  .exe ,\t.dll  ',
        ])

        self.assertEqual(exit_code, 1)


class TestQuickStartSizeInputs(GitRepoTestBase):
    """The size inputs act as rules, whatever their magnitude."""

    def test_large_limit_is_valid(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('a.txt', 'X' * 2000, 'Add a text file')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--max-text-size-kb', '1048576',
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)


class TestQuickStartRuleOrder(GitRepoTestBase):
    """
    A file matching several quick-start-input rules gets one entry that
    lists every hit, in the table's fixed order.
    """

    def test_disallowed_extension_and_oversized_reported_together(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('a.exe', 'X' * 2000, 'Add an oversized exe')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--disallow-extensions', 'exe',
            '--max-text-size-kb', '1',
        ])

        self.assertEqual(exit_code, 1)
        entry_line = next(
            line for line in stdout.splitlines() if 'a.exe' in line and 'ERROR' in line)
        self.assertIn("File extension '.exe' is disallowed", entry_line)
        self.assertIn('Text file size', entry_line)
        self.assertIn('[rules: disallow_extensions, max_text_size_kb]', entry_line)


class TestPolicyAndInputsConflict(GitRepoTestBase):
    """A policy file and any quick-start input are mutually exclusive."""

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')
        self.helper.create_file('policy.yml', "rules: []\n")

    def test_conflict_with_one_input_exits_two(self):
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml', '--max-text-size-kb', '500',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn(
            "Policy file 'policy.yml' can't be combined with the "
            "max_text_size_kb input(s)", stderr)
        self.assertIn(
            "Remove the input(s) from the workflow, or add these rules to "
            "the policy file:", stderr)

    def test_an_empty_policy_file_still_conflicts(self):
        self.helper.create_file('empty_policy.yml', '')
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'empty_policy.yml', '--max-binary-size-kb', '100',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn("Policy file 'empty_policy.yml' can't be combined", stderr)

    def test_message_lists_every_set_input_in_table_order(self):
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
            '--max-binary-size-kb', '200',
            '--disallow-extensions', 'exe',
            '--max-text-size-kb', '500',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn(
            "disallow_extensions, max_text_size_kb, max_binary_size_kb input(s)", stderr)

    def test_printed_rules_parse_back_into_an_equivalent_policy(self):
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
            '--disallow-extensions', 'exe, dll',
            '--max-text-size-kb', '500',
            '--max-binary-size-kb', '200',
        ])
        self.assertEqual(exit_code, 2)

        yaml_block = stderr.split('policy file:\n\n', 1)[1]
        data = yaml.safe_load(yaml_block)
        self.assertEqual(len(data['rules']), 3)
        by_id = {rule['id']: rule for rule in data['rules']}

        self.assertEqual(by_id['disallow_extensions']['match']['extensions'], ['exe', 'dll'])
        self.assertEqual(by_id['disallow_extensions']['action'], 'error')

        self.assertEqual(by_id['max_text_size_kb']['match']['binary'], False)
        self.assertEqual(by_id['max_text_size_kb']['match']['size'], '> 500 KB')

        self.assertEqual(by_id['max_binary_size_kb']['match']['binary'], True)
        self.assertEqual(by_id['max_binary_size_kb']['match']['size'], '> 200 KB')

        # The printed policy must itself be valid -- i.e. loadable by the
        # same schema this run just rejected the combination against.
        from repo_size_guardian.rule_engine import Policy
        policy = Policy.from_dict(data)
        self.assertEqual(len(policy.rules), 3)

    def test_no_conflict_when_policy_file_does_not_exist(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'does-not-exist.yml', '--max-text-size-kb', '1000',
        ])
        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)

    def test_no_conflict_when_no_quick_start_input_is_set(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
        ])
        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)

    def test_conflict_is_checked_before_any_git_work(self):
        # A base ref that cannot possibly resolve; if the conflict check
        # did not run first, this would fail with a *different* error
        # (an unresolvable ref), not the policy/inputs conflict.
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'no-such-ref-at-all', '--head-ref', 'feature',
            '--policy-path', 'policy.yml', '--max-text-size-kb', '500',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn("can't be combined", stderr)


class TestFailOnSeverity(GitRepoTestBase):
    """fail_on=any vs fail_on=error changes the exit code for a warn-only violation."""

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

    def test_fail_on_any_fails_on_warn_violation(self):
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml', '--fail-on', 'any',
        ])
        self.assertEqual(exit_code, 1)
        self.assertIn('WARN', stdout)


class TestStopRule(GitRepoTestBase):
    """A policy `stop` rule excludes a file from every rule listed after it."""

    def test_stop_rule_excludes_a_file_a_later_rule_would_flag(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.create_file('policy.yml', (
            "rules:\n"
            "  - id: skip-vendor\n"
            "    match: {globs: [\"vendor/**\"]}\n"
            "    action: stop\n"
            "  - id: no-exe\n"
            "    match: {extensions: [\"exe\"]}\n"
            "    action: error\n"
        ))
        self.helper.commit_file('vendor/a.exe', 'binary-ish', 'Add a vendored exe')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
        ])
        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)


class TestTransientPolicyRule(GitRepoTestBase):
    """A `match: {transient: true}` policy rule catches a file added and
    removed again within the same PR, end to end through the CLI."""

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.create_file('policy.yml', (
            "rules:\n"
            "  - id: no-transient-files\n"
            "    match: {transient: true}\n"
            "    action: error\n"
        ))

    def test_added_then_deleted_file_fails_the_job(self):
        self.helper.commit_file('scratch.txt', 'temporary content', 'Add scratch')
        self.helper.delete_file('scratch.txt', 'Remove scratch again')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
        ])

        self.assertEqual(exit_code, 1)
        self.assertIn('scratch.txt', stdout)
        self.assertIn("Matched rule 'no-transient-files'", stdout)

    def test_report_carries_the_history_rewrite_note(self):
        self.helper.commit_file('scratch.txt', 'temporary content', 'Add scratch')
        self.helper.delete_file('scratch.txt', 'Remove scratch again')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
        ])

        self.assertEqual(exit_code, 1)
        self.assertIn(
            'This version of scratch.txt was removed or replaced later in '
            'this pull request, but it stays in the history.', stdout)

    def test_a_file_that_stays_passes(self):
        self.helper.commit_file('keep.txt', 'stays around', 'Add a file that stays')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn('No violations found', stdout)


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
        isolate_github_environment(self)
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

    def test_shallow_clone_writes_a_configuration_error_job_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = os.path.join(tmp, 'summary.md')
            with mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': summary_path}):
                exit_code, _stdout, _stderr = run_cli(
                    ['--base-ref', 'HEAD~1', '--head-ref', 'HEAD'])
            self.assertEqual(exit_code, 2)
            with open(summary_path, encoding='utf-8') as handle:
                summary = handle.read()
            self.assertIn('## Repo Size Guardian', summary)
            self.assertIn('**Status:** Configuration error', summary)
            self.assertIn('fetch-depth: 0', summary)
            self.assertIn("this repository's repo-size-guardian setup", summary)
            self.assertIn('A maintainer needs to fix it', summary)


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

    def test_malformed_policy_is_checked_before_any_git_work(self):
        # No commits at all yet, and a nonsensical base ref: if policy
        # validation did not run first, this would fail on ref resolution
        # instead of the malformed policy.
        self.helper.create_file('bad_policy.yml', 'rules: [unterminated\n')
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'no-such-ref', '--head-ref', 'HEAD',
            '--policy-path', 'bad_policy.yml',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn('bad_policy.yml', stderr)

    def test_malformed_policy_produces_no_violation_output(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')
        self.helper.create_file('bad_policy.yml', 'rules: [unterminated\n')

        with tempfile.TemporaryDirectory() as tmp:
            output_path = os.path.join(tmp, 'gh_output')
            open(output_path, 'w', encoding='utf-8').close()
            with mock.patch.dict(os.environ, {'GITHUB_OUTPUT': output_path}):
                exit_code, _stdout, _stderr = run_cli([
                    '--base-ref', 'main', '--head-ref', 'feature',
                    '--policy-path', 'bad_policy.yml',
                ])
            self.assertEqual(exit_code, 2)
            with open(output_path, encoding='utf-8') as handle:
                self.assertEqual(handle.read(), '')


class TestEmptyConfigWarning(GitRepoTestBase):
    """No policy and no quick-start inputs -> a prominent warning, but exit 0."""

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

    def test_disallow_extensions_alone_suppresses_the_warning(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--disallow-extensions', 'exe',
        ])

        self.assertEqual(exit_code, 0)
        self.assertNotIn('::warning::', stdout)

    def test_policy_file_with_rules_suppresses_the_warning(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')
        self.helper.create_file('policy.yml', (
            "rules:\n  - match: {extensions: [\"exe\"]}\n    action: error\n"
        ))

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
        ])

        self.assertEqual(exit_code, 0)
        self.assertNotIn('::warning::', stdout)

    def test_policy_file_with_no_rules_still_warns(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')
        self.helper.create_file('policy.yml', "rules: []\n")

        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml',
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn('::warning::', stdout)
        self.assertIn('nothing is being enforced', stdout.lower())


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

    def test_event_without_pull_request_falls_through(self):
        event = {'ref': 'refs/heads/main'}
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump(event, handle)
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

        with mock.patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'push'}):
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


class TestParseExtensionList(unittest.TestCase):
    """--disallow-extensions splits on commas and/or whitespace."""

    def test_comma_separated(self):
        self.assertEqual(main_module._parse_extension_list('exe,dll,zip'), ['exe', 'dll', 'zip'])

    def test_comma_and_whitespace(self):
        self.assertEqual(main_module._parse_extension_list('exe, dll, zip'), ['exe', 'dll', 'zip'])

    def test_whitespace_only(self):
        self.assertEqual(main_module._parse_extension_list('exe   dll'), ['exe', 'dll'])

    def test_leading_dots_preserved_for_later_matching(self):
        # matches_extension itself is dot-insensitive; the split just keeps
        # whatever was written.
        self.assertEqual(main_module._parse_extension_list('.exe, .dll'), ['.exe', '.dll'])

    def test_extra_separators_collapse(self):
        self.assertEqual(main_module._parse_extension_list(' , exe ,, dll ,'), ['exe', 'dll'])


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


class TestMissingCommitInCheckout(GitRepoTestBase):
    """
    A PR head (or base) SHA named in the event payload but absent from
    this checkout -- e.g. a stale `refs/pull/N/merge` ref after a push
    that conflicts with the base branch -- gets a specific, correctly
    targeted message. Without this, `git merge-base` fails the same way
    it would for genuinely unrelated histories, and the generic
    fetch-depth: 0 / force-push message sends the reader looking at the
    wrong thing entirely.
    """

    def _run_with_event(self, base_sha, head_sha):
        event = {'pull_request': {
            'base': {'sha': base_sha},
            'head': {'sha': head_sha},
        }}
        # Deliberately not under self.test_dir: tearDown's rmtree of that
        # directory runs before addCleanup callbacks, which would make the
        # unlink below fail with FileNotFoundError.
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump(event, handle)
            event_path = handle.name
        self.addCleanup(os.unlink, event_path)

        with mock.patch.dict(os.environ, {'GITHUB_EVENT_PATH': event_path}, clear=False):
            return run_cli([])

    def test_missing_head_sha_gets_specific_message(self):
        base_sha = self.helper.commit_file('README.md', 'hello', 'Base commit')
        missing_head_sha = 'f' * 40  # syntactically valid, never committed

        exit_code, _stdout, stderr = self._run_with_event(base_sha, missing_head_sha)

        self.assertEqual(exit_code, 2)
        self.assertIn(missing_head_sha, stderr)
        self.assertIn('not present in this checkout', stderr)
        self.assertIn('stale', stderr.lower())
        self.assertIn('merge ref', stderr.lower())
        # Must not send the reader chasing the unrelated fetch-depth fix.
        self.assertNotIn('fetch-depth', stderr)

    def test_missing_base_sha_gets_specific_message(self):
        head_sha = self.helper.commit_file('README.md', 'hello', 'Head commit')
        missing_base_sha = 'e' * 40  # syntactically valid, never committed

        exit_code, _stdout, stderr = self._run_with_event(missing_base_sha, head_sha)

        self.assertEqual(exit_code, 2)
        self.assertIn(missing_base_sha, stderr)
        self.assertIn('not present in this checkout', stderr)
        self.assertIn('force-pushed', stderr.lower())
        self.assertNotIn('fetch-depth', stderr)


class TestArgValidation(GitRepoTestBase):
    """A handful of inputs are typos that must not degrade silently."""

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

    def test_disallow_extensions_with_no_extensions_is_a_config_error(self):
        # An empty match.extensions list imposes NO condition, so this
        # would otherwise silently build a rule that disallows every file.
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--disallow-extensions', ' , ',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn('--disallow-extensions', stderr)
        self.assertIn('no extensions', stderr)

    def test_disallow_extensions_all_whitespace_is_a_config_error(self):
        exit_code, _stdout, stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--disallow-extensions', '   ',
        ])
        self.assertEqual(exit_code, 2)
        self.assertIn('--disallow-extensions', stderr)


class TestUnexpectedExceptionExitCode(GitRepoTestBase):
    """
    An internal crash must not be reported as exit code 1 (violations) or
    exit code 2 (a configuration error the user caused): it gets its own
    exit code 3.
    """

    def test_internal_error_exits_three(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        with mock.patch.object(main_module, 'evaluate_blobs',
                               side_effect=RuntimeError('boom')):
            exit_code, _stdout, stderr = run_cli([
                '--base-ref', 'main', '--head-ref', 'feature',
                '--max-text-size-kb', '1000',
            ])
        self.assertEqual(exit_code, 3)
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

        self.assertEqual(exit_code, 3)
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
        # GITHUB_OUTPUT exactly as it found it -- never a partially-written
        # entry a downstream step (e.g. one gated on `if: always()`) could
        # misread as real scan results. GITHUB_STEP_SUMMARY, unlike
        # GITHUB_OUTPUT, does get an internal-error summary appended (see
        # test_internal_error_writes_a_job_summary below); this test only
        # pins down GITHUB_OUTPUT.
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, 'github_output')
            open(output_path, 'w', encoding='utf-8').close()

            env = dict(os.environ)
            env['GITHUB_OUTPUT'] = output_path

            with mock.patch.object(main_module, 'evaluate_blobs',
                                   side_effect=RuntimeError('boom')):
                with mock.patch.dict(os.environ, env, clear=True):
                    exit_code, _stdout, _stderr = run_cli([
                        '--base-ref', 'main', '--head-ref', 'feature',
                        '--max-text-size-kb', '1000',
                    ])

            self.assertEqual(exit_code, 3)
            with open(output_path, encoding='utf-8') as handle:
                self.assertEqual(handle.read(), '')

    def test_internal_error_writes_a_job_summary(self):
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('more.txt', 'content', 'Add more')

        with tempfile.TemporaryDirectory() as tmp:
            summary_path = os.path.join(tmp, 'summary.md')
            with mock.patch.object(main_module, 'evaluate_blobs',
                                   side_effect=RuntimeError('boom')):
                with mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': summary_path}):
                    exit_code, _stdout, _stderr = run_cli([
                        '--base-ref', 'main', '--head-ref', 'feature',
                        '--max-text-size-kb', '1000',
                    ])
            self.assertEqual(exit_code, 3)
            with open(summary_path, encoding='utf-8') as handle:
                summary = handle.read()
            self.assertIn('## Repo Size Guardian', summary)
            self.assertIn('**Status:** Internal error', summary)
            self.assertIn('RuntimeError', summary)


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
        self.assertIn("this repository's repo-size-guardian setup", annotation_line)
        self.assertIn('A maintainer needs to fix it', annotation_line)
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
    `match.mime_types` would therefore pass every PR clean -- a false
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

    def test_warns_when_a_rule_matches_on_mime_types(self):
        _exit_code, stdout, _stderr = self._run_without_file_command(
            "rules:\n"
            "  - id: no-executables\n"
            "    match:\n"
            "      mime_types: [\"application/x-executable\"]\n")
        self.assertIn('::warning::', stdout)
        self.assertIn('`file` command', stdout)

    def test_no_warning_when_the_policy_does_not_use_mime(self):
        _exit_code, stdout, _stderr = self._run_without_file_command(
            "rules:\n  - match: {extensions: [\"exe\"]}\n    action: error\n")
        self.assertNotIn('`file` command', stdout)

    def test_no_warning_when_the_file_command_is_present(self):
        self.helper.create_file(
            'policy.yml',
            "rules:\n  - match: {mime_types: [\"application/x-dosexec\"]}\n    action: error\n")
        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--policy-path', 'policy.yml'])
        self.assertNotIn('`file` command', stdout)


class TestTransientMatchingUnavailableWarning(GitRepoTestBase):
    """
    `transient`/`transient_version` can never hold in scan_mode: diff, which
    only ever sees the final net diff -- exactly what already collapses away
    the file versions those keys exist to catch.
    """

    def setUp(self):
        super().setUp()
        self.helper.commit_file('README.md', 'hello', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('scratch.txt', 'temp', 'Add scratch')
        self.helper.delete_file('scratch.txt', 'Remove scratch again')

    def test_warns_once_in_diff_mode_when_a_rule_uses_transient(self):
        self.helper.create_file('policy.yml', (
            "rules:\n  - match: {transient: true}\n    action: error\n"
        ))
        exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--policy-path', 'policy.yml',
        ])
        # diff mode collapses the add+delete away entirely, so there is
        # nothing left to flag -- the warning is the only signal.
        self.assertEqual(exit_code, 0)
        self.assertIn('::warning::', stdout)
        self.assertEqual(stdout.count("scan_mode is 'diff'"), 1)
        self.assertIn('Use scan_mode: history', stdout)

    def test_warns_once_in_diff_mode_when_a_rule_uses_transient_version(self):
        self.helper.create_file('policy.yml', (
            "rules:\n  - match: {transient_version: true}\n    action: error\n"
        ))
        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--policy-path', 'policy.yml',
        ])
        self.assertEqual(stdout.count("scan_mode is 'diff'"), 1)

    def test_warns_only_once_when_a_rule_uses_both_keys(self):
        self.helper.create_file('policy.yml', (
            "rules:\n  - match: {transient: true, transient_version: true}\n    action: error\n"
        ))
        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--policy-path', 'policy.yml',
        ])
        self.assertEqual(stdout.count("scan_mode is 'diff'"), 1)

    def test_no_warning_when_the_policy_does_not_use_transient(self):
        self.helper.create_file('policy.yml', (
            "rules:\n  - match: {extensions: [\"exe\"]}\n    action: error\n"
        ))
        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'diff', '--policy-path', 'policy.yml',
        ])
        self.assertNotIn("scan_mode is 'diff'", stdout)

    def test_no_warning_in_history_mode(self):
        self.helper.create_file('policy.yml', (
            "rules:\n  - match: {transient: true}\n    action: error\n"
        ))
        _exit_code, stdout, _stderr = run_cli([
            '--base-ref', 'main', '--head-ref', 'feature',
            '--scan-mode', 'history', '--policy-path', 'policy.yml',
        ])
        self.assertNotIn("scan_mode is 'diff'", stdout)


if __name__ == '__main__':
    unittest.main()
