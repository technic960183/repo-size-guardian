"""
Tests for `action.yml`, the composite-action wrapper.

Nothing here runs on a GitHub runner, so every defect in this file is
invisible until a real PR triggers the action. These tests therefore check
the two things that silently break in production:

1. Static consistency -- every action input reaches the CLI under the name
   the CLI actually accepts, and the composite `outputs` are wired to the
   keys the Python code really writes to `$GITHUB_OUTPUT`. A typo in a flag
   name here would be caught by nothing else.
2. Shell behavior -- the generated `run:` script is executed for real
   (with `python` stubbed out) to prove its quoting, its handling of empty
   optional inputs, and its immunity to shell metacharacters in an input.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

from repo_size_guardian.main import _build_arg_parser
from repo_size_guardian.reporting import ReportConfig, write_github_output

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ACTION_YML = os.path.join(_REPO_ROOT, 'action.yml')


def load_action():
    """Parse action.yml into a dict."""
    with open(_ACTION_YML, 'r', encoding='utf-8') as handle:
        return yaml.safe_load(handle)


def scan_step(action):
    """Return the composite step with id 'scan'."""
    for step in action['runs']['steps']:
        if step.get('id') == 'scan':
            return step
    raise AssertionError("action.yml has no step with id 'scan'")


class TestActionInputsMatchCli(unittest.TestCase):
    """Every input must map to a flag the CLI really accepts."""

    def setUp(self):
        self.action = load_action()
        self.step = scan_step(self.action)
        self.script = self.step['run']
        parser = _build_arg_parser()
        self.cli_flags = set()
        for action in parser._actions:  # pylint: disable=protected-access
            self.cli_flags.update(action.option_strings)

    def test_every_flag_in_the_script_exists_in_the_cli(self):
        # A flag typo (e.g. --max-annotation) would otherwise only surface
        # as an argparse error on a real PR run.
        used = set(re.findall(r'(?<![\w-])--[a-z][a-z0-9-]*', self.script))
        # pip's own flags are not ours.
        used -= {'--quiet', '--disable-pip-version-check', '--retries', '--timeout'}
        unknown = sorted(used - self.cli_flags)
        self.assertEqual(unknown, [], "flags used in action.yml but unknown to the CLI")

    def test_every_declared_input_is_passed_to_the_cli(self):
        for name in self.action['inputs']:
            expected_flag = '--' + name.replace('_', '-')
            self.assertIn(
                expected_flag, self.script,
                "input '{0}' is declared but never passed to the CLI".format(name))
            self.assertIn(expected_flag, self.cli_flags)

    def test_every_input_is_passed_through_the_environment(self):
        env = self.step['env']
        for name in self.action['inputs']:
            key = 'INPUT_' + name.upper()
            self.assertIn(key, env, "input '{0}' has no env passthrough".format(name))
            self.assertEqual(env[key], '${{ inputs.%s }}' % name)

    def test_no_input_expression_is_interpolated_into_the_script(self):
        # `${{ inputs.x }}` inside `run:` is substituted before bash parses
        # the script, so a branch name like `a$(curl evil)` -- a legal git
        # ref, and exactly what `base_ref: ${{ github.head_ref }}` yields
        # on a fork PR -- would be executed.
        self.assertNotIn('${{ inputs.', self.script)
        self.assertNotIn('${{ github.', self.script)


class TestActionOutputsWiring(unittest.TestCase):
    """The composite outputs must name the keys the Python code writes."""

    def test_outputs_reference_the_scan_step(self):
        action = load_action()
        for name, spec in action['outputs'].items():
            self.assertEqual(spec['value'], '${{ steps.scan.outputs.%s }}' % name)

    def test_output_names_match_write_github_output_keys(self):
        # Actually run write_github_output and read back the keys it wrote:
        # a rename on either side silently yields an empty output on a real
        # run, which no other test would notice.
        action = load_action()
        handle = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        write_github_output([], ReportConfig(github_output_path=handle.name))
        with open(handle.name, 'r', encoding='utf-8') as output_file:
            written = {line.split('=', 1)[0]
                       for line in output_file.read().splitlines() if line}
        self.assertEqual(written, set(action['outputs']),
                         "action.yml outputs and GITHUB_OUTPUT keys disagree")


class TestScanStepShellBehavior(unittest.TestCase):
    """Run the real `run:` script with `python` stubbed, and inspect argv."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.workspace = os.path.join(self.tmpdir, 'workspace')
        self.bindir = os.path.join(self.tmpdir, 'bin')
        self.argv_file = os.path.join(self.tmpdir, 'argv.txt')
        os.makedirs(self.workspace)
        os.makedirs(self.bindir)

        stub = os.path.join(self.bindir, 'python')
        with open(stub, 'w', encoding='utf-8') as handle:
            handle.write(
                '#!/bin/bash\n'
                'if [ "$2" = "pip" ]; then exit 0; fi\n'
                'printf "%s\\n" "$@" > "$ARGV_FILE"\n'
            )
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        self.script = scan_step(load_action())['run']

    def run_script(self, **inputs):
        """Run the step script with the given inputs; return the captured argv."""
        env = dict(os.environ)
        env['PATH'] = self.bindir + os.pathsep + env['PATH']
        env['ACTION_PATH'] = _REPO_ROOT
        env['GITHUB_WORKSPACE'] = self.workspace
        env['ARGV_FILE'] = self.argv_file
        for name in load_action()['inputs']:
            env['INPUT_' + name.upper()] = inputs.get(name, '')
        result = subprocess.run(['bash', '-c', self.script], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.argv_file, 'r', encoding='utf-8') as handle:
            return handle.read().splitlines()

    def test_all_inputs_empty_produces_no_flags(self):
        # An input explicitly set to "" must fall back to the CLI default
        # rather than being forwarded as an empty value (which would make
        # e.g. --fail-on "" an argparse error on every run).
        argv = self.run_script()
        self.assertEqual(argv, ['-m', 'repo_size_guardian'])

    def test_populated_inputs_become_flags(self):
        argv = self.run_script(policy_path='.github/p.yml', fail_on='warn',
                               scan_mode='diff', dedupe_blobs='false',
                               annotate_pr='false', max_annotations='0',
                               max_text_size_kb='500', max_binary_size_kb='100',
                               base_ref='origin/main', head_ref='HEAD')
        self.assertEqual(argv[:2], ['-m', 'repo_size_guardian'])
        flags = argv[2:]
        self.assertEqual(flags, [
            '--policy-path', '.github/p.yml',
            '--fail-on', 'warn',
            '--scan-mode', 'diff',
            '--dedupe-blobs', 'false',
            '--annotate-pr', 'false',
            '--max-annotations', '0',
            '--max-text-size-kb', '500',
            '--max-binary-size-kb', '100',
            '--base-ref', 'origin/main',
            '--head-ref', 'HEAD',
        ])

    def test_shell_metacharacters_in_an_input_are_not_executed(self):
        # `$(...)`, backticks, quotes and `;` are all legal in a git branch
        # name, and `base_ref: ${{ github.head_ref }}` is a documented
        # pattern, so a fork PR can choose this value.
        hostile = 'a$(touch ' + os.path.join(self.tmpdir, 'pwned') + ');echo "x"`id`'
        argv = self.run_script(base_ref=hostile)
        self.assertIn(hostile, argv)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, 'pwned')),
                         "input value was executed by the shell")

    def test_whitespace_in_an_input_stays_one_argument(self):
        argv = self.run_script(policy_path='my policy/a b.yml')
        self.assertEqual(argv, ['-m', 'repo_size_guardian',
                                '--policy-path', 'my policy/a b.yml'])

    def test_script_runs_from_the_consumer_workspace(self):
        # The action must scan ${GITHUB_WORKSPACE}, not its own checkout.
        script = self.script + '\npwd\n'
        env = dict(os.environ)
        env['PATH'] = self.bindir + os.pathsep + env['PATH']
        env['ACTION_PATH'] = _REPO_ROOT
        env['GITHUB_WORKSPACE'] = self.workspace
        env['ARGV_FILE'] = self.argv_file
        for name in load_action()['inputs']:
            env['INPUT_' + name.upper()] = ''
        result = subprocess.run(['bash', '-c', script], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.path.realpath(result.stdout.strip()),
                         os.path.realpath(self.workspace))


if __name__ == '__main__':
    unittest.main()
