"""
Test suite for repo-size-guardian.
"""

import unittest

from repo_size_guardian import __version__


class TestBasic(unittest.TestCase):
    """Basic tests for the repo-size-guardian package."""
    
    def test_version_exists(self):
        """Test that version is defined."""
        self.assertIsNotNone(__version__)
        self.assertIsInstance(__version__, str)
        self.assertTrue(len(__version__) > 0)
    
    def test_import_main(self):
        """Test that main module can be imported."""
        from repo_size_guardian import main
        self.assertIsNotNone(main)


if __name__ == '__main__':
    unittest.main()