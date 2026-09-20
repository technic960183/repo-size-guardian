"""
Test suite for git_utils module.

Tests low-level git operations including cat-file commands,
commit ranges, and file change detection.
"""

import subprocess
from unittest import mock

from repo_size_guardian.git_utils import (
    get_blob_sha_at_commit,
    get_diff_files,
    get_diff_files_between,
    get_merge_base,
    git_cat_file_content,
    git_cat_file_exists,
    git_cat_file_size,
    list_commits,
    parse_raw_diff_z,
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
        # The reported SHA must be the post-image blob, resolved here
        # independently of how get_diff_files parses diff-tree output.
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(commit_sha, 'file2.txt')
        )

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
        for entry in files:
            self.assertEqual(
                entry['blob_sha'],
                get_blob_sha_at_commit(commit_sha, entry['path'])
            )

    def test_file_modified(self):
        """Test getting diff files for a commit that modifies a file."""
        parent_sha = self.helper.commit_file('file1.txt', 'original', 'Initial commit')
        commit_sha = self.helper.commit_file('file1.txt', 'modified', 'Modify file1')

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['status'], 'M')
        self.assertEqual(files[0]['path'], 'file1.txt')
        # For a modification both a pre-image and a post-image blob exist, so
        # this is where picking the wrong raw field silently reports content
        # that was never introduced by the commit. Pin the post-image blob,
        # and assert it is not the pre-image one.
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(commit_sha, 'file1.txt')
        )
        self.assertNotEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(parent_sha, 'file1.txt')
        )

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
        """Test that a root commit's files are reported as additions.

        A parentless commit has nothing to be diffed against implicitly, so
        without --root git diff-tree prints nothing for it and every file the
        commit introduces escapes the scan.
        """
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Initial commit')

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['status'], 'A')
        self.assertEqual(files[0]['path'], 'file1.txt')
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(commit_sha, 'file1.txt')
        )

    def test_orphan_root_commit_in_history(self):
        """Test that a root commit reached mid-history is not skipped.

        An orphan branch merged into a PR puts a second parentless commit in
        the scanned range; the blob it introduces exists in no other commit.
        """
        self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        self.helper.create_orphan_branch('orphan')
        orphan_sha = self.helper.commit_file(
            'orphan_only.txt', 'only here', 'Orphan root commit')

        # Sanity check: the commit really has no parents.
        parents = self.helper.run_git(
            'rev-list', '--parents', '-n', '1', orphan_sha
        ).stdout.split()
        self.assertEqual(len(parents) - 1, 0)

        files = get_diff_files(orphan_sha)
        paths = [f['path'] for f in files]
        self.assertIn('orphan_only.txt', paths)
        entry = next(f for f in files if f['path'] == 'orphan_only.txt')
        self.assertEqual(entry['status'], 'A')
        self.assertEqual(
            entry['blob_sha'],
            get_blob_sha_at_commit(orphan_sha, 'orphan_only.txt')
        )

    def test_non_ascii_path_is_not_quoted(self):
        """Test that a non-ASCII path is reported literally.

        With git's default core.quotePath, diff-tree renders 'café.txt' as
        the literal C-quoted string '"caf\\303\\251.txt"', which matches no
        real file and breaks any downstream reporting keyed on the path.
        """
        self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        commit_sha = self.helper.commit_file('café.txt', 'unicode', 'Add unicode name')

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['path'], 'café.txt')
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(commit_sha, 'café.txt')
        )

    def test_trailing_whitespace_in_path_is_preserved(self):
        """Test that trailing whitespace in a filename survives parsing.

        'zz trailing ' is a legal filename; stripping the raw diff-tree
        output eats its trailing space when it is the last entry, so the
        reported path names a file that does not exist.
        """
        self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        path = 'zz trailing '
        commit_sha = self.helper.commit_file(path, 'spaces', 'Add trailing-space name')

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['path'], path)
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(commit_sha, path)
        )

    def test_submodule_gitlink_is_skipped(self):
        """Test that a submodule entry is not reported as a blob.

        A gitlink's recorded SHA is a commit in the submodule's own
        repository, so it names no object here and `git cat-file` on it
        fails. Regular files added alongside it must still be reported.
        """
        self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        self.helper.create_file('regular.txt', 'regular content')
        self.helper.run_git('add', 'regular.txt')
        commit_sha = self.helper.commit_gitlink('mysub', 'Add submodule and a file')

        # Sanity check: the commit really records a gitlink tree entry.
        tree_entry = self.helper.run_git('ls-tree', commit_sha, 'mysub').stdout
        self.assertTrue(tree_entry.startswith('160000 commit '), tree_entry)

        files = get_diff_files(commit_sha)
        paths = [f['path'] for f in files]
        self.assertEqual(paths, ['regular.txt'])
        self.assertEqual(files[0]['status'], 'A')
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(commit_sha, 'regular.txt')
        )

    def test_submodule_replaced_by_regular_file_is_reported(self):
        """Test that a file replacing a submodule is still reported.

        Only the post-image mode decides: when a gitlink becomes a real file,
        the new content is a blob this scanner must see.
        """
        self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        self.helper.commit_gitlink('mysub', 'Add submodule')
        self.helper.run_git('rm', '--cached', 'mysub')
        commit_sha = self.helper.commit_file('mysub', 'now a real file', 'Replace submodule')

        files = get_diff_files(commit_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['path'], 'mysub')
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(commit_sha, 'mysub')
        )

    def test_merge_commit_skips_gitlink_and_keeps_literal_paths(self):
        """Test that the merge code path parses output the same way.

        A merge is diffed with a second, two-tree-ish diff-tree invocation,
        which must carry the same -z and gitlink handling as the single-commit
        form.
        """
        self.helper.commit_file('data.txt', 'base', 'Initial commit')
        self.helper.create_branch('feature')
        self.helper.create_file('café.txt', 'unicode')
        self.helper.run_git('add', 'café.txt')
        self.helper.commit_gitlink('mysub', 'Add submodule and unicode file')
        self.helper.checkout('main')
        self.helper.commit_file('main.txt', 'from main', 'Add main file')
        merge_sha = self.helper.merge_branch('feature', 'Merge feature')

        files = get_diff_files(merge_sha)
        paths = sorted(f['path'] for f in files)
        self.assertEqual(paths, ['café.txt'])
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(merge_sha, 'café.txt')
        )

    def test_merge_commit(self):
        """Test that a merge commit reports the changes it introduces.

        git diff-tree has no single parent to diff a merge against, so without
        -m/--cc it prints nothing and every change carried by the merge is
        invisible.
        """
        self.helper.commit_file('data.txt', 'base', 'Initial commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('feature.txt', 'from feature', 'Add feature file')
        self.helper.checkout('main')
        self.helper.commit_file('main.txt', 'from main', 'Add main file')
        merge_sha = self.helper.merge_branch('feature', 'Merge feature')

        files = get_diff_files(merge_sha)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['path'], 'feature.txt')
        self.assertEqual(files[0]['status'], 'A')
        # A merge is diffed against its first parent explicitly, so pin the
        # post-image blob it reports for that second code path too.
        self.assertEqual(
            files[0]['blob_sha'],
            get_blob_sha_at_commit(merge_sha, 'feature.txt')
        )

    def test_octopus_merge_commit(self):
        """Test that an octopus merge reports first-parent changes exactly once.

        An octopus merge has three or more parents. Diffing against every
        parent (e.g. with -m) repeats the same change once per parent, so the
        diff must be taken against the first parent only.
        """
        self.helper.commit_file('data.txt', 'base', 'Initial commit')

        for branch in ('feature1', 'feature2', 'feature3'):
            self.helper.run_git('checkout', '-b', branch, 'main')
            self.helper.commit_file(f'{branch}.txt', f'from {branch}', f'Add {branch}')

        self.helper.checkout('main')
        self.helper.commit_file('main.txt', 'from main', 'Add main file')
        self.helper.run_git('merge', '--no-ff', '-m', 'Octopus merge',
                            'feature1', 'feature2', 'feature3')
        merge_sha = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        # Sanity check: this really is an octopus merge (main + 3 branches).
        parents = self.helper.run_git(
            'rev-list', '--parents', '-n', '1', merge_sha
        ).stdout.split()
        self.assertEqual(len(parents) - 1, 4)

        files = get_diff_files(merge_sha)

        # Each feature file is reported exactly once; main.txt came from the
        # first parent and so is not part of the merge's own changes.
        paths = [f['path'] for f in files]
        self.assertEqual(
            sorted(paths),
            ['feature1.txt', 'feature2.txt', 'feature3.txt']
        )
        self.assertEqual(len(paths), len(set(paths)))
        for entry in files:
            self.assertEqual(entry['status'], 'A')
            self.assertEqual(
                entry['blob_sha'],
                get_blob_sha_at_commit(merge_sha, entry['path'])
            )

    def test_empty_commit_has_no_changes(self):
        """Test that a commit identical to its parent reports no files.

        A non-merge, non-root commit with no tree changes makes diff-tree
        print nothing, so get_diff_files must return an empty list rather
        than treating that as the merge or malformed-output case.
        """
        self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        self.helper.run_git('commit', '--allow-empty', '-m', 'Empty commit')
        commit_sha = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        files = get_diff_files(commit_sha)
        self.assertEqual(files, [])

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
        for entry in files:
            if entry['status'] == 'D':
                # Deleted paths have no post-image blob; git reports zeros.
                self.assertEqual(entry['blob_sha'], '0' * 40)
            else:
                self.assertEqual(
                    entry['blob_sha'],
                    get_blob_sha_at_commit(commit_sha, entry['path'])
                )


class TestGetDiffFilesMalformedOutput(GitRepoTestBase):
    """Test the raw-format defensive checks in get_diff_files.

    `git diff-tree --raw -z` never actually produces these shapes with the
    flags this function passes (rename/copy detection is off, and the
    metadata/path record shape is otherwise fixed), so subprocess.run is
    patched to return crafted bytes in place of the real diff-tree output,
    with every other git invocation (e.g. commit setup) still running for
    real.
    """

    def _get_diff_files_with_fake_diff_tree_output(self, commit_sha: str, raw_stdout: bytes):
        real_run = subprocess.run

        def fake_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get('args')
            if cmd[:2] == ['git', 'diff-tree']:
                return subprocess.CompletedProcess(cmd, 0, stdout=raw_stdout, stderr=b'')
            return real_run(*args, **kwargs)

        with mock.patch('repo_size_guardian.git_utils.subprocess.run', side_effect=fake_run):
            return get_diff_files(commit_sha)

    def test_meta_not_starting_with_colon_raises(self):
        """A metadata field is expected to always start with ':'."""
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Initial commit')

        with self.assertRaises(ValueError):
            self._get_diff_files_with_fake_diff_tree_output(commit_sha, b'garbage\0path.txt\0')

    def test_meta_with_wrong_field_count_raises(self):
        """A metadata field must carry exactly 5 space-separated parts."""
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        meta = b':100644 100644 ' + b'0' * 40 + b' ' + b'1' * 40  # missing status

        with self.assertRaises(ValueError):
            self._get_diff_files_with_fake_diff_tree_output(commit_sha, meta + b'\0path.txt\0')

    def test_missing_path_field_raises(self):
        """A metadata field with no following path field is malformed."""
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        meta = (':100644 100644 ' + '0' * 40 + ' ' + '1' * 40 + ' A').encode()

        with self.assertRaises(ValueError):
            self._get_diff_files_with_fake_diff_tree_output(commit_sha, meta + b'\0')

    def test_rename_status_raises(self):
        """A rename/copy status carries two paths, which this function,
        never having requested -M/-C, does not support."""
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        meta = (':100644 100644 ' + '0' * 40 + ' ' + '1' * 40 + ' R100').encode()

        with self.assertRaises(ValueError):
            self._get_diff_files_with_fake_diff_tree_output(
                commit_sha, meta + b'\0old.txt\0new.txt\0')

    def test_output_without_trailing_nul_is_still_parsed(self):
        """The trailing empty field from -z's final NUL is only dropped when
        present; output missing it must still parse the real records."""
        commit_sha = self.helper.commit_file('file1.txt', 'content', 'Initial commit')
        meta = (':100644 100644 ' + '0' * 40 + ' ' + '1' * 40 + ' A').encode()
        raw_stdout = meta + b'\0path.txt'  # no trailing NUL

        files = self._get_diff_files_with_fake_diff_tree_output(commit_sha, raw_stdout)
        self.assertEqual(files, [{'status': 'A', 'path': 'path.txt', 'blob_sha': '1' * 40}])


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


class TestGetDiffFilesBetween(GitRepoTestBase):
    """
    Two-tree diffs, used by `scan_mode=diff`.

    This shares `parse_raw_diff_z` with `get_diff_files`; these tests exist
    so the two callers cannot drift apart on the awkward cases (gitlinks,
    deletions, NUL-separated and non-ASCII paths) the way two hand-copied
    parsers would.
    """

    def test_reports_net_additions_and_modifications(self):
        base = self.helper.commit_file('a.txt', 'one', 'Base')
        self.helper.commit_file('a.txt', 'two', 'Modify a')
        head = self.helper.commit_file('b.txt', 'new', 'Add b')

        changes = get_diff_files_between(base, head)
        by_path = {change['path']: change for change in changes}
        self.assertEqual(set(by_path), {'a.txt', 'b.txt'})
        self.assertEqual(by_path['a.txt']['status'], 'M')
        self.assertEqual(by_path['b.txt']['status'], 'A')
        self.assertEqual(len(by_path['b.txt']['blob_sha']), 40)

    def test_collapses_intermediate_states(self):
        # A blob added and then removed between the two trees does not
        # appear at all: this is exactly why `diff` mode is weaker than
        # `history` mode, and it must stay true.
        base = self.helper.commit_file('a.txt', 'one', 'Base')
        self.helper.create_and_commit_file('transient.bin', b'\x00' * 100, 'Add')
        self.helper.delete_file('transient.bin', 'Remove')
        head = self.helper.commit_file('a.txt', 'two', 'Modify a')

        paths = [change['path'] for change in get_diff_files_between(base, head)]
        self.assertEqual(paths, ['a.txt'])

    def test_deletion_reports_all_zero_blob_sha(self):
        base = self.helper.commit_file('gone.txt', 'bye', 'Base')
        head = self.helper.delete_file('gone.txt', 'Delete it')

        changes = get_diff_files_between(base, head)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['status'], 'D')
        self.assertEqual(changes[0]['blob_sha'], '0' * 40)

    def test_gitlink_entries_are_skipped(self):
        base = self.helper.commit_file('a.txt', 'one', 'Base')
        head = self.helper.commit_gitlink('vendor/sub', 'Add submodule')

        paths = [change['path'] for change in get_diff_files_between(base, head)]
        self.assertNotIn('vendor/sub', paths)

    def test_non_ascii_and_whitespace_paths_survive_intact(self):
        base = self.helper.commit_file('a.txt', 'one', 'Base')
        self.helper.create_file('caf\u00e9/r\u00e9sum\u00e9 v2 .txt', 'x')
        self.helper.run_git('add', '.')
        self.helper.run_git('commit', '-m', 'Add awkward path')
        head = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()

        paths = [change['path'] for change in get_diff_files_between(base, head)]
        self.assertIn('caf\u00e9/r\u00e9sum\u00e9 v2 .txt', paths)

    def test_identical_trees_produce_no_changes(self):
        base = self.helper.commit_file('a.txt', 'one', 'Base')
        self.assertEqual(get_diff_files_between(base, base), [])

    def test_matches_get_diff_files_for_a_single_commit(self):
        # The two entry points must agree: a two-tree diff of parent..commit
        # is the same change set as the single-commit diff of that commit.
        self.helper.commit_file('a.txt', 'one', 'Base')
        self.helper.commit_file('a.txt', 'two', 'Modify a')
        self.helper.create_and_commit_file('b.bin', b'\x00' * 50, 'Add b')
        head = self.helper.run_git('rev-parse', 'HEAD').stdout.strip()
        parent = self.helper.run_git('rev-parse', 'HEAD~1').stdout.strip()

        self.assertEqual(get_diff_files_between(parent, head), get_diff_files(head))


class TestParseRawDiffZ(GitRepoTestBase):
    """Direct tests for the shared raw-diff parser."""

    def test_empty_output_is_no_changes(self):
        self.assertEqual(parse_raw_diff_z(b''), [])

    def test_rename_status_is_rejected_rather_than_misparsed(self):
        # A rename record carries two paths, which would desynchronise the
        # metadata/path pairing and silently mis-attribute every subsequent
        # blob. Rename detection is never requested, so this must raise.
        raw = (b':100644 100644 ' + b'a' * 40 + b' ' + b'b' * 40 + b' R100\0'
               b'old.txt\0new.txt\0')
        with self.assertRaises(ValueError):
            parse_raw_diff_z(raw)

    def test_truncated_record_is_rejected(self):
        raw = b':100644 100644 ' + b'a' * 40 + b' ' + b'b' * 40 + b' M\0'
        with self.assertRaises(ValueError):
            parse_raw_diff_z(raw)

    def test_garbage_output_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_raw_diff_z(b'not a diff record\0')
