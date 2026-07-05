"""App Configuration"""

# Django
from django.apps import AppConfig

# AA ACL Checker
from acl_checker import __version__


class AclCheckerConfig(AppConfig):
    """App Config"""

    name = "acl_checker"
    label = "acl_checker"
    verbose_name = f"ACL Checker v{__version__}"
