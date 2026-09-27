"""
Test suite for formatting module.
"""

import unittest

from repo_size_guardian.formatting import format_number


class TestFormatNumber(unittest.TestCase):
    """Size-limit numbers render the way a person would write them."""

    def test_integer(self):
        self.assertEqual(format_number(500.0), '500')

    def test_decimal(self):
        self.assertEqual(format_number(1.5), '1.5')

    def test_zero(self):
        self.assertEqual(format_number(0.0), '0')

    def test_large_number_has_no_exponent(self):
        self.assertEqual(format_number(1048576.0), '1048576')


if __name__ == '__main__':
    unittest.main()
