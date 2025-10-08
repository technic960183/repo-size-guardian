"""
Test suite for type_detector module.

Tests text vs binary detection using various file types.
"""

import json
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from repo_size_guardian.models import Blob
from repo_size_guardian.type_detector import (
    _detect_type_with_content_heuristics, _detect_type_with_file_command,
    augment_blob_objects_with_types, detect_blob_type, detect_blob_types_batch)
from tests.test_base import GitRepoTestBase


class TestDetectBlobType(GitRepoTestBase):
    """Test cases for detect_blob_type function."""

    def test_text_file(self):
        """Test detection of plain text files."""
        content = "Hello, world!\nThis is a text file.\nIt has multiple lines."
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add text file')

        result = detect_blob_type(blob_sha)

        self.assertFalse(result['is_binary'])
        self.assertIn(result['confidence'], ['high', 'medium', 'low'])

    def test_markdown_file(self):
        """Test detection of Markdown files."""
        content = """# Test Markdown
        
This is a **markdown** file with:
- Lists
- *Italic* text
- `code blocks`

```python
print("Hello, world!")
```
"""
        blob_sha = self.helper.create_and_commit_file('test.md', content, 'Add markdown file')

        result = detect_blob_type(blob_sha)

        self.assertFalse(result['is_binary'])

    def test_json_file(self):
        """Test detection of JSON files (should be text)."""
        data = {
            "name": "test",
            "version": "1.0.0",
            "description": "A test JSON file",
            "nested": {
                "key": "value",
                "array": [1, 2, 3]
            }
        }
        content = json.dumps(data, indent=2)
        blob_sha = self.helper.create_and_commit_file('test.json', content, 'Add JSON file')

        result = detect_blob_type(blob_sha)

        self.assertFalse(result['is_binary'])

    def test_jupyter_notebook(self):
        """Test detection of Jupyter notebooks (should be text/JSON)."""
        notebook_content = {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": 1,
                    "metadata": {},
                    "outputs": [],
                    "source": ["print('Hello from Jupyter!')"]
                }
            ],
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3"
                }
            },
            "nbformat": 4,
            "nbformat_minor": 4
        }
        content = json.dumps(notebook_content, indent=2)
        blob_sha = self.helper.create_and_commit_file('test.ipynb', content, 'Add notebook')

        result = detect_blob_type(blob_sha)

        self.assertFalse(result['is_binary'])

    def test_binary_file_with_null_bytes(self):
        """Test detection of binary files containing null bytes."""
        # Create binary content with null bytes
        binary_content = b'Hello\x00World\x00Binary\x00File'
        blob_sha = self.helper.create_and_commit_file('test.bin', binary_content, 'Add binary file')

        result = detect_blob_type(blob_sha)

        self.assertTrue(result['is_binary'])

    def test_random_binary(self):
        """Test detection of binary files with random bytes."""
        # Create random binary content
        binary_content = bytes([i % 256 for i in range(0, 256, 3)])  # Mix of bytes
        blob_sha = self.helper.create_and_commit_file('random.bin', binary_content, 'Add random binary')

        result = detect_blob_type(blob_sha)

        self.assertTrue(result['is_binary'])

    def test_empty_file(self):
        """Test detection of empty files."""
        blob_sha = self.helper.create_and_commit_file('empty.txt', '', 'Add empty file')

        result = detect_blob_type(blob_sha)

        # Empty files should be considered text
        self.assertFalse(result['is_binary'])

    def test_large_text(self):
        """Test detection of larger text files."""
        # Create a larger text file
        content = "Line of text.\n" * 1000  # 1000 lines
        blob_sha = self.helper.create_and_commit_file('large.txt', content, 'Add large text')

        result = detect_blob_type(blob_sha)

        self.assertFalse(result['is_binary'])

    def test_invalid_sha(self):
        """Test handling of invalid blob SHA."""
        with self.assertRaises(subprocess.CalledProcessError):
            detect_blob_type('invalid_sha_that_does_not_exist')

    def test_empty_sha(self):
        """Test handling of empty blob SHA."""
        with self.assertRaises(ValueError):
            detect_blob_type('')

        with self.assertRaises(ValueError):
            detect_blob_type('   ')  # Only whitespace


class TestDetectBlobTypesBatch(GitRepoTestBase):
    """Test cases for detect_blob_types_batch function."""

    def test_basic(self):
        """Test batch type detection."""
        # Create multiple files
        text_content = "This is a text file."
        binary_content = b'Binary\x00file\x00content'

        text_sha = self.helper.create_and_commit_file('text.txt', text_content, 'Add text')
        binary_sha = self.helper.create_and_commit_file('binary.bin', binary_content, 'Add binary')

        # Detect types in batch
        results = detect_blob_types_batch([text_sha, binary_sha])

        self.assertEqual(len(results), 2)
        self.assertFalse(results[text_sha]['is_binary'])
        self.assertTrue(results[binary_sha]['is_binary'])

    def test_with_invalid_sha(self):
        """Test batch detection with invalid SHAs."""
        text_content = "Valid text content."
        valid_sha = self.helper.create_and_commit_file('valid.txt', text_content, 'Add valid file')

        results = detect_blob_types_batch([valid_sha, 'invalid_sha', ''])

        # Should only contain the valid SHA
        self.assertEqual(len(results), 1)
        self.assertIn(valid_sha, results)
        self.assertFalse(results[valid_sha]['is_binary'])


class TestAugmentBlobObjectsWithTypes(GitRepoTestBase):
    """Test cases for augment_blob_objects_with_types function."""

    def test_basic(self):
        """Test augmenting Blob objects with type information."""
        # Create test files
        text_content = "Text file content."
        binary_content = b'Binary\x00content'

        text_sha = self.helper.create_and_commit_file('text.txt', text_content, 'Add text')
        binary_sha = self.helper.create_and_commit_file('binary.bin', binary_content, 'Add binary')

        # Create Blob objects
        blobs = [
            Blob(
                path='text.txt',
                blob_sha=text_sha,
                commit_sha='commit1',
                status='A'
            ),
            Blob(
                path='binary.bin',
                blob_sha=binary_sha,
                commit_sha='commit2',
                status='A'
            )
        ]

        # Augment with type information
        augmented = augment_blob_objects_with_types(blobs)

        self.assertEqual(len(augmented), 2)

        # Check text file
        text_blob = next(b for b in augmented if b.path == 'text.txt')
        self.assertFalse(text_blob.is_binary)
        self.assertIsNotNone(text_blob.type_confidence)

        # Check binary file
        binary_blob = next(b for b in augmented if b.path == 'binary.bin')
        self.assertTrue(binary_blob.is_binary)
        self.assertIsNotNone(binary_blob.type_confidence)

    def test_deleted_file(self):
        """Test augmenting Blob objects including deleted files."""
        # Create a text file
        text_content = "Text file content."
        text_sha = self.helper.create_and_commit_file('text.txt', text_content, 'Add text')

        # Create Blob objects including a deleted file
        blobs = [
            Blob(
                path='text.txt',
                blob_sha=text_sha,
                commit_sha='commit1',
                status='A'
            ),
            Blob(
                path='deleted.txt',
                blob_sha='',  # Empty for deleted files
                commit_sha='commit2',
                status='D'
            )
        ]

        # Augment with type information
        augmented = augment_blob_objects_with_types(blobs)

        self.assertEqual(len(augmented), 2)

        # Normal file should have type info
        text_blob = next(b for b in augmented if b.path == 'text.txt')
        self.assertFalse(text_blob.is_binary)

        # Deleted file should have None values
        deleted_blob = next(b for b in augmented if b.path == 'deleted.txt')
        self.assertIsNone(deleted_blob.is_binary)
        self.assertIsNone(deleted_blob.mime_type)
        self.assertIsNone(deleted_blob.type_confidence)


class TestPrivateDetectionMethods(GitRepoTestBase):
    """Test cases for private detection methods."""

    def test_content_heuristics_fallback(self):
        """Test content heuristics directly."""
        # Create a text file
        text_content = "Plain text content without special characters."
        text_sha = self.helper.create_and_commit_file('text.txt', text_content, 'Add text')

        # Test content heuristics directly
        result = _detect_type_with_content_heuristics(text_sha)

        self.assertFalse(result['is_binary'])
        self.assertIsNone(result['mime'])  # Content heuristics don't detect MIME
        self.assertIn(result['confidence'], ['high', 'medium', 'low'])

    def test_content_heuristics_binary_with_null_bytes(self):
        """Test content heuristics with null bytes."""
        binary_content = b'Content with \x00 null bytes'
        binary_sha = self.helper.create_and_commit_file('binary.bin', binary_content, 'Add binary')

        result = _detect_type_with_content_heuristics(binary_sha)

        self.assertTrue(result['is_binary'])
        self.assertEqual(result['confidence'], 'high')  # Null bytes = high confidence

    def test_content_heuristics_low_printable_ratio(self):
        """Test content heuristics with low printable character ratio."""
        # Create content with many non-printable characters
        mixed_content = b'Text' + bytes(range(128, 200)) + b'More text'
        mixed_sha = self.helper.create_and_commit_file('mixed.dat', mixed_content, 'Add mixed')

        result = _detect_type_with_content_heuristics(mixed_sha)

        # Should be detected as binary due to low printable ratio
        self.assertTrue(result['is_binary'])
        # With this specific content, we expect medium confidence due to low printable ratio + failed decoding
        self.assertEqual(result['confidence'], 'medium')

    def test_content_heuristics_invalid_sha(self):
        """Test content heuristics with invalid SHA returns None."""
        result = _detect_type_with_content_heuristics('invalid_sha_that_does_not_exist')

        # Should return None instead of raising an exception
        self.assertIsNone(result)


class TestDetectTypeWithFileCommandWithMock(unittest.TestCase):
    """Test cases for _detect_type_with_file_command function using mocks."""

    @patch('repo_size_guardian.type_detector.subprocess.run')
    @patch('repo_size_guardian.type_detector.git_cat_file_content')
    def test_detects_text_file(self, mock_git_cat_file_content, mock_subprocess_run):
        """Test detection of text files using file command."""
        mock_git_cat_file_content.return_value = b'Hello, world!'
        mock_subprocess_run.return_value = MagicMock(stdout='text/plain; charset=utf-8')
        
        result = _detect_type_with_file_command('sha123')
        
        self.assertFalse(result['is_binary'])
        self.assertEqual(result['mime'], 'text/plain')
        self.assertEqual(result['confidence'], 'high')

    @patch('repo_size_guardian.type_detector.subprocess.run')
    @patch('repo_size_guardian.type_detector.git_cat_file_content')
    def test_detects_binary_file(self, mock_git_cat_file_content, mock_subprocess_run):
        """Test detection of binary files using file command."""
        mock_git_cat_file_content.return_value = b'\x00\x01\x02\x03'
        mock_subprocess_run.return_value = MagicMock(stdout='application/octet-stream')
        
        result = _detect_type_with_file_command('sha123')
        
        self.assertTrue(result['is_binary'])
        self.assertEqual(result['mime'], 'application/octet-stream')

    @patch('repo_size_guardian.type_detector.git_cat_file_content')
    def test_returns_none_on_error(self, mock_git_cat_file_content):
        """Test that None is returned on errors."""
        mock_git_cat_file_content.side_effect = subprocess.CalledProcessError(1, ['git'])
        
        result = _detect_type_with_file_command('sha123')
        
        self.assertIsNone(result)


class TestDetectTypeWithContentHeuristicsWithMock(unittest.TestCase):
    """Test cases for _detect_type_with_content_heuristics function using mocks."""

    @patch('repo_size_guardian.type_detector.git_cat_file_content')
    def test_detects_text_file(self, mock_git_cat_file_content):
        """Test detection of text files using heuristics."""
        mock_git_cat_file_content.return_value = b'Hello, world! This is text.'
        
        result = _detect_type_with_content_heuristics('sha123')
        
        self.assertFalse(result['is_binary'])
        self.assertIsNone(result['mime'])

    @patch('repo_size_guardian.type_detector.git_cat_file_content')
    def test_detects_binary_with_null_bytes(self, mock_git_cat_file_content):
        """Test detection of binary files with null bytes."""
        mock_git_cat_file_content.return_value = b'Binary\x00content'
        
        result = _detect_type_with_content_heuristics('sha123')
        
        self.assertTrue(result['is_binary'])
        self.assertEqual(result['confidence'], 'high')

    @patch('repo_size_guardian.type_detector.git_cat_file_content')
    def test_returns_none_on_error(self, mock_git_cat_file_content):
        """Test that None is returned on errors."""
        mock_git_cat_file_content.side_effect = subprocess.CalledProcessError(1, ['git'])
        
        result = _detect_type_with_content_heuristics('sha123')
        
        self.assertIsNone(result)


class TestDetectBlobTypeWithMock(unittest.TestCase):
    """Test cases for detect_blob_type function using mocks."""

    @patch('repo_size_guardian.type_detector._detect_type_with_file_command')
    def test_uses_file_command_first(self, mock_file_command):
        """Test that file command is tried first."""
        mock_file_command.return_value = {
            'is_binary': False,
            'mime': 'text/plain',
            'confidence': 'high'
        }
        
        result = detect_blob_type('sha123')
        
        self.assertFalse(result['is_binary'])
        self.assertEqual(result['mime'], 'text/plain')

    @patch('repo_size_guardian.type_detector._detect_type_with_content_heuristics')
    @patch('repo_size_guardian.type_detector._detect_type_with_file_command')
    def test_falls_back_to_heuristics(self, mock_file_command, mock_heuristics):
        """Test fallback to content heuristics."""
        mock_file_command.return_value = None
        mock_heuristics.return_value = {
            'is_binary': False,
            'mime': None,
            'confidence': 'medium'
        }
        
        result = detect_blob_type('sha123')
        
        self.assertFalse(result['is_binary'])
        self.assertEqual(result['confidence'], 'medium')

    @patch('repo_size_guardian.type_detector._detect_type_with_content_heuristics')
    @patch('repo_size_guardian.type_detector._detect_type_with_file_command')
    def test_raises_error_when_both_fail(self, mock_file_command, mock_heuristics):
        """Test that error is raised when both methods fail."""
        mock_file_command.return_value = None
        mock_heuristics.return_value = None
        
        with self.assertRaises(subprocess.CalledProcessError):
            detect_blob_type('sha123')


class TestDetectBlobTypesBatchWithMock(unittest.TestCase):
    """Test cases for detect_blob_types_batch function using mocks."""

    @patch('repo_size_guardian.type_detector.detect_blob_type')
    @patch('repo_size_guardian.type_detector.git_cat_file_exists')
    def test_processes_multiple_blobs(self, mock_git_cat_file_exists, mock_detect_blob_type):
        """Test batch processing of multiple blobs."""
        mock_git_cat_file_exists.return_value = True
        mock_detect_blob_type.side_effect = [
            {'is_binary': False, 'mime': 'text/plain', 'confidence': 'high'},
            {'is_binary': True, 'mime': 'application/octet-stream', 'confidence': 'high'}
        ]
        
        result = detect_blob_types_batch(['sha1', 'sha2'])
        
        self.assertEqual(len(result), 2)
        self.assertFalse(result['sha1']['is_binary'])
        self.assertTrue(result['sha2']['is_binary'])

    @patch('repo_size_guardian.type_detector.git_cat_file_exists')
    def test_skips_nonexistent_blobs(self, mock_git_cat_file_exists):
        """Test that non-existent blobs are skipped."""
        mock_git_cat_file_exists.side_effect = [True, False]
        
        with patch('repo_size_guardian.type_detector.detect_blob_type') as mock_detect:
            mock_detect.return_value = {'is_binary': False, 'mime': 'text/plain', 'confidence': 'high'}
            
            result = detect_blob_types_batch(['sha1', 'sha2'])
            
            self.assertEqual(len(result), 1)
            self.assertIn('sha1', result)
            self.assertNotIn('sha2', result)


class TestAugmentBlobObjectsWithTypesWithMock(unittest.TestCase):
    """Test cases for augment_blob_objects_with_types function using mocks."""

    @patch('repo_size_guardian.type_detector.detect_blob_types_batch')
    def test_augments_blobs_with_types(self, mock_detect_blob_types_batch):
        """Test that blobs are augmented with type information."""
        mock_detect_blob_types_batch.return_value = {
            'sha1': {'is_binary': False, 'mime': 'text/plain', 'confidence': 'high'},
            'sha2': {'is_binary': True, 'mime': 'application/octet-stream', 'confidence': 'high'}
        }
        
        blobs = [
            Blob(path='file1.txt', blob_sha='sha1', commit_sha='commit1', status='A'),
            Blob(path='file2.bin', blob_sha='sha2', commit_sha='commit2', status='A')
        ]
        
        result = augment_blob_objects_with_types(blobs)
        
        self.assertFalse(result[0].is_binary)
        self.assertEqual(result[0].mime_type, 'text/plain')
        self.assertTrue(result[1].is_binary)
        self.assertEqual(result[1].mime_type, 'application/octet-stream')

    @patch('repo_size_guardian.type_detector.detect_blob_types_batch')
    def test_handles_deleted_files(self, mock_detect_blob_types_batch):
        """Test that deleted files are handled correctly."""
        mock_detect_blob_types_batch.return_value = {}
        
        blobs = [
            Blob(path='deleted.txt', blob_sha='', commit_sha='commit1', status='D')
        ]
        
        result = augment_blob_objects_with_types(blobs)
        
        self.assertIsNone(result[0].is_binary)
        self.assertIsNone(result[0].mime_type)


if __name__ == '__main__':
    unittest.main()
