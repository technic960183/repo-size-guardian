"""
Shared test utilities for repository-based tests.

Provides common functionality for setting up temporary git repositories
and performing git operations in tests.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


class GitRepoTestHelper:
    """Helper class for creating and managing test git repositories."""

    def __init__(self, test_dir: str):
        self.test_dir = test_dir

    def run_git(self, *args):
        """Run a git command in the test directory."""
        return subprocess.run(
            ['git'] + list(args),
            cwd=self.test_dir,
            capture_output=True,
            text=True,
            check=True
        )

    def init_repo(self):
        """Initialize a git repository."""
        self.run_git('init', '--initial-branch=main')
        self.run_git('config', 'user.name', 'Test User')
        self.run_git('config', 'user.email', 'test@example.com')

    def create_file(self, path: str, content: str):
        """Create a file with given content."""
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w') as f:
            f.write(content)

    def commit_file(self, path: str, content: str, message: str) -> str:
        """Create a file and commit it, returning the commit SHA."""
        self.create_file(path, content)
        self.run_git('add', path)
        result = self.run_git('commit', '-m', message)
        # Get the commit SHA
        sha_result = self.run_git('rev-parse', 'HEAD')
        return sha_result.stdout.strip()

    def create_and_commit_file(self, path: str, content, message: str) -> str:
        """Create a file, commit it, and return the blob SHA."""
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Write content (handle both text and binary)
        if isinstance(content, str):
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
        else:
            with open(full_path, 'wb') as f:
                f.write(content)

        self.run_git('add', path)
        self.run_git('commit', '-m', message)

        # Get the blob SHA for the committed file
        result = self.run_git('rev-parse', f'HEAD:{path}')
        return result.stdout.strip()

    def commit_gitlink(self, path: str, message: str,
                       gitlink_sha: str = '1' * 40) -> str:
        """Commit a submodule (gitlink) entry at `path`, returning commit SHA.

        The entry is written straight into the index with
        `git update-index --cacheinfo`, which produces exactly the same
        `160000` tree entry that `git submodule add` would, without needing a
        second repository on disk or relaxed `protocol.file` settings. The
        recorded SHA is a commit of the submodule's own repository and so
        deliberately names no object in this repository.
        """
        self.run_git('update-index', '--add', '--cacheinfo',
                     f'160000,{gitlink_sha},{path}')
        self.run_git('commit', '-m', message)
        return self.run_git('rev-parse', 'HEAD').stdout.strip()

    def create_branch(self, branch_name: str):
        """Create and checkout a new branch."""
        self.run_git('checkout', '-b', branch_name)

    def create_orphan_branch(self, branch_name: str):
        """Create and checkout a branch with no history, so its next commit
        is a parentless (root) commit."""
        self.run_git('checkout', '--orphan', branch_name)
        # Drop the inherited working-tree contents from the index, so the
        # orphan's first commit introduces only what the test stages. Not an
        # error if the index was already empty.
        subprocess.run(
            ['git', 'rm', '-rf', '--cached', '.'],
            cwd=self.test_dir,
            capture_output=True,
            text=True
        )

    def checkout(self, ref: str):
        """Checkout a reference."""
        self.run_git('checkout', ref)

    def merge_branch(self, branch: str, message: str) -> str:
        """Merge a branch without fast-forward, returning the merge commit SHA."""
        self.run_git('merge', '--no-ff', '-m', message, branch)
        return self.run_git('rev-parse', 'HEAD').stdout.strip()

    def start_conflicting_merge(self, branch: str):
        """Begin a merge expected to conflict, leaving the conflict unresolved."""
        return subprocess.run(
            ['git', 'merge', '--no-ff', branch],
            cwd=self.test_dir,
            capture_output=True,
            text=True
        )

    def delete_file(self, path: str, message: str) -> str:
        """Delete a file and commit the deletion, returning commit SHA."""
        self.run_git('rm', path)
        result = self.run_git('commit', '-m', message)
        sha_result = self.run_git('rev-parse', 'HEAD')
        return sha_result.stdout.strip()


class GitRepoTestBase(unittest.TestCase):
    """Base class for tests that need a temporary git repository."""

    def setUp(self):
        """Set up test environment with temporary git repository."""
        self.original_cwd = os.getcwd()
        self.test_dir = tempfile.mkdtemp()
        self.helper = GitRepoTestHelper(self.test_dir)
        os.chdir(self.test_dir)
        self.helper.init_repo()

        # Most of these tests drive main.py's CLI end to end, which
        # constructs a real ReportConfig() with no explicit paths -- its
        # defaults read GITHUB_STEP_SUMMARY/GITHUB_OUTPUT straight from the
        # environment. On a real GitHub Actions runner (including the one
        # running this very test suite in CI) both are always set, so an
        # un-isolated test that completes a scan would append a real
        # "Repo Size Guardian" table to the run's own job summary and a
        # real violations_found=/summary= line to the step's own output --
        # stray, misleading noise on a public repo's Actions run. Popping
        # them here (restored verbatim by mock.patch.dict on teardown, via
        # addCleanup, whatever they were -- set, unset, or changed mid-test)
        # makes every such test behave like a local run with no GitHub
        # environment, which is what these tests actually mean to exercise
        # unless they opt in with their own explicit temp-file paths.
        env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        os.environ.pop('GITHUB_OUTPUT', None)

    def tearDown(self):
        """Clean up test environment."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)
