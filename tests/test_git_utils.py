"""
Test suite for git_utils module.

Tests git operations using temporary git repositories.
"""

import os
import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path

from repo_size_guardian.git_utils import get_merge_base, list_commits, enumerate_changed_blobs


class GitUtilsTestHelper:
    """Helper class for creating temporary git repositories for testing."""
    
    def __init__(self, test_dir: str):
        self.test_dir = test_dir
        self.git_dir = os.path.join(test_dir, '.git')
        
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
        self.run_git('init')
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
        
    def create_branch(self, branch_name: str):
        """Create and checkout a new branch."""
        self.run_git('checkout', '-b', branch_name)
        
    def checkout(self, ref: str):
        """Checkout a specific reference."""
        self.run_git('checkout', ref)


class TestGitUtils(unittest.TestCase):
    """Test cases for git utilities."""
    
    def setUp(self):
        """Set up test environment with temporary git repo."""
        self.test_dir = tempfile.mkdtemp()
        self.helper = GitUtilsTestHelper(self.test_dir)
        self.helper.init_repo()
        
        # Store original working directory
        self.original_cwd = os.getcwd()
        # Change to test directory for git operations
        os.chdir(self.test_dir)
        
    def tearDown(self):
        """Clean up test environment."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)
        
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
        
        # Create feature branch and add commits
        self.helper.create_branch('feature')
        self.helper.commit_file('feature.txt', 'feature content', 'Feature commit 1')
        self.helper.commit_file('feature2.txt', 'feature content 2', 'Feature commit 2')
        
        # Switch back to master and add a commit
        self.helper.checkout('master')
        self.helper.commit_file('master.txt', 'master content', 'Master commit')
        
        # Merge base should be the original base commit
        merge_base = get_merge_base('master', 'feature')
        self.assertEqual(merge_base, base_commit)
        
    def test_list_commits_empty_range(self):
        """Test listing commits with empty range."""
        # Create a commit
        self.helper.commit_file('file1.txt', 'content1', 'Initial commit')
        
        # Range from HEAD to HEAD should be empty
        commits = list_commits('HEAD..HEAD')
        self.assertEqual(commits, [])
        
    def test_list_commits_single_commit(self):
        """Test listing commits with single commit range."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        
        # Create another commit
        head_commit = self.helper.commit_file('file2.txt', 'content2', 'Head commit')
        
        # Range should contain just the head commit
        commits = list_commits(f'{base_commit}..{head_commit}')
        self.assertEqual(commits, [head_commit])
        
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
        
        # Check statuses
        for blob in blobs:
            self.assertEqual(blob['commit_sha'], head_commit)
            if blob['path'] == 'file1.txt':
                self.assertEqual(blob['status'], 'M')  # Modified
            elif blob['path'] == 'file2.txt':
                self.assertEqual(blob['status'], 'A')  # Added
                
    def test_enumerate_changed_blobs_deleted_file(self):
        """Test enumerating blobs with deleted file."""
        # Create file and commit
        self.helper.commit_file('file1.txt', 'content1', 'Add file1')
        base_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Delete file and commit
        os.remove(os.path.join(self.test_dir, 'file1.txt'))
        self.helper.run_git('add', 'file1.txt')
        self.helper.run_git('commit', '-m', 'Delete file1')
        head_commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Enumerate blobs
        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        
        # Should have one blob for deletion
        self.assertEqual(len(blobs), 1)
        blob = blobs[0]
        
        self.assertEqual(blob['path'], 'file1.txt')
        self.assertEqual(blob['commit_sha'], head_commit)
        self.assertEqual(blob['status'], 'D')  # Deleted
        self.assertEqual(blob['blob_sha'], '')  # No blob SHA for deleted files
        
    def test_enumerate_changed_blobs_across_multiple_commits(self):
        """Test enumerating blobs across multiple commits."""
        # Create base commit
        base_commit = self.helper.commit_file('file1.txt', 'content1', 'Base commit')
        
        # Create first commit in range
        self.helper.commit_file('file2.txt', 'content2', 'Add file2')
        commit1 = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Create second commit in range
        self.helper.commit_file('file3.txt', 'content3', 'Add file3')
        commit2 = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        
        # Enumerate blobs
        blobs = list(enumerate_changed_blobs(f'{base_commit}..HEAD'))
        
        # Should have two blobs from two commits
        self.assertEqual(len(blobs), 2)
        
        # Check commits
        commit_shas = [blob['commit_sha'] for blob in blobs]
        self.assertIn(commit1, commit_shas)
        self.assertIn(commit2, commit_shas)


if __name__ == '__main__':
    unittest.main()