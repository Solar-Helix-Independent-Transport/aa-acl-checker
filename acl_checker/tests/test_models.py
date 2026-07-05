"""
Tests for the Acl / AclCharacterEntry flagging logic
"""

# Django
from django.contrib.auth.models import User
from django.test import TestCase

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

# AA ACL Checker
from acl_checker.models import (
    AccessLevel,
    Acl,
    AclAllianceEntry,
    AclCharacterEntry,
    AclCorporationEntry,
    Config,
)


class TestAclCharacterEntryFlagging(TestCase):
    """
    Verify that AclCharacterEntry.objects.flagged() correctly identifies
    characters who are granted access on an ACL but are not an owned
    character in Auth.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.acl = Acl.objects.create(esi_id=1, name="Test ACL")

        # Character not known to Auth at all, but granted access
        cls.unknown_member = AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=1001, access=AccessLevel.ALLOWED
        )

        # Character known to Auth, but not owned/registered by any user
        EveCharacter.objects.create(
            character_id=1002,
            character_name="Unregistered Guy",
            corporation_id=2000,
        )
        cls.unregistered_member = AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=1002, access=AccessLevel.MANAGER
        )

        # Character known to Auth and owned by a user
        registered_character = EveCharacter.objects.create(
            character_id=1003,
            character_name="Registered Guy",
            corporation_id=2000,
        )
        user = User.objects.create_user(username="registeredguy")
        CharacterOwnership.objects.create(
            character=registered_character, owner_hash="abc123", user=user
        )
        cls.registered_member = AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=1003, access=AccessLevel.ALLOWED
        )

        # Blocked and unregistered: should never be flagged
        cls.blocked_member = AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=1004, access=AccessLevel.BLOCKED
        )

    def test_flagged_members_excludes_registered_character(self):
        """
        :return:
        :rtype:
        """

        flagged_ids = set(
            AclCharacterEntry.objects.flagged().values_list(
                "character_id", flat=True
            )
        )

        self.assertIn(self.unknown_member.character_id, flagged_ids)
        self.assertIn(self.unregistered_member.character_id, flagged_ids)
        self.assertNotIn(self.registered_member.character_id, flagged_ids)

    def test_flagged_members_excludes_blocked_entries(self):
        """
        :return:
        :rtype:
        """

        flagged_ids = set(
            AclCharacterEntry.objects.flagged().values_list(
                "character_id", flat=True
            )
        )

        self.assertNotIn(self.blocked_member.character_id, flagged_ids)


class TestAclCharacterEntryStateFlagging(TestCase):
    """
    Verify that AclCharacterEntry.objects.flagged() also flags a registered
    character whose owning account's State isn't in the configured allow-list.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.acl = Acl.objects.create(esi_id=1, name="Test ACL")
        cls.member_state = AuthUtils.get_member_state()

        # Owned by a "Member" state account.
        member_character = EveCharacter.objects.create(
            character_id=2001, character_name="Member Guy", corporation_id=2000
        )
        member_user = AuthUtils.create_member("memberguy")
        CharacterOwnership.objects.create(
            character=member_character, owner_hash="member-hash", user=member_user
        )
        cls.member_entry = AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=2001, access=AccessLevel.ALLOWED
        )

        # Owned, but left at the default "Guest" state.
        guest_character = EveCharacter.objects.create(
            character_id=2002, character_name="Guest Guy", corporation_id=2000
        )
        guest_user = User.objects.create_user(username="guestguy")
        CharacterOwnership.objects.create(
            character=guest_character, owner_hash="guest-hash", user=guest_user
        )
        cls.guest_entry = AclCharacterEntry.objects.create(
            acl=cls.acl, character_id=2002, access=AccessLevel.ALLOWED
        )

    def test_no_config_never_flags_on_state_alone(self):
        """With no allowed_states configured, the state check is a no-op"""

        flagged_ids = set(
            AclCharacterEntry.objects.flagged().values_list(
                "character_id", flat=True
            )
        )

        self.assertNotIn(self.member_entry.character_id, flagged_ids)
        self.assertNotIn(self.guest_entry.character_id, flagged_ids)

    def test_flags_registered_character_with_disallowed_state(self):
        """A Guest-state account's character is flagged even though registered"""

        Config.get_solo().allowed_states.add(self.member_state)

        flagged_ids = set(
            AclCharacterEntry.objects.flagged().values_list(
                "character_id", flat=True
            )
        )

        self.assertNotIn(self.member_entry.character_id, flagged_ids)
        self.assertIn(self.guest_entry.character_id, flagged_ids)


class TestAclVisibleTo(TestCase):
    """Tests for Acl.objects.visible_to()"""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.admin_acl = Acl.objects.create(esi_id=1, name="Admin ACL")
        cls.corp_admin_acl = Acl.objects.create(esi_id=2, name="Corp Admin ACL")
        cls.alliance_admin_acl = Acl.objects.create(esi_id=3, name="Alliance Admin ACL")
        cls.manager_only_acl = Acl.objects.create(esi_id=4, name="Manager Only ACL")
        cls.unrelated_acl = Acl.objects.create(esi_id=5, name="Unrelated ACL")

        cls.viewer_character = EveCharacter.objects.create(
            character_id=3001,
            character_name="Viewer Guy",
            corporation_id=4001,
            alliance_id=5001,
        )
        cls.viewer_user = User.objects.create_user(username="viewer")
        CharacterOwnership.objects.create(
            character=cls.viewer_character, owner_hash="viewer-hash", user=cls.viewer_user
        )

        AclCharacterEntry.objects.create(
            acl=cls.admin_acl, character_id=3001, access=AccessLevel.ADMIN
        )
        AclCorporationEntry.objects.create(
            acl=cls.corp_admin_acl, corporation_id=4001, access=AccessLevel.ADMIN
        )
        AclAllianceEntry.objects.create(
            acl=cls.alliance_admin_acl, alliance_id=5001, access=AccessLevel.ADMIN
        )
        AclCharacterEntry.objects.create(
            acl=cls.manager_only_acl, character_id=3001, access=AccessLevel.MANAGER
        )

    def test_shows_only_acls_where_viewer_has_admin_access(self):
        visible_names = set(
            Acl.objects.visible_to(self.viewer_user).values_list("name", flat=True)
        )

        self.assertEqual(
            visible_names,
            {"Admin ACL", "Corp Admin ACL", "Alliance Admin ACL"},
        )

    def test_manager_access_alone_does_not_grant_visibility(self):
        visible_names = set(
            Acl.objects.visible_to(self.viewer_user).values_list("name", flat=True)
        )

        self.assertNotIn("Manager Only ACL", visible_names)

    def test_unrelated_acl_is_never_visible(self):
        visible_names = set(
            Acl.objects.visible_to(self.viewer_user).values_list("name", flat=True)
        )

        self.assertNotIn("Unrelated ACL", visible_names)

    def test_superuser_sees_every_acl(self):
        superuser = User.objects.create_superuser(
            username="root", email="root@example.com", password="password"
        )

        visible_names = set(
            Acl.objects.visible_to(superuser).values_list("name", flat=True)
        )

        self.assertEqual(
            visible_names,
            {
                "Admin ACL",
                "Corp Admin ACL",
                "Alliance Admin ACL",
                "Manager Only ACL",
                "Unrelated ACL",
            },
        )
