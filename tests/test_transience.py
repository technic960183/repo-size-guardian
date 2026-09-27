"""
Test suite for the transience module.

Covers `augment_blob_objects_with_transience`'s diff-mode short-circuit and
its history-mode tree comparison (added-then-deleted, shrunk-under-the-
same-name, restored-to-original-content, and unchanged file versions, plus
submodule entries and deleted/no-content blobs), against real temporary git
repositories (see tests/test_base.py) so the flags are checked against
actual merge-base and head tree listings rather than a hand-rolled model of
them.
"""

import unittest

from repo_size_guardian.git_utils import get_blob_sha_at_commit
from repo_size_guardian.models import Blob
from repo_size_guardian.transience import augment_blob_objects_with_transience
from tests.test_base import GitRepoTestBase


def make_blob(path, blob_sha, commit_sha, status='A'):
    """Build a Blob for transience tests; only path/blob_sha/status/commit_sha matter here."""
    return Blob(path=path, blob_sha=blob_sha, commit_sha=commit_sha, status=status)


class TestDiffModeShortCircuit(GitRepoTestBase):
    """In diff mode both fields are always False, and no tree is ever listed."""

    def test_both_flags_false_regardless_of_content(self):
        base = self.helper.commit_file('a.txt', 'one', 'Base')
        head = self.helper.commit_file('a.txt', 'two', 'Modify')
        blob = make_blob('a.txt', get_blob_sha_at_commit(head, 'a.txt'), head)

        augment_blob_objects_with_transience([blob], 'diff', base, head)

        self.assertFalse(blob.is_transient)
        self.assertFalse(blob.is_transient_version)

    def test_does_not_list_any_tree_so_bogus_refs_do_not_raise(self):
        # Diff mode must not even attempt to resolve merge_base/head_sha:
        # both are set unconditionally, with no git call at all.
        blob = make_blob('a.txt', 'a' * 40, 'b' * 40)
        augment_blob_objects_with_transience(
            [blob], 'diff', 'not-a-real-ref', 'also-not-a-real-ref')
        self.assertFalse(blob.is_transient)
        self.assertFalse(blob.is_transient_version)


class TestHistoryModeAddedThenDeleted(GitRepoTestBase):
    """A file added then deleted within the PR: both fields True."""

    def test_added_then_deleted(self):
        base = self.helper.commit_file('base.txt', 'base', 'Base commit')
        self.helper.create_branch('feature')
        add_commit = self.helper.commit_file('scratch.txt', 'temp', 'Add scratch')
        blob_sha = get_blob_sha_at_commit(add_commit, 'scratch.txt')
        head = self.helper.delete_file('scratch.txt', 'Remove scratch')
        blob = make_blob('scratch.txt', blob_sha, add_commit)

        augment_blob_objects_with_transience([blob], 'history', base, head)

        self.assertTrue(blob.is_transient)
        self.assertTrue(blob.is_transient_version)


class TestHistoryModeShrunkUnderSameName(GitRepoTestBase):
    """A big file added then shrunk under the same name."""

    def test_big_version_is_transient_version_but_not_transient(self):
        base = self.helper.commit_file('base.txt', 'base', 'Base commit')
        self.helper.create_branch('feature')
        big_commit = self.helper.commit_file('data.bin', 'X' * 1000, 'Add big version')
        big_sha = get_blob_sha_at_commit(big_commit, 'data.bin')
        head = self.helper.commit_file('data.bin', 'small', 'Shrink it')

        big_blob = make_blob('data.bin', big_sha, big_commit)
        augment_blob_objects_with_transience([big_blob], 'history', base, head)

        self.assertFalse(big_blob.is_transient)
        self.assertTrue(big_blob.is_transient_version)

    def test_final_version_is_neither(self):
        base = self.helper.commit_file('base.txt', 'base', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('data.bin', 'X' * 1000, 'Add big version')
        head = self.helper.commit_file('data.bin', 'small', 'Shrink it')
        small_sha = get_blob_sha_at_commit(head, 'data.bin')

        small_blob = make_blob('data.bin', small_sha, head)
        augment_blob_objects_with_transience([small_blob], 'history', base, head)

        self.assertFalse(small_blob.is_transient)
        self.assertFalse(small_blob.is_transient_version)


class TestHistoryModeRestoredToOriginalContent(GitRepoTestBase):
    """A file present at the merge-base, changed, then restored to its original content."""

    def test_middle_version_is_transient_version_but_not_transient(self):
        base = self.helper.commit_file('config.txt', 'original', 'Base commit')
        self.helper.create_branch('feature')
        middle_commit = self.helper.commit_file('config.txt', 'temporary edit', 'Edit config')
        middle_sha = get_blob_sha_at_commit(middle_commit, 'config.txt')
        head = self.helper.commit_file('config.txt', 'original', 'Restore config')

        # Sanity check: this really is the same content as the base version,
        # not merely equal text -- the same blob SHA.
        self.assertEqual(
            get_blob_sha_at_commit(base, 'config.txt'), get_blob_sha_at_commit(head, 'config.txt'))

        middle_blob = make_blob('config.txt', middle_sha, middle_commit)
        augment_blob_objects_with_transience([middle_blob], 'history', base, head)

        self.assertFalse(middle_blob.is_transient)
        self.assertTrue(middle_blob.is_transient_version)

    def test_restored_version_is_neither(self):
        base = self.helper.commit_file('config.txt', 'original', 'Base commit')
        self.helper.create_branch('feature')
        self.helper.commit_file('config.txt', 'temporary edit', 'Edit config')
        head = self.helper.commit_file('config.txt', 'original', 'Restore config')
        restored_sha = get_blob_sha_at_commit(head, 'config.txt')

        restored_blob = make_blob('config.txt', restored_sha, head)
        augment_blob_objects_with_transience([restored_blob], 'history', base, head)

        self.assertFalse(restored_blob.is_transient)
        self.assertFalse(restored_blob.is_transient_version)


class TestHistoryModeUnchangedFile(GitRepoTestBase):
    """A file version that stays unchanged all the way to the head: both fields False."""

    def test_unchanged_file(self):
        base = self.helper.commit_file('base.txt', 'base', 'Base commit')
        self.helper.create_branch('feature')
        head = self.helper.commit_file('readme.txt', 'hello', 'Add readme')
        blob = make_blob('readme.txt', get_blob_sha_at_commit(head, 'readme.txt'), head)

        augment_blob_objects_with_transience([blob], 'history', base, head)

        self.assertFalse(blob.is_transient)
        self.assertFalse(blob.is_transient_version)


class TestHistoryModeSubmoduleEntriesIgnored(GitRepoTestBase):
    """A gitlink entry is never counted as a path/version surviving."""

    def test_gitlink_at_head_does_not_count_as_the_path_surviving(self):
        base = self.helper.commit_file('other.txt', 'x', 'Base commit (no mysub yet)')
        self.helper.create_branch('feature')
        add_commit = self.helper.commit_file('mysub', 'placeholder content', 'Add mysub as a file')
        blob_sha = get_blob_sha_at_commit(add_commit, 'mysub')
        self.helper.run_git('rm', '--cached', 'mysub')
        head = self.helper.commit_gitlink('mysub', 'Replace file with a submodule')

        blob = make_blob('mysub', blob_sha, add_commit)
        augment_blob_objects_with_transience([blob], 'history', base, head)

        # `list_tree_blobs` skips gitlink entries entirely, so the path does
        # not "survive" at head even though a tree entry for it still
        # exists there.
        self.assertTrue(blob.is_transient)
        self.assertTrue(blob.is_transient_version)


class TestDeletedOrEmptyShaBlobsAreLeftUndetermined(GitRepoTestBase):
    """A deleted file version, or one with no content, is left at None."""

    def test_history_mode_deleted_status_leaves_none(self):
        base = self.helper.commit_file('a.txt', 'content', 'Base')
        self.helper.create_branch('feature')
        self.helper.commit_file('gone.txt', 'temp', 'Add gone')
        head = self.helper.delete_file('gone.txt', 'Delete gone')
        blob = Blob(path='gone.txt', blob_sha='', commit_sha=head, status='D')

        augment_blob_objects_with_transience([blob], 'history', base, head)

        self.assertIsNone(blob.is_transient)
        self.assertIsNone(blob.is_transient_version)

    def test_history_mode_empty_sha_leaves_none_even_if_not_marked_deleted(self):
        base = self.helper.commit_file('a.txt', 'content', 'Base')
        head = self.helper.commit_file('b.txt', 'content2', 'Head')
        blob = Blob(path='b.txt', blob_sha='', commit_sha=head, status='A')

        augment_blob_objects_with_transience([blob], 'history', base, head)

        self.assertIsNone(blob.is_transient)
        self.assertIsNone(blob.is_transient_version)

    def test_diff_mode_still_sets_false_for_a_deleted_status(self):
        # Diff mode's short-circuit sets every blob unconditionally -- it is
        # evaluator.py, not this pass, that skips deleted blobs.
        blob = Blob(path='gone.txt', blob_sha='', commit_sha='x' * 40, status='D')
        augment_blob_objects_with_transience([blob], 'diff', 'a' * 40, 'b' * 40)
        self.assertFalse(blob.is_transient)
        self.assertFalse(blob.is_transient_version)


class TestReturnsTheSameList(GitRepoTestBase):
    """The function returns its input list, like the other augmentation passes."""

    def test_returns_input_list(self):
        head = self.helper.commit_file('a.txt', 'content', 'Base')
        blobs = [make_blob('a.txt', get_blob_sha_at_commit(head, 'a.txt'), head)]
        result = augment_blob_objects_with_transience(blobs, 'diff', head, head)
        self.assertIs(result, blobs)


if __name__ == '__main__':
    unittest.main()
