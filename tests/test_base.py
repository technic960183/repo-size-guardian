"""
Shared test utilities for repository-based tests.

Provides common functionality for setting up temporary git repositories
and performing git operations in tests.
"""

import os
import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path


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
        
    def create_branch(self, branch_name: str):
        """Create and checkout a new branch."""
        self.run_git('checkout', '-b', branch_name)
        
    def checkout(self, ref: str):
        """Checkout a reference."""
        self.run_git('checkout', ref)
        
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
        
    def tearDown(self):
        """Clean up test environment."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)