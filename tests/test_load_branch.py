"""
Tests for high-level change enumeration in load_branch module.
"""

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
