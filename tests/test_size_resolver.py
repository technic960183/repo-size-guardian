"""
Test suite for size_resolver module.

Tests blob size resolution using git cat-file commands.
"""

import os
import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path

from repo_size_guardian.size_resolver import (
    get_blob_size, 
    get_blob_sizes_batch, 
    augment_blobs_with_sizes,
    augment_blob_objects_with_sizes
)
from repo_size_guardian.models import Violation, Blob


class SizeResolverTestHelper:
    """Helper class for creating test blobs."""
    
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
        
    def create_and_commit_file(self, path: str, content: str, message: str) -> str:
        """Create a file, commit it, and return the blob SHA."""
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        # Write content (handle both text and binary)
        if isinstance(content, str):
            with open(full_path, 'w') as f:
                f.write(content)
        else:
            with open(full_path, 'wb') as f:
                f.write(content)
                
        self.run_git('add', path)
        self.run_git('commit', '-m', message)
        
        # Get the blob SHA for this file
        result = self.run_git('rev-parse', f'HEAD:{path}')
        return result.stdout.strip()


class TestSizeResolver(unittest.TestCase):
    """Test cases for size resolver utilities."""
    
    def setUp(self):
        """Set up test environment with temporary git repo."""
        self.test_dir = tempfile.mkdtemp()
        self.helper = SizeResolverTestHelper(self.test_dir)
        self.helper.init_repo()
        
        # Store original working directory
        self.original_cwd = os.getcwd()
        # Change to test directory for git operations
        os.chdir(self.test_dir)
        
    def tearDown(self):
        """Clean up test environment."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)
        
    def test_violation_dataclass(self):
        """Test the Violation dataclass."""
        blob = Blob(
            path='test.txt',
            blob_sha='abc123',
            commit_sha='def456',
            status='A',
            size_bytes=1024
        )
        
        violation = Violation(
            blob=blob,
            rule_name='size_limit',
            message='File too large',
            severity='error'
        )
        
        self.assertEqual(violation.path, 'test.txt')
        self.assertEqual(violation.blob_sha, 'abc123')
        self.assertEqual(violation.commit_sha, 'def456')
        self.assertEqual(violation.message, 'File too large')
        self.assertEqual(violation.size_bytes, 1024)
        
    def test_violation_dataclass_optional_size(self):
        """Test Violation dataclass with optional size."""
        blob = Blob(
            path='test.txt',
            blob_sha='abc123',
            commit_sha='def456',
            status='A'
        )
        
        violation = Violation(
            blob=blob,
            rule_name='pattern_match',
            message='Disallowed pattern'
        )
        
        self.assertIsNone(violation.size_bytes)
        
    def test_get_blob_size_text_file(self):
        """Test getting size of a text file blob."""
        content = "Hello, world!\nThis is a test file.\n"
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add test file')
        
        size = get_blob_size(blob_sha)
        expected_size = len(content.encode('utf-8'))
        self.assertEqual(size, expected_size)
        
    def test_get_blob_size_empty_file(self):
        """Test getting size of an empty file."""
        blob_sha = self.helper.create_and_commit_file('empty.txt', '', 'Add empty file')
        
        size = get_blob_size(blob_sha)
        self.assertEqual(size, 0)
        
    def test_get_blob_size_binary_file(self):
        """Test getting size of a binary file."""
        # Create some binary content
        binary_content = bytes([i % 256 for i in range(100)])
        blob_sha = self.helper.create_and_commit_file('binary.bin', binary_content, 'Add binary file')
        
        size = get_blob_size(blob_sha)
        self.assertEqual(size, len(binary_content))
        
    def test_get_blob_size_large_file(self):
        """Test getting size of a larger file."""
        # Create a larger text file
        content = "A" * 10000 + "\n" + "B" * 5000
        blob_sha = self.helper.create_and_commit_file('large.txt', content, 'Add large file')
        
        size = get_blob_size(blob_sha)
        expected_size = len(content.encode('utf-8'))
        self.assertEqual(size, expected_size)
        
    def test_get_blob_size_invalid_sha(self):
        """Test error handling for invalid blob SHA."""
        with self.assertRaises(subprocess.CalledProcessError):
            get_blob_size('invalid_sha_that_does_not_exist')
            
    def test_get_blob_size_empty_sha(self):
        """Test error handling for empty blob SHA."""
        with self.assertRaises(ValueError):
            get_blob_size('')
            
        with self.assertRaises(ValueError):
            get_blob_size('   ')  # Only whitespace
            
    def test_get_blob_sizes_batch_multiple_blobs(self):
        """Test getting sizes for multiple blobs in batch."""
        # Create multiple files
        content1 = "File 1 content"
        content2 = "File 2 has different content length"
        content3 = ""  # Empty file
        
        blob_sha1 = self.helper.create_and_commit_file('file1.txt', content1, 'Add file1')
        blob_sha2 = self.helper.create_and_commit_file('file2.txt', content2, 'Add file2')
        blob_sha3 = self.helper.create_and_commit_file('file3.txt', content3, 'Add file3')
        
        # Get sizes in batch
        sizes = get_blob_sizes_batch([blob_sha1, blob_sha2, blob_sha3])
        
        # Verify sizes
        self.assertEqual(len(sizes), 3)
        self.assertEqual(sizes[blob_sha1], len(content1.encode('utf-8')))
        self.assertEqual(sizes[blob_sha2], len(content2.encode('utf-8')))
        self.assertEqual(sizes[blob_sha3], 0)
        
    def test_get_blob_sizes_batch_with_invalid_sha(self):
        """Test batch size resolution with some invalid SHAs."""
        # Create one valid file
        content = "Valid file content"
        valid_sha = self.helper.create_and_commit_file('valid.txt', content, 'Add valid file')
        
        # Mix valid and invalid SHAs
        sizes = get_blob_sizes_batch([valid_sha, 'invalid_sha', ''])
        
        # Should only contain the valid SHA
        self.assertEqual(len(sizes), 1)
        self.assertEqual(sizes[valid_sha], len(content.encode('utf-8')))
        
    def test_get_blob_sizes_batch_empty_list(self):
        """Test batch size resolution with empty list."""
        sizes = get_blob_sizes_batch([])
        self.assertEqual(sizes, {})
        
    def test_augment_blobs_with_sizes_single_blob(self):
        """Test augmenting blob records with size information."""
        # Create a test file
        content = "Test file for augmentation"
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add test file')
        
        # Create blob record (like from enumerate_changed_blobs)
        blobs = [{
            'path': 'test.txt',
            'blob_sha': blob_sha,
            'commit_sha': 'commit123',
            'status': 'A'
        }]
        
        # Augment with sizes
        augmented = augment_blobs_with_sizes(blobs)
        
        # Verify augmentation
        self.assertEqual(len(augmented), 1)
        augmented_blob = augmented[0]
        
        self.assertEqual(augmented_blob['path'], 'test.txt')
        self.assertEqual(augmented_blob['blob_sha'], blob_sha)
        self.assertEqual(augmented_blob['commit_sha'], 'commit123')
        self.assertEqual(augmented_blob['status'], 'A')
        self.assertEqual(augmented_blob['size_bytes'], len(content.encode('utf-8')))
        
    def test_augment_blobs_with_sizes_multiple_blobs(self):
        """Test augmenting multiple blob records."""
        # Create multiple test files
        content1 = "First file"
        content2 = "Second file with more content"
        
        blob_sha1 = self.helper.create_and_commit_file('file1.txt', content1, 'Add file1')
        blob_sha2 = self.helper.create_and_commit_file('file2.txt', content2, 'Add file2')
        
        # Create blob records
        blobs = [
            {
                'path': 'file1.txt',
                'blob_sha': blob_sha1,
                'commit_sha': 'commit1',
                'status': 'A'
            },
            {
                'path': 'file2.txt',
                'blob_sha': blob_sha2,
                'commit_sha': 'commit2',
                'status': 'M'
            }
        ]
        
        # Augment with sizes
        augmented = augment_blobs_with_sizes(blobs)
        
        # Verify augmentation
        self.assertEqual(len(augmented), 2)
        
        # Check first blob
        self.assertEqual(augmented[0]['size_bytes'], len(content1.encode('utf-8')))
        
        # Check second blob
        self.assertEqual(augmented[1]['size_bytes'], len(content2.encode('utf-8')))
        
    def test_augment_blobs_with_sizes_deleted_file(self):
        """Test augmenting blob records including deleted files."""
        # Create a test file
        content = "Test file"
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add test file')
        
        # Create blob records including a deleted file (empty blob_sha)
        blobs = [
            {
                'path': 'test.txt',
                'blob_sha': blob_sha,
                'commit_sha': 'commit1',
                'status': 'A'
            },
            {
                'path': 'deleted.txt',
                'blob_sha': '',  # Empty for deleted files
                'commit_sha': 'commit2',
                'status': 'D'
            }
        ]
        
        # Augment with sizes
        augmented = augment_blobs_with_sizes(blobs)
        
        # Verify augmentation
        self.assertEqual(len(augmented), 2)
        
        # Normal file should have size
        self.assertEqual(augmented[0]['size_bytes'], len(content.encode('utf-8')))
        
        # Deleted file should have None size
        self.assertIsNone(augmented[1]['size_bytes'])
        
    def test_augment_blobs_with_sizes_duplicate_blobs(self):
        """Test augmenting blob records with duplicate blob SHAs."""
        # Create a test file
        content = "Shared content"
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add test file')
        
        # Create blob records with duplicate blob SHA (same file in different commits)
        blobs = [
            {
                'path': 'test.txt',
                'blob_sha': blob_sha,
                'commit_sha': 'commit1',
                'status': 'A'
            },
            {
                'path': 'test.txt',
                'blob_sha': blob_sha,  # Same blob SHA
                'commit_sha': 'commit2',
                'status': 'M'
            }
        ]
        
        # Augment with sizes
        augmented = augment_blobs_with_sizes(blobs)
        
        # Both should have the same size (deduplicated lookup)
        self.assertEqual(len(augmented), 2)
        expected_size = len(content.encode('utf-8'))
        self.assertEqual(augmented[0]['size_bytes'], expected_size)
        self.assertEqual(augmented[1]['size_bytes'], expected_size)


if __name__ == '__main__':
    unittest.main()