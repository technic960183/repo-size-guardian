"""
Tests for high-level change enumeration in load_branch module.
"""

import subprocess
from unittest import mock

from repo_size_guardian.load_branch import enumerate_changed_blobs
from tests.test_base import GitRepoTestBase


class TestEnumerateChangedBlobs(GitRepoTestBase):
    def test_single_commit(self):
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        head_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        self.assertEqual(len(blobs), 1)
        blob = blobs[0]
        self.assertEqual(blob['path'], 'file2.txt')
        self.assertEqual(blob['commit_sha'], head_commit)
        self.assertEqual(blob['status'], 'A')
        self.assertTrue(len(blob['blob_sha']) > 0)

    def test_multiple_files(self):
        base_commit = self.helper.commit_file('file1.txt', 'original content', 'Base commit')
        self.helper.create_file('file2.txt', 'new file content')
        self.helper.create_file('file1.txt', 'modified content')
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', 'Multiple changes')

        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        self.assertEqual(len(blobs), 2)
        paths = [b['path'] for b in blobs]
        self.assertIn('file1.txt', paths)
        self.assertIn('file2.txt', paths)
        for b in blobs:
            if b['path'] == 'file1.txt':
                self.assertEqual(b['status'], 'M')
            elif b['path'] == 'file2.txt':
                self.assertEqual(b['status'], 'A')

    def test_mixed_changes_single_commit(self):
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        base_commit = self.helper.commit_file('file3.txt', 'content3', 'Add file3')

        self.helper.create_file('file1.txt', 'modified content1')
        self.helper.create_file('file4.txt', 'new content4')
        self.helper.run_git('rm', 'file2.txt')
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', 'Add/Modify/Delete multiple files')

        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        self.assertEqual(len(blobs), 3)
        status_map = {b['path']: b['status'] for b in blobs}
        self.assertEqual(status_map['file1.txt'], 'M')
        self.assertEqual(status_map['file2.txt'], 'D')
        self.assertEqual(status_map['file4.txt'], 'A')
        for b in blobs:
            if b['status'] == 'D':
                self.assertEqual(b['blob_sha'], '')
            else:
                self.assertTrue(len(b['blob_sha']) > 0)

    def test_deleted_file(self):
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        base_commit = self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        self.helper.run_git('rm', 'file2.txt')
        self.helper.run_git('commit', '-m', 'Delete file2')
        head_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        self.assertEqual(len(blobs), 1)
        blob = blobs[0]
        self.assertEqual(blob['path'], 'file2.txt')
        self.assertEqual(blob['commit_sha'], head_commit)
        self.assertEqual(blob['status'], 'D')
        self.assertEqual(blob['blob_sha'], '')

    def test_across_multiple_commits(self):
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        commit1 = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        self.helper.commit_file('file3.txt', 'content3', 'Add file3')
        commit2 = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        self.assertEqual(len(blobs), 2)
        commit_shas = [b['commit_sha'] for b in blobs]
        self.assertIn(commit1, commit_shas)
        self.assertIn(commit2, commit_shas)
        paths = [b['path'] for b in blobs]
        self.assertIn('file2.txt', paths)
        self.assertIn('file3.txt', paths)

    def test_blob_introduced_only_by_merge_commit(self):
        """Test that a blob created while resolving a merge conflict is enumerated.

        Conflict resolution can write content that exists in no other commit, so
        the merge commit is the only place the blob appears. Because merge
        commits are skipped, such a blob is never enumerated and escapes the
        scan completely.
        """
        base_commit = self.helper.commit_file('data.txt', 'base', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('data.txt', 'from feature', 'Change on feature')
        self.helper.checkout('main')
        self.helper.commit_file('data.txt', 'from main', 'Change on main')

        self.helper.start_conflicting_merge('feature')
        self.helper.create_file('data.txt', 'resolved content present in no other commit')
        self.helper.run_git('add', 'data.txt')
        self.helper.run_git('commit', '-m', 'Merge feature and resolve conflict')

        resolved_sha = self.helper.run_git('rev-parse', 'HEAD:data.txt').stdout.strip()

        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        self.assertIn(resolved_sha, [b['blob_sha'] for b in blobs])


class TestEnumerateChangedBlobsSpawnCount(GitRepoTestBase):
    """Locks in the fix for the per-file git process spawn cost.

    enumerate_changed_blobs used to spawn one extra `git rev-parse` process
    per changed file (via get_blob_sha_at_commit), on top of the one
    `git diff-tree` per commit. Against the PRD target of 10,000 changed
    files that is >10,000 avoidable spawns. `git diff-tree --raw` already
    reports the post-image blob SHA on the same line as the status, so the
    per-path rev-parse must not happen.
    """

    def _spawn_count_for_commit_adding_files(self, prefix: str, n_files: int) -> int:
        """Commit `n_files` new files in one commit, then return how many git
        processes `enumerate_changed_blobs` spawns while scanning just that
        commit (setup spawns are not counted)."""
        base_commit = self.helper.commit_file(f'{prefix}_base.txt', prefix, 'Base commit')
        for i in range(n_files):
            self.helper.create_file(f'{prefix}_file{i}.txt', f'content{i}')
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', f'Add {n_files} files')

        real_run = subprocess.run
        call_count = 0

        def counting_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return real_run(*args, **kwargs)

        # Patch the `subprocess.run` name as seen from git_utils, so every git
        # invocation made on behalf of enumerate_changed_blobs is counted,
        # while still actually running (via real_run) so the test stays a
        # real end-to-end check rather than a mock of git's behavior.
        with mock.patch('repo_size_guardian.git_utils.subprocess.run', side_effect=counting_run):
            blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))

        self.assertEqual(len(blobs), n_files)
        return call_count

    def test_spawn_count_does_not_grow_with_changed_file_count(self):
        """A commit touching many files must cost the same number of git
        process spawns as a commit touching few files: one git invocation to
        list the commit, plus one to diff it -- never one per changed file.
        """
        few_files_spawns = self._spawn_count_for_commit_adding_files('few', 2)
        many_files_spawns = self._spawn_count_for_commit_adding_files('many', 20)

        self.assertEqual(
            few_files_spawns, many_files_spawns,
            "git process spawn count must not depend on the number of "
            f"changed files: {few_files_spawns} spawns for 2 files vs "
            f"{many_files_spawns} spawns for 20 files"
        )
        # One list_commits() call (git rev-list) + one get_diff_files() call
        # (git diff-tree) per commit, regardless of how many files it touches.
        self.assertEqual(few_files_spawns, 2)
