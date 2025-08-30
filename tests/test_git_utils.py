"""
Test suite for git_utils module.

Tests git history enumeration and blob detection using git plumbing commands.
"""

import os
import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path

from repo_size_guardian.git_utils import get_merge_base, list_commits, enumerate_changed_blobs
from tests.test_base import GitRepoTestBase


class TestGetMergeBase(GitRepoTestBase):
    """Test cases for get_merge_base function."""
    
    def test_get_merge_base_same_branch(self):
        """Test merge base when both refs point to same commit."""
        # Create initial commit
        commit_sha = self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        
        # Merge base of HEAD with itself should be itself
        merge_base = get_merge_base('HEAD', 'HEAD')
        self.assertEqual(merge_base, commit_sha)
        
    def test_get_merge_base_linear_history(self):
        """Test merge base with linear history."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        
        # Create another commit
        head_commit = self.helper.commit_file('file2.txt', 'content2', 'Head commit')
        
        # Merge base should be the base commit
        merge_base = get_merge_base(base_commit, 'HEAD')
        self.assertEqual(merge_base, base_commit)
        
    def test_get_merge_base_with_branch(self):
        """Test merge base with actual branching."""
        # Create base commit on main
        self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        base_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Create branch
        self.helper.create_branch('feature')
        self.helper.commit_file('file2.txt', 'branch content', 'Branch commit')
        
        # Go back to main and create another commit
        self.helper.checkout('master')
        self.helper.commit_file('file3.txt', 'main content', 'Main commit')
        
        # Merge base should be the original commit
        merge_base = get_merge_base('master', 'feature')
        self.assertEqual(merge_base, base_commit)


class TestListCommits(GitRepoTestBase):
    """Test cases for list_commits function."""
    
    def test_list_commits_empty_range(self):
        """Test listing commits with empty range."""
        # Create initial commit
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        
        # Empty range should return no commits
        commits = list(list_commits('HEAD..HEAD'))
        self.assertEqual(len(commits), 0)
        
    def test_list_commits_single_commit(self):
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
        
    def test_list_commits_multiple_commits(self):
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


class TestEnumerateChangedBlobs(GitRepoTestBase):
    """Test cases for enumerate_changed_blobs function."""
    
    def test_enumerate_changed_blobs_single_commit(self):
        """Test enumerating blobs from single commit."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        
        # Create commit with new file
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        head_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Enumerate blobs
        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        
        # Should have one blob
        self.assertEqual(len(blobs), 1)
        blob = blobs[0]
        
        self.assertEqual(blob['path'], 'file2.txt')
        self.assertEqual(blob['commit_sha'], head_commit)
        self.assertEqual(blob['status'], 'A')  # Added
        self.assertTrue(len(blob['blob_sha']) > 0)  # Should have blob SHA
        
    def test_enumerate_changed_blobs_multiple_files(self):
        """Test enumerating blobs with multiple file changes."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'original content', 'Base commit')
        
        # Create commit with multiple changes
        self.helper.create_file('file2.txt', 'new file content')
        self.helper.create_file('file1.txt', 'modified content')  # Modify existing
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', 'Multiple changes')
        head_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Enumerate blobs
        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        
        # Should have two blobs
        self.assertEqual(len(blobs), 2)
        
        # Check that we have both files
        paths = [blob['path'] for blob in blobs]
        self.assertIn('file1.txt', paths)
        self.assertIn('file2.txt', paths)
        
        # Check that statuses are correct
        for blob in blobs:
            if blob['path'] == 'file1.txt':
                self.assertEqual(blob['status'], 'M')  # Modified
            elif blob['path'] == 'file2.txt':
                self.assertEqual(blob['status'], 'A')  # Added
                
    def test_enumerate_changed_blobs_multiple_files_single_commit(self):
        """Test enumerating multiple files in a single commit (add/mod/delete)."""
        # Create initial files
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        base_commit = self.helper.commit_file('file3.txt', 'content3', 'Add file3')
        
        # In one commit: modify file1, add file4, delete file2
        self.helper.create_file('file1.txt', 'modified content1')  # Modify
        self.helper.create_file('file4.txt', 'new content4')  # Add
        self.helper.run_git('rm', 'file2.txt')  # Delete
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', 'Add/Modify/Delete multiple files')
        
        # Enumerate blobs
        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        
        # Should have three blobs
        self.assertEqual(len(blobs), 3)
        
        # Check each type of change
        status_map = {blob['path']: blob['status'] for blob in blobs}
        self.assertEqual(status_map['file1.txt'], 'M')  # Modified
        self.assertEqual(status_map['file2.txt'], 'D')  # Deleted
        self.assertEqual(status_map['file4.txt'], 'A')  # Added
        
        # Check blob SHAs
        for blob in blobs:
            if blob['status'] == 'D':
                # Deleted files should have empty blob SHA
                self.assertEqual(blob['blob_sha'], '')
            else:
                # Added/Modified files should have blob SHA
                self.assertTrue(len(blob['blob_sha']) > 0)
        
    def test_enumerate_changed_blobs_deleted_file(self):
        """Test enumerating blobs with deleted file."""
        # Create and commit a file
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        base_commit = self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        
        # Delete the file
        self.helper.run_git('rm', 'file2.txt')
        self.helper.run_git('commit', '-m', 'Delete file2')
        head_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Enumerate blobs
        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        
        # Should have one blob for the deleted file
        self.assertEqual(len(blobs), 1)
        blob = blobs[0]
        
        self.assertEqual(blob['path'], 'file2.txt')
        self.assertEqual(blob['commit_sha'], head_commit)
        self.assertEqual(blob['status'], 'D')  # Deleted
        self.assertEqual(blob['blob_sha'], '')  # No blob SHA for deleted files
        
    def test_enumerate_changed_blobs_across_multiple_commits(self):
        """Test enumerating blobs across multiple commits."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        
        # Create first commit with new file
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        commit1 = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Create second commit with another file
        self.helper.commit_file('file3.txt', 'content3', 'Add file3')
        commit2 = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Enumerate blobs across both commits
        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        
        # Should have two blobs
        self.assertEqual(len(blobs), 2)
        
        # Verify blobs are from different commits
        commit_shas = [blob['commit_sha'] for blob in blobs]
        self.assertIn(commit1, commit_shas)
        self.assertIn(commit2, commit_shas)
        
        # Verify file paths
        paths = [blob['path'] for blob in blobs]
        self.assertIn('file2.txt', paths)
        self.assertIn('file3.txt', paths)