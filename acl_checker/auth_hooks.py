"""Hook into Alliance Auth"""

# Django
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

# AA ACL Checker
from acl_checker import urls


class AclCheckerMenuItem(MenuItemHook):
    """This class ensures only authorized users will see the menu entry"""

    def __init__(self):
        # setup menu entry for sidebar
        MenuItemHook.__init__(
            self,
            _("ACL Checker"),
            "fas fa-user-shield fa-fw",
            "acl_checker:index",
            navactive=["acl_checker:"],
        )

    def render(self, request):
        """Render the menu item

        Shown to anyone who can either view flagged characters or link a
        token - the two are independent permissions.
        """

        if request.user.has_perm("acl_checker.basic_access") or request.user.has_perm(
            "acl_checker.add_owner"
        ):
            return MenuItemHook.render(self, request)

        return ""


@hooks.register("menu_item_hook")
def register_menu():
    """Register the menu item"""

    return AclCheckerMenuItem()


@hooks.register("url_hook")
def register_urls():
    """Register app urls"""

    return UrlHook(urls, "acl_checker", r"^acl_checker/")
