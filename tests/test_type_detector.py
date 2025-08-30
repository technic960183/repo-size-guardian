"""
Test suite for type_detector module.

Tests text vs binary detection using various file types.
"""

import os
import tempfile
import shutil
import subprocess
import unittest
import json
from pathlib import Path

from repo_size_guardian.type_detector import (
    detect_blob_type,
    detect_blob_types_batch,
    augment_blobs_with_types,
    augment_blob_objects_with_types,
    _detect_type_with_file_command,
    _detect_type_with_content_heuristics
)
from repo_size_guardian.models import Blob


class TypeDetectorTestHelper:
    """Helper class for creating test blobs of different types."""
    
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
        
        # Get the blob SHA for this file
        result = self.run_git('rev-parse', f'HEAD:{path}')
        return result.stdout.strip()


class TestTypeDetector(unittest.TestCase):
    """Test cases for type detection utilities."""
    
    def setUp(self):
        """Set up test environment with temporary git repo."""
        self.test_dir = tempfile.mkdtemp()
        self.helper = TypeDetectorTestHelper(self.test_dir)
        self.helper.init_repo()
        
        # Store original working directory
        self.original_cwd = os.getcwd()
        # Change to test directory for git operations
        os.chdir(self.test_dir)
        
    def tearDown(self):
        """Clean up test environment."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)
        
    def test_detect_text_file(self):
        """Test detection of plain text files."""
        content = "Hello, world!\nThis is a text file.\nIt has multiple lines."
        blob_sha = self.helper.create_and_commit_file('test.txt', content, 'Add text file')
        
        result = detect_blob_type(blob_sha)
        
        self.assertFalse(result['is_binary'])
        self.assertIn(result['confidence'], ['high', 'medium', 'low'])
        
    def test_detect_markdown_file(self):
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
        
    def test_detect_json_file(self):
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
        
    def test_detect_jupyter_notebook(self):
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
        
    def test_detect_binary_file_with_null_bytes(self):
        """Test detection of binary files containing null bytes."""
        # Create binary content with null bytes
        binary_content = b'Hello\x00World\x00Binary\x00File'
        blob_sha = self.helper.create_and_commit_file('test.bin', binary_content, 'Add binary file')
        
        result = detect_blob_type(blob_sha)
        
        self.assertTrue(result['is_binary'])
        
    def test_detect_binary_file_random_bytes(self):
        """Test detection of binary files with random bytes."""
        # Create random binary content
        binary_content = bytes([i % 256 for i in range(0, 256, 3)])  # Mix of bytes
        blob_sha = self.helper.create_and_commit_file('random.bin', binary_content, 'Add random binary')
        
        result = detect_blob_type(blob_sha)
        
        self.assertTrue(result['is_binary'])
        
    def test_detect_empty_file(self):
        """Test detection of empty files."""
        blob_sha = self.helper.create_and_commit_file('empty.txt', '', 'Add empty file')
        
        result = detect_blob_type(blob_sha)
        
        # Empty files should be considered text
        self.assertFalse(result['is_binary'])
        
    def test_detect_type_invalid_blob_sha(self):
        """Test handling of invalid blob SHA."""
        result = detect_blob_type('invalid_sha_that_does_not_exist')
        
        # Should default to binary with low confidence
        self.assertTrue(result['is_binary'])
        self.assertEqual(result['confidence'], 'low')
        
    def test_detect_type_empty_blob_sha(self):
        """Test handling of empty blob SHA."""
        result = detect_blob_type('')
        
        self.assertTrue(result['is_binary'])
        self.assertEqual(result['confidence'], 'low')
        
        result = detect_blob_type('   ')  # Only whitespace
        
        self.assertTrue(result['is_binary'])
        self.assertEqual(result['confidence'], 'low')
        
    def test_detect_blob_types_batch(self):
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
        
    def test_detect_blob_types_batch_with_invalid_sha(self):
        """Test batch detection with invalid SHAs."""
        text_content = "Valid text content."
        valid_sha = self.helper.create_and_commit_file('valid.txt', text_content, 'Add valid file')
        
        results = detect_blob_types_batch([valid_sha, 'invalid_sha', ''])
        
        # Should only contain the valid SHA
        self.assertEqual(len(results), 1)
        self.assertIn(valid_sha, results)
        self.assertFalse(results[valid_sha]['is_binary'])
        
    def test_augment_blobs_with_types(self):
        """Test augmenting blob records with type information."""
        # Create test files
        text_content = "Text file content."
        binary_content = b'Binary\x00content'
        
        text_sha = self.helper.create_and_commit_file('text.txt', text_content, 'Add text')
        binary_sha = self.helper.create_and_commit_file('binary.bin', binary_content, 'Add binary')
        
        # Create blob records
        blobs = [
            {
                'path': 'text.txt',
                'blob_sha': text_sha,
                'commit_sha': 'commit1',
                'status': 'A'
            },
            {
                'path': 'binary.bin',
                'blob_sha': binary_sha,
                'commit_sha': 'commit2',
                'status': 'A'
            }
        ]
        
        # Augment with type information
        augmented = augment_blobs_with_types(blobs)
        
        self.assertEqual(len(augmented), 2)
        
        # Check text file
        text_blob = next(b for b in augmented if b['path'] == 'text.txt')
        self.assertFalse(text_blob['is_binary'])
        self.assertIsNotNone(text_blob['type_confidence'])
        
        # Check binary file
        binary_blob = next(b for b in augmented if b['path'] == 'binary.bin')
        self.assertTrue(binary_blob['is_binary'])
        self.assertIsNotNone(binary_blob['type_confidence'])
        
    def test_augment_blobs_with_types_deleted_file(self):
        """Test augmenting blob records including deleted files."""
        # Create a text file
        text_content = "Text file content."
        text_sha = self.helper.create_and_commit_file('text.txt', text_content, 'Add text')
        
        # Create blob records including a deleted file
        blobs = [
            {
                'path': 'text.txt',
                'blob_sha': text_sha,
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
        
        # Augment with type information
        augmented = augment_blobs_with_types(blobs)
        
        self.assertEqual(len(augmented), 2)
        
        # Normal file should have type info
        text_blob = next(b for b in augmented if b['path'] == 'text.txt')
        self.assertFalse(text_blob['is_binary'])
        
        # Deleted file should have None values
        deleted_blob = next(b for b in augmented if b['path'] == 'deleted.txt')
        self.assertIsNone(deleted_blob['is_binary'])
        self.assertIsNone(deleted_blob['mime_type'])
        self.assertIsNone(deleted_blob['type_confidence'])
        
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
        
    def test_large_text_file(self):
        """Test detection of larger text files."""
        # Create a larger text file
        content = "Line of text.\n" * 1000  # 1000 lines
        blob_sha = self.helper.create_and_commit_file('large.txt', content, 'Add large text')
        
        result = detect_blob_type(blob_sha)
        
        self.assertFalse(result['is_binary'])


if __name__ == '__main__':
    unittest.main()