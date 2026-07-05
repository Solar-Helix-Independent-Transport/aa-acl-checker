"""Tests for the acl_checker views"""

# Standard Library
import inspect
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

# AA ACL Checker
from acl_checker import views
from acl_checker.models import AccessLevel, Acl, AclCharacterEntry, Owner

# Rendering a real page pulls in allianceauth/base-bs5.html, which needs a
# staticfiles manifest from `collectstatic`. Plain filesystem storage avoids
# that requirement for tests that render a full page.
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=_TEST_STORAGES)
class TestIndexView(TestCase):
    """Tests for views.index"""

    @classmethod
    def setUpTestData(cls):
        cls.acl = Acl.objects.create(esi_id=1, name="Test ACL")

        # Unknown to Auth entirely - shown with only its raw character_id.
        AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=1001, access=AccessLevel.ALLOWED
        )

        # Known to Auth, but unregistered - name should be resolved.
        EveCharacter.objects.create(
            character_id=1002, character_name="Unregistered Guy", corporation_id=2000
        )
        AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=1002, access=AccessLevel.MANAGER
        )

        # Registered - must not appear at all.
        registered_character = EveCharacter.objects.create(
            character_id=1003, character_name="Registered Guy", corporation_id=2000
        )
        registered_user = AuthUtils.create_member("registeredguy")
        CharacterOwnership.objects.create(
            character=registered_character, owner_hash="abc123", user=registered_user
        )
        AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=1003, access=AccessLevel.ALLOWED
        )

    def setUp(self):
        self.user = AuthUtils.create_member("member")
        main_character = AuthUtils.add_main_character_2(self.user, "Main Character", 1099)
        # A viewer only sees ACLs they're Admin on - register this character
        # as owned and grant it Admin on the test ACL.
        CharacterOwnership.objects.create(
            character=main_character, owner_hash="main-hash", user=self.user
        )
        AclCharacterEntry.objects.create(
            acl=self.acl, character_id=1099, access=AccessLevel.ADMIN
        )
        AuthUtils.add_permission_to_user_by_name("acl_checker.basic_access", self.user)
        self.client.force_login(self.user)

    def test_denies_users_with_neither_permission(self):
        no_access_user = AuthUtils.create_member("no_access")
        AuthUtils.add_main_character_2(no_access_user, "No Access", 1098)
        self.client.force_login(no_access_user)

        response = self.client.get(reverse("acl_checker:index"))

        self.assertEqual(response.status_code, 302)

    def test_add_owner_only_user_sees_no_flagged_rows(self):
        """basic_access and add_owner are independent permissions: someone
        who can only link characters must still reach this page (for the
        "Add / Refresh Character" button) but shouldn't see any flagged data.
        """

        add_only_user = AuthUtils.create_member("add_only")
        AuthUtils.add_main_character_2(add_only_user, "Add Only", 1097)
        AuthUtils.add_permission_to_user_by_name("acl_checker.add_owner", add_only_user)
        self.client.force_login(add_only_user)

        response = self.client.get(reverse("acl_checker:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "1001")
        self.assertNotContains(response, "Unregistered Guy")
        self.assertNotContains(response, "Test ACL")

    def test_shows_unregistered_characters_only(self):
        response = self.client.get(reverse("acl_checker:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1001")
        self.assertContains(response, "Unregistered Guy")
        self.assertNotContains(response, "Registered Guy")

    def test_shows_reason_for_unregistered_character(self):
        response = self.client.get(reverse("acl_checker:index"))

        self.assertContains(response, "Not registered")

    def test_shows_tracked_acls_with_counts(self):
        response = self.client.get(reverse("acl_checker:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test ACL")
        # 4 character entries on the ACL (including the viewer's own Admin
        # entry from setUp), 2 of which are flagged.
        self.assertContains(response, "<td>4</td>", html=True)
        self.assertContains(
            response, '<span class="badge text-bg-danger">2</span>', html=True
        )

    def test_hides_acls_the_viewer_is_not_admin_on(self):
        """basic_access alone isn't enough to see an ACL - the viewer must
        also be Admin on it (directly, or via their corp/alliance).
        """

        non_admin_user = AuthUtils.create_member("non_admin")
        AuthUtils.add_main_character_2(non_admin_user, "Non Admin", 1096)
        AuthUtils.add_permission_to_user_by_name(
            "acl_checker.basic_access", non_admin_user
        )
        self.client.force_login(non_admin_user)

        response = self.client.get(reverse("acl_checker:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Test ACL")
        self.assertNotContains(response, "1001")

    def test_superuser_sees_acls_without_being_admin_on_them(self):
        superuser = AuthUtils.create_member("superuser")
        AuthUtils.add_main_character_2(superuser, "Super User", 1095)
        AuthUtils.add_permission_to_user_by_name(
            "acl_checker.basic_access", superuser
        )
        superuser.is_superuser = True
        superuser.save()
        self.client.force_login(superuser)

        response = self.client.get(reverse("acl_checker:index"))

        self.assertContains(response, "Test ACL")


class TestAddOwnerView(TestCase):
    """Tests for views.add_owner permission gating and owner linking"""

    def test_denies_users_without_add_owner_permission(self):
        user = AuthUtils.create_member("no_perms")
        self.client.force_login(user)

        response = self.client.get(reverse("acl_checker:add_owner"))

        self.assertEqual(response.status_code, 302)

    def test_links_character_and_triggers_sync(self):
        character_id = 1001
        EveCharacter.objects.create(
            character_id=character_id, character_name="Manager Guy", corporation_id=2000
        )
        token = SimpleNamespace(character_id=character_id)

        user = AuthUtils.create_member("manager")
        AuthUtils.add_permission_to_user_by_name("acl_checker.add_owner", user)

        request = RequestFactory().get("/acl_checker/add-owner/")
        request.user = user
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        raw_add_owner = inspect.unwrap(views.add_owner)
        with patch("acl_checker.views.sync_owner_acls") as mock_sync:
            response = raw_add_owner(request, token)

        owner = Owner.objects.get(character__character_id=character_id)
        self.assertTrue(owner.is_active)
        mock_sync.delay.assert_called_once_with(owner.pk)
        self.assertEqual(response.status_code, 302)

    def test_reactivates_a_previously_disabled_owner(self):
        character_id = 1002
        character = EveCharacter.objects.create(
            character_id=character_id, character_name="Director Guy", corporation_id=2000
        )
        owner = Owner.objects.create(character=character, is_active=False)
        token = SimpleNamespace(character_id=character_id)

        user = AuthUtils.create_member("director")
        AuthUtils.add_permission_to_user_by_name("acl_checker.add_owner", user)

        request = RequestFactory().get("/acl_checker/add-owner/")
        request.user = user
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        raw_add_owner = inspect.unwrap(views.add_owner)
        with patch("acl_checker.views.sync_owner_acls"):
            raw_add_owner(request, token)

        owner.refresh_from_db()
        self.assertTrue(owner.is_active)
