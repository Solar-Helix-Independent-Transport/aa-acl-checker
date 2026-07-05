"""
ACL Checker Test
"""

# Django
from django.test import TestCase


class TestAclChecker(TestCase):
    """
    TestAclChecker
    """

    @classmethod
    def setUpClass(cls) -> None:
        """
        Test setup
        :return:
        :rtype:
        """

        super().setUpClass()

    def test_acl_checker(self):
        """
        Dummy test function
        :return:
        :rtype:
        """

        self.assertEqual(True, True)
