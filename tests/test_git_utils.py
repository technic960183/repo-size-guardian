"""
Test suite for git_utils module.

Tests low-level git operations including cat-file commands,
commit ranges, and file change detection.
"""

import subprocess

from repo_size_guardian.git_utils import (
    get_blob_sha_at_commit,
    get_diff_files,
    get_merge_base,
    git_cat_file_content,
    git_cat_file_exists,
    git_cat_file_size,
    list_commits,
)
from tests.test_base import GitRepoTestBase


class TestGitCatFileSize(GitRepoTestBase):
    """Test cases for git_cat_file_size function."""

    def test_text_file(self):
        """Test getting size of a text file blob."""
        content = "Hello, world!\nThis is a test file.\n"
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add test file')

        size = git_cat_file_size(blob_sha)
        expected_size = len(content.encode('utf-8'))
        self.assertEqual(size, expected_size)

    def test_empty_file(self):
        """Test getting size of an empty file."""
        blob_sha = self.helper.create_and_commit_file('empty.txt', '', 'Add empty file')

        size = git_cat_file_size(blob_sha)
        self.assertEqual(size, 0)

    def test_binary_file(self):
        """Test getting size of a binary file."""
        binary_content = bytes([i % 256 for i in range(100)])
        blob_sha = self.helper.create_and_commit_file('binary.bin', binary_content, 'Add binary file')

        size = git_cat_file_size(blob_sha)
        self.assertEqual(size, len(binary_content))

    def test_invalid_sha(self):
        """Test error handling for invalid blob SHA."""
        with self.assertRaises(subprocess.CalledProcessError):
            git_cat_file_size('invalid_sha_that_does_not_exist')

    def test_empty_sha(self):
        """Test error handling for empty blob SHA."""
        with self.assertRaises(ValueError):
            git_cat_file_size('')


class TestGitCatFileContent(GitRepoTestBase):
    """Test cases for git_cat_file_content function."""

    def test_text_file(self):
        """Test getting content of a text file."""
        content = "Hello, world!\nThis is a test file.\n"
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add test file')

        result = git_cat_file_content(blob_sha)
        self.assertEqual(result, content.encode('utf-8'))

    def test_binary_file(self):
        """Test getting content of a binary file."""
        binary_content = bytes([i % 256 for i in range(100)])
        blob_sha = self.helper.create_and_commit_file('binary.bin', binary_content, 'Add binary file')

        result = git_cat_file_content(blob_sha)
        self.assertEqual(result, binary_content)

    def test_empty_file(self):
        """Test getting content of an empty file."""
        blob_sha = self.helper.create_and_commit_file('empty.txt', '', 'Add empty file')

        result = git_cat_file_content(blob_sha)
        self.assertEqual(result, b'')

    def test_invalid_sha(self):
        """Test error handling for invalid blob SHA."""
        with self.assertRaises(subprocess.CalledProcessError):
            git_cat_file_content('invalid_sha_that_does_not_exist')

    def test_empty_sha(self):
        """Test error handling for empty blob SHA."""
        with self.assertRaises(ValueError):
            git_cat_file_content('')


class TestGitCatFileExists(GitRepoTestBase):
    """Test cases for git_cat_file_exists function."""

    def test_existing_blob(self):
        """Test checking existence of a valid blob."""
        content = "Test content"
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add test file')

        exists = git_cat_file_exists(blob_sha)
        self.assertTrue(exists)

    def test_nonexistent_blob(self):
        """Test checking existence of a non-existent blob."""
        exists = git_cat_file_exists('0000000000000000000000000000000000000000')
        self.assertFalse(exists)

    def test_invalid_sha(self):
        """Test checking existence with invalid SHA format."""
        exists = git_cat_file_exists('invalid_sha')
        self.assertFalse(exists)

    def test_empty_sha(self):
        """Test error handling for empty blob SHA."""
        with self.assertRaises(ValueError):
            git_cat_file_exists('')


class TestGetMergeBase(GitRepoTestBase):
    """Test cases for get_merge_base function."""

    def test_same_branch(self):
        """Test merge base when both refs point to same commit."""
        # Create initial commit
        commit_sha = self.helper.commit_file('file1.txt', 'content1', 'Initial commit')

        # Merge base of HEAD with itself should be itself
        merge_base = get_merge_base('HEAD', 'HEAD')
        self.assertEqual(merge_base, commit_sha)

    def test_linear_history(self):
        """Test merge base with linear history."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')

        # Create another commit
        head_commit = self.helper.commit_file('file2.txt', 'content2', 'Head commit')

        # Merge base should be the base commit
        merge_base = get_merge_base(base_commit, 'HEAD')
        self.assertEqual(merge_base, base_commit)

    def test_with_branch(self):
        """Test merge base with actual branching."""
        # Create base commit on main
        self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        base_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        # Create branch
        self.helper.create_branch('feature')
        self.helper.commit_file('file2.txt', 'branch content', 'Branch commit')

        # Go back to main and create another commit
        self.helper.checkout('main')
        self.helper.commit_file('file3.txt', 'main content', 'Main commit')

        # Merge base should be the original commit
        merge_base = get_merge_base('main', 'feature')
        self.assertEqual(merge_base, base_commit)


class TestListCommits(GitRepoTestBase):
    """Test cases for list_commits function."""

    def test_empty_range(self):
        """Test listing commits with empty range."""
        # Create initial commit
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')

        # Empty range should return no commits
        commits = list(list_commits('HEAD..HEAD'))
        self.assertEqual(len(commits), 0)

    def test_single_commit(self):
        """Test listing commits with single commit range."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')

        # Create another commit
        new_commit = self.helper.commit_file('file2.txt', 'content2', 'New commit')

        # List commits in range
        commits = list(list_commits(f'{base_commit}..HEAD'))

        # Should contain only the new commit
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0], new_commit)

    def test_multiple_commits(self):
        """Test listing commits with multiple commits."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')

        # Create multiple commits
        commit1 = self.helper.commit_file('file2.txt', 'content2', 'Commit 1')
        commit2 = self.helper.commit_file('file3.txt', 'content3', 'Commit 2')

        # List commits in range
        commits = list_commits(f'{base_commit}..HEAD')

        # Should contain both commits in reverse chronological order
        self.assertEqual(len(commits), 2)
        self.assertIn(commit1, commits)
        self.assertIn(commit2, commits)


class TestGetDiffFiles(GitRepoTestBase):
    """Test cases for get_diff_files function."""

    def test_single_file_added(self):
        """Test getting diff files for a commit that adds one file."""
        self.helper.commit_file('file1.txt', 'initial', 'Initial commit')
        commit_sha = self.helper.commit_file('file2.txt', 'content', 'Add file2')

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['status'], 'A')
        self.assertEqual(files[0]['path'], 'file2.txt')

    def test_multiple_files_added(self):
        """Test getting diff files for a commit that adds multiple files."""
        self.helper.commit_file('file1.txt', 'initial', 'Initial commit')
        self.helper.create_file('file2.txt', 'content2')
        self.helper.create_file('file3.txt', 'content3')
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', 'Add multiple files')
        commit_sha = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 2)
        paths = [f['path'] for f in files]
        self.assertIn('file2.txt', paths)
        self.assertIn('file3.txt', paths)

    def test_file_modified(self):
        """Test getting diff files for a commit that modifies a file."""
        self.helper.commit_file('file1.txt', 'original', 'Initial commit')
        commit_sha = self.helper.commit_file('file1.txt', 'modified', 'Modify file1')

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['status'], 'M')
        self.assertEqual(files[0]['path'], 'file1.txt')

    def test_file_deleted(self):
        """Test getting diff files for a commit that deletes a file."""
        self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        self.helper.run_git('rm', 'file2.txt')
        self.helper.run_git('commit', '-m', 'Delete file2')
        commit_sha = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['status'], 'D')
        self.assertEqual(files[0]['path'], 'file2.txt')

    def test_initial_commit_no_parent(self):
        """Test getting diff files for initial commit (has no parent, returns empty)."""
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Initial commit')

        # Initial commits have no parent, so diff-tree returns empty
        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 0)

    def test_mixed_changes(self):
        """Test getting diff files for a commit with mixed changes."""
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        self.helper.create_file('file1.txt', 'modified1')
        self.helper.create_file('file3.txt', 'content3')
        self.helper.run_git('rm', 'file2.txt')
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', 'Mixed changes')
        commit_sha = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 3)
        status_map = {f['path']: f['status'] for f in files}
        self.assertEqual(status_map['file1.txt'], 'M')
        self.assertEqual(status_map['file2.txt'], 'D')
        self.assertEqual(status_map['file3.txt'], 'A')


class TestGetBlobShaAtCommit(GitRepoTestBase):
    """Test cases for get_blob_sha_at_commit function."""

    def test_existing_file(self):
        """Test getting blob SHA for an existing file at a commit."""
        content = 'test content'
        commit_sha = self.helper.commit_file('file1.txt', content, 'Add file')

        blob_sha = get_blob_sha_at_commit(commit_sha, 'file1.txt')
        self.assertTrue(len(blob_sha) == 40)  # SHA-1 is 40 hex chars
        self.assertTrue(all(c in '0123456789abcdef' for c in blob_sha))

    def test_nonexistent_file(self):
        """Test error when getting blob SHA for non-existent file."""
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Add file')

        with self.assertRaises(subprocess.CalledProcessError):
            get_blob_sha_at_commit(commit_sha, 'nonexistent.txt')

    def test_file_at_different_commits(self):
        """Test getting different blob SHAs when file content changes."""
        commit1 = self.helper.commit_file('file1.txt', 'version1', 'First version')
        blob_sha1 = get_blob_sha_at_commit(commit1, 'file1.txt')

        commit2 = self.helper.commit_file('file1.txt', 'version2', 'Second version')
        blob_sha2 = get_blob_sha_at_commit(commit2, 'file1.txt')

        self.assertNotEqual(blob_sha1, blob_sha2)

    def test_same_content_same_blob(self):
        """Test that same content produces same blob SHA across commits."""
        content = 'identical content'
        commit1 = self.helper.commit_file('file1.txt', content, 'Add file1')
        blob_sha1 = get_blob_sha_at_commit(commit1, 'file1.txt')

        commit2 = self.helper.commit_file('file2.txt', content, 'Add file2')
        blob_sha2 = get_blob_sha_at_commit(commit2, 'file2.txt')

        self.assertEqual(blob_sha1, blob_sha2)
