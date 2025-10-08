"""
Test suite for size_resolver module.

Tests blob size resolution using git cat-file commands.
"""

import subprocess
import unittest
from unittest.mock import patch

from repo_size_guardian.models import Blob, Violation
from repo_size_guardian.size_resolver import (augment_blob_objects_with_sizes,
                                              get_blob_size,
                                              get_blob_sizes_batch)
from tests.test_base import GitRepoTestBase


class TestModels(GitRepoTestBase):
    """Test cases for data models."""

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


class TestGetBlobSizeWithMock(unittest.TestCase):
    """Test cases for get_blob_size function using mocks."""

    @patch('repo_size_guardian.size_resolver.git_cat_file_size')
    def test_calls_git_cat_file_size(self, mock_git_cat_file_size):
        """Test that get_blob_size calls git_cat_file_size."""
        mock_git_cat_file_size.return_value = 1024
        
        result = get_blob_size('abc123')
        
        mock_git_cat_file_size.assert_called_once_with('abc123')
        self.assertEqual(result, 1024)

    @patch('repo_size_guardian.size_resolver.git_cat_file_size')
    def test_propagates_value_error(self, mock_git_cat_file_size):
        """Test that ValueError from git_cat_file_size is propagated."""
        mock_git_cat_file_size.side_effect = ValueError("blob_sha cannot be empty")
        
        with self.assertRaises(ValueError):
            get_blob_size('')


class TestGetBlobSizesBatchWithMock(unittest.TestCase):
    """Test cases for get_blob_sizes_batch function using mocks."""

    @patch('repo_size_guardian.size_resolver.git_cat_file_size')
    def test_batch_processing(self, mock_git_cat_file_size):
        """Test batch processing of multiple blobs."""
        mock_git_cat_file_size.side_effect = [100, 200, 300]
        
        result = get_blob_sizes_batch(['sha1', 'sha2', 'sha3'])
        
        self.assertEqual(len(result), 3)
        self.assertEqual(result['sha1'], 100)
        self.assertEqual(result['sha2'], 200)
        self.assertEqual(result['sha3'], 300)

    @patch('repo_size_guardian.size_resolver.git_cat_file_size')
    def test_handles_errors_gracefully(self, mock_git_cat_file_size):
        """Test that errors for individual blobs are handled gracefully."""
        def side_effect(sha):
            if sha == 'bad_sha':
                raise subprocess.CalledProcessError(1, ['git'])
            return 100
        
        mock_git_cat_file_size.side_effect = side_effect
        
        result = get_blob_sizes_batch(['good_sha', 'bad_sha'])
        
        self.assertEqual(len(result), 1)
        self.assertIn('good_sha', result)
        self.assertNotIn('bad_sha', result)


class TestAugmentBlobObjectsWithSizesWithMock(unittest.TestCase):
    """Test cases for augment_blob_objects_with_sizes function using mocks."""

    @patch('repo_size_guardian.size_resolver.get_blob_sizes_batch')
    def test_augments_blobs_with_sizes(self, mock_get_blob_sizes_batch):
        """Test that blobs are augmented with sizes."""
        mock_get_blob_sizes_batch.return_value = {
            'sha1': 100,
            'sha2': 200
        }
        
        blobs = [
            Blob(path='file1.txt', blob_sha='sha1', commit_sha='commit1', status='A'),
            Blob(path='file2.txt', blob_sha='sha2', commit_sha='commit2', status='M')
        ]
        
        result = augment_blob_objects_with_sizes(blobs)
        
        self.assertEqual(result[0].size_bytes, 100)
        self.assertEqual(result[1].size_bytes, 200)

    @patch('repo_size_guardian.size_resolver.get_blob_sizes_batch')
    def test_handles_deleted_files(self, mock_get_blob_sizes_batch):
        """Test that deleted files are handled correctly."""
        mock_get_blob_sizes_batch.return_value = {}
        
        blobs = [
            Blob(path='deleted.txt', blob_sha='', commit_sha='commit1', status='D')
        ]
        
        result = augment_blob_objects_with_sizes(blobs)
        
        self.assertIsNone(result[0].size_bytes)

    @patch('repo_size_guardian.size_resolver.get_blob_sizes_batch')
    def test_deduplicates_blob_shas(self, mock_get_blob_sizes_batch):
        """Test that duplicate blob SHAs are deduplicated."""
        mock_get_blob_sizes_batch.return_value = {'sha1': 100}
        
        blobs = [
            Blob(path='file1.txt', blob_sha='sha1', commit_sha='commit1', status='A'),
            Blob(path='file2.txt', blob_sha='sha1', commit_sha='commit2', status='M')
        ]
        
        augment_blob_objects_with_sizes(blobs)
        
        # Should be called with unique blob SHAs only
        mock_get_blob_sizes_batch.assert_called_once()
        call_args = mock_get_blob_sizes_batch.call_args[0][0]
        self.assertEqual(len(call_args), 1)
        self.assertIn('sha1', call_args)


if __name__ == '__main__':
    unittest.main()
