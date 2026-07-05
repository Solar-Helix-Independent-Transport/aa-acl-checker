"""Tests for the acl_checker sync tasks"""

# Standard Library
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.test import TestCase

# Alliance Auth
from allianceauth.eveonline.models import (
    EveAllianceInfo,
    EveCharacter,
    EveCorporationInfo,
)
from esi.exceptions import HTTPClientError
from esi.models import Scope, Token

# AA ACL Checker
from acl_checker.models import (
    Acl,
    AclAllianceEntry,
    AclCharacterEntry,
    AclCorporationEntry,
    Owner,
)
from acl_checker.tasks import sync_owner_acls


def _create_token(*, character_id: int, character_name: str, scope_name: str) -> Token:
    """Create a freshly-issued (therefore non-expired) Token with a given scope."""

    EveCharacter.objects.get_or_create(
        character_id=character_id,
        defaults={"character_name": character_name, "corporation_id": 1000000},
    )

    scope, _created = Scope.objects.get_or_create(
        name=scope_name, defaults={"help_text": ""}
    )
    token = Token.objects.create(
        character_id=character_id,
        character_name=character_name,
        character_owner_hash=f"hash-{character_id}",
        access_token="access-token",
        refresh_token="refresh-token",
    )
    token.scopes.add(scope)

    return token


def _make_acl_detail(acl_id: int) -> SimpleNamespace:
    """Build a fake ESI CharactersAccessListsDetail-shaped object"""

    return SimpleNamespace(
        id=acl_id,
        name="Test ACL",
        description="A test ACL",
        membership=SimpleNamespace(
            allow_everyone=False,
            characters=[SimpleNamespace(character_id=2001, access="Allowed")],
            corporations=[SimpleNamespace(corporation_id=3001, access="Allowed")],
            alliances=[SimpleNamespace(alliance_id=4001, access="Blocked")],
        ),
    )


class TestSyncOwnerAcls(TestCase):
    """Tests for tasks.sync_owner_acls"""

    @classmethod
    def setUpTestData(cls):
        cls.manager_character = EveCharacter.objects.create(
            character_id=1001, character_name="Manager Guy", corporation_id=2000
        )
        cls.owner = Owner.objects.create(character=cls.manager_character)
        _create_token(
            character_id=cls.manager_character.character_id,
            character_name=cls.manager_character.character_name,
            scope_name="esi-access.read_lists.v1",
        )

    def setUp(self):
        # Name-resolution for ACL entries hits ESI via these manager
        # methods - stub them out so tests don't make real ESI calls.
        self.mock_create_character = self.enterContext(
            patch("acl_checker.tasks.EveCharacter.objects.create_character")
        )
        self.mock_get_or_create_corp = self.enterContext(
            patch("acl_checker.tasks.EveCorporationInfo.objects.get_or_create_esi")
        )
        self.mock_get_or_create_alliance = self.enterContext(
            patch("acl_checker.tasks.EveAllianceInfo.objects.get_or_create_esi")
        )

    @patch("acl_checker.tasks.esi")
    def test_syncs_acl_and_its_entries(self, mock_esi):
        acl_id = 5001
        listing = SimpleNamespace(access_lists=[SimpleNamespace(id=acl_id)])
        detail = _make_acl_detail(acl_id)

        mock_esi.client.Access_List.GetCharactersAccessListsListing.return_value.result.return_value = (
            listing
        )
        mock_esi.client.Access_List.GetCharactersAccessListsDetail.return_value.result.return_value = (
            detail
        )

        sync_owner_acls(self.owner.pk)

        acl = Acl.objects.get(esi_id=acl_id)
        self.assertEqual(acl.name, "Test ACL")
        self.assertFalse(acl.allow_everyone)

        self.assertTrue(
            AclCharacterEntry.objects.filter(
                acl=acl, character_id=2001, access="Allowed"
            ).exists()
        )
        self.assertTrue(
            AclCorporationEntry.objects.filter(
                acl=acl, corporation_id=3001, access="Allowed"
            ).exists()
        )
        self.assertTrue(
            AclAllianceEntry.objects.filter(
                acl=acl, alliance_id=4001, access="Blocked"
            ).exists()
        )

        self.owner.refresh_from_db()
        self.assertIsNotNone(self.owner.last_synced_at)

    @patch("acl_checker.tasks.esi")
    def test_replaces_stale_entries_on_resync(self, mock_esi):
        """A member removed from the ACL upstream must disappear locally too"""

        acl_id = 5002
        listing = SimpleNamespace(access_lists=[SimpleNamespace(id=acl_id)])

        mock_esi.client.Access_List.GetCharactersAccessListsListing.return_value.result.return_value = (
            listing
        )
        mock_esi.client.Access_List.GetCharactersAccessListsDetail.return_value.result.return_value = (
            _make_acl_detail(acl_id)
        )
        sync_owner_acls(self.owner.pk)

        acl = Acl.objects.get(esi_id=acl_id)
        self.assertTrue(
            AclCharacterEntry.objects.filter(acl=acl, character_id=2001).exists()
        )

        empty_detail = SimpleNamespace(
            id=acl_id,
            name="Test ACL",
            description="",
            membership=SimpleNamespace(
                allow_everyone=False, characters=[], corporations=[], alliances=[]
            ),
        )
        mock_esi.client.Access_List.GetCharactersAccessListsDetail.return_value.result.return_value = (
            empty_detail
        )
        sync_owner_acls(self.owner.pk)

        self.assertFalse(
            AclCharacterEntry.objects.filter(acl=acl, character_id=2001).exists()
        )

    @patch("acl_checker.tasks.esi")
    def test_no_token_skips_sync_without_error(self, mock_esi):
        Token.objects.all().delete()

        sync_owner_acls(self.owner.pk)

        self.assertFalse(Acl.objects.exists())
        mock_esi.client.Access_List.GetCharactersAccessListsListing.assert_not_called()

    @patch("acl_checker.tasks.esi")
    def test_caches_names_for_new_entries(self, mock_esi):
        """A character/corp/alliance new to Auth must be looked up via ESI
        so its name is available, rather than only ever showing a raw ID.
        """

        acl_id = 5003
        listing = SimpleNamespace(access_lists=[SimpleNamespace(id=acl_id)])
        mock_esi.client.Access_List.GetCharactersAccessListsListing.return_value.result.return_value = (
            listing
        )
        mock_esi.client.Access_List.GetCharactersAccessListsDetail.return_value.result.return_value = (
            _make_acl_detail(acl_id)
        )

        sync_owner_acls(self.owner.pk)

        self.mock_create_character.assert_called_once_with(2001)
        self.mock_get_or_create_corp.assert_called_once_with(3001)
        self.mock_get_or_create_alliance.assert_called_once_with(4001)

    @patch("acl_checker.tasks.esi")
    def test_skips_already_cached_character(self, mock_esi):
        """A character already known to Auth must not trigger a redundant ESI lookup"""

        EveCharacter.objects.create(
            character_id=2001, character_name="Already Known", corporation_id=2000
        )

        acl_id = 5004
        listing = SimpleNamespace(access_lists=[SimpleNamespace(id=acl_id)])
        mock_esi.client.Access_List.GetCharactersAccessListsListing.return_value.result.return_value = (
            listing
        )
        mock_esi.client.Access_List.GetCharactersAccessListsDetail.return_value.result.return_value = (
            _make_acl_detail(acl_id)
        )

        sync_owner_acls(self.owner.pk)

        self.mock_create_character.assert_not_called()

    @patch("acl_checker.tasks.esi")
    def test_name_lookup_failure_does_not_break_sync(self, mock_esi):
        """A character ESI can't resolve (e.g. deleted) must not abort the whole sync"""

        self.mock_create_character.side_effect = HTTPClientError(
            status_code=404, headers={}, data=None
        )

        acl_id = 5005
        listing = SimpleNamespace(access_lists=[SimpleNamespace(id=acl_id)])
        mock_esi.client.Access_List.GetCharactersAccessListsListing.return_value.result.return_value = (
            listing
        )
        mock_esi.client.Access_List.GetCharactersAccessListsDetail.return_value.result.return_value = (
            _make_acl_detail(acl_id)
        )

        sync_owner_acls(self.owner.pk)

        acl = Acl.objects.get(esi_id=acl_id)
        self.assertTrue(
            AclCharacterEntry.objects.filter(acl=acl, character_id=2001).exists()
        )
