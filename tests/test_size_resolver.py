"""
Test suite for size_resolver module.

Tests blob size resolution using git cat-file commands.
"""

import os
import subprocess
import unittest
from unittest.mock import patch

from repo_size_guardian.models import Blob, Violation
from repo_size_guardian.size_resolver import (
    augment_blob_objects_with_sizes,
    get_blob_size,
    get_blob_sizes_batch,
)
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


class TestGetBlobSizesBatch(GitRepoTestBase):
    """
    Behavioural tests for get_blob_sizes_batch against a real repository.

    These replace two earlier tests that mocked `git_cat_file_size` and so
    only asserted that the function looped over that mock. They assert the
    same contract (every requested blob measured; unmeasurable ones
    skipped) against real git output, which also pins the
    `git cat-file --batch-check` protocol handling the function now relies
    on: one result line per input line, positionally paired.
    """

    def test_returns_exact_sizes_for_every_blob(self):
        contents = ['a', 'bb' * 500, 'c' * 100000]
        shas = [self.helper.create_and_commit_file('f%d.txt' % i, text, 'Add')
                for i, text in enumerate(contents)]

        result = get_blob_sizes_batch(shas)

        self.assertEqual(len(result), 3)
        for sha, text in zip(shas, contents):
            self.assertEqual(result[sha], len(text.encode('utf-8')))

    def test_sizes_are_not_shifted_between_blobs(self):
        # Positional pairing of batch output with batch input means an
        # off-by-one would return plausible-looking but wrong sizes for
        # every blob, which is how a 200 MB file slips past a threshold.
        sizes = [10, 20, 30, 40, 50]
        shas = [self.helper.create_and_commit_file('g%d.txt' % n, 'x' * n, 'Add')
                for n in sizes]

        result = get_blob_sizes_batch(shas)

        self.assertEqual([result[sha] for sha in shas], sizes)

    def test_empty_blob_is_zero_not_missing(self):
        sha = self.helper.create_and_commit_file('empty.txt', '', 'Add empty')
        self.assertEqual(get_blob_sizes_batch([sha]), {sha: 0})

    def test_unknown_sha_is_skipped_without_losing_the_others(self):
        good = self.helper.create_and_commit_file('good.txt', 'hello', 'Add')
        missing = 'f' * 40

        result = get_blob_sizes_batch([good, missing, 'not_a_sha_at_all'])

        self.assertEqual(result, {good: 5})

    def test_empty_and_blank_shas_are_skipped(self):
        good = self.helper.create_and_commit_file('good.txt', 'hello', 'Add')
        result = get_blob_sizes_batch(['', '   ', good])
        self.assertEqual(result, {good: 5})

    def test_empty_input_list(self):
        self.assertEqual(get_blob_sizes_batch([]), {})

    def test_duplicate_shas_are_resolved_once_and_returned_once(self):
        sha = self.helper.create_and_commit_file('dup.txt', 'hello', 'Add')
        self.assertEqual(get_blob_sizes_batch([sha, sha, sha]), {sha: 5})

    def test_many_blobs_do_not_deadlock_on_pipe_buffers(self):
        # Feeding thousands of names to a single child process while
        # reading its output must not fill either pipe and hang; 64 KiB is
        # the usual pipe buffer, and 3000 names (41 bytes in, ~60 out)
        # exceed it in both directions.
        blob_dir = os.path.join(self.test_dir, 'bulk')
        os.makedirs(blob_dir)
        paths = []
        lengths = []
        for i in range(3000):
            path = os.path.join(blob_dir, 'f%04d.txt' % i)
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('x' * (i + 1))
            paths.append(path)
            lengths.append(i + 1)
        shas = subprocess.run(
            ['git', 'hash-object', '-w', '--stdin-paths'],
            cwd=self.test_dir, input='\n'.join(paths) + '\n',
            capture_output=True, text=True, check=True).stdout.split()
        expected = dict(zip(shas, lengths))
        self.assertEqual(len(expected), 3000)

        result = get_blob_sizes_batch(list(expected))

        self.assertEqual(result, expected)

    def test_revision_syntax_with_whitespace_falls_back(self):
        # A name with whitespace cannot ride the line-oriented batch
        # protocol; it must still be resolved rather than silently dropped.
        self.helper.create_and_commit_file('has space.txt', 'hello', 'Add')
        commit = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        name = '%s:has space.txt' % commit

        self.assertEqual(get_blob_sizes_batch([name]), {name: 5})


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
