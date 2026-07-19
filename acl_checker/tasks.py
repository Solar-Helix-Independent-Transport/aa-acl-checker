"""App Tasks"""

# Standard Library
import logging

# Third Party
from celery import shared_task

# Django
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import (
    EveAllianceInfo,
    EveCharacter,
    EveCorporationInfo,
)
from esi.exceptions import HTTPClientError, HTTPNotModified
from esi.models import Token

# AA ACL Checker
from acl_checker.models import (
    Acl,
    AclAllianceEntry,
    AclCharacterEntry,
    AclCorporationEntry,
    Owner,
)
from acl_checker.providers import esi

logger = logging.getLogger(__name__)


def _ensure_character_cached(character_id: int) -> None:
    """Cache a character's public ESI info locally so its name can be displayed.

    ESI's Access List detail never includes names, and characters granted
    access but never registered in Auth (i.e. exactly the ones we care about
    showing) have no local EveCharacter record to resolve a name from
    otherwise.
    """

    if EveCharacter.objects.get_character_by_id(character_id):
        return

    try:
        EveCharacter.objects.create_character(character_id)
    except HTTPClientError:
        logger.warning("Could not resolve character %s via ESI", character_id)


def _ensure_corporation_cached(corporation_id: int) -> None:
    """Cache a corporation's public ESI info locally so its name can be displayed."""

    try:
        EveCorporationInfo.objects.get_or_create_esi(corporation_id)
    except HTTPClientError:
        logger.warning("Could not resolve corporation %s via ESI", corporation_id)


def _ensure_alliance_cached(alliance_id: int) -> None:
    """Cache an alliance's public ESI info locally so its name can be displayed."""

    try:
        EveAllianceInfo.objects.get_or_create_esi(alliance_id)
    except HTTPClientError:
        logger.warning("Could not resolve alliance %s via ESI", alliance_id)


@shared_task
def update_all_acls(force: bool = False) -> None:
    """Queue a sync for every active tracked owner"""

    for owner_pk in Owner.objects.filter(is_active=True).values_list("pk", flat=True):
        sync_owner_acls.delay(owner_pk, force)


@shared_task
def sync_owner_acls(owner_pk: int, force: bool = False) -> None:
    """Sync every Access List a single owner character manages or administers"""

    owner = Owner.objects.get(pk=owner_pk)
    character_id = owner.character.character_id

    token = (
        Token.objects.filter(character_id=character_id)
        .require_scopes(Owner.get_esi_scopes())
        .require_valid()
        .first()
    )

    if token is None:
        logger.warning("No valid Access List token found for %s", owner.character)
        return

    # The listing's ETag only reflects *which* Access Lists this character
    # manages, not their membership - an in-game membership change never
    # changes it. Always bypass its cache so a 304 there can never skip the
    # per-ACL detail sync below (which has its own, membership-aware ETag).
    listing = esi.client.Access_List.GetCharactersAccessListsListing(
        character_id=character_id, token=token
    ).result(force_refresh=True)

    for access_list in listing.access_lists:
        _sync_acl(access_list.id, token, force=force)

    owner.last_synced_at = timezone.now()
    owner.save(update_fields=["last_synced_at"])


def _sync_acl(esi_id: int, token: Token, force: bool = False) -> None:
    """Fetch a single Access List's detail and replace its stored membership"""

    try:
        detail = esi.client.Access_List.GetCharactersAccessListsDetail(
            character_id=token.character_id, access_list_id=esi_id, token=token
        ).result(force_refresh=force)
    except HTTPNotModified:
        logger.debug("Access List %s unchanged", esi_id)
        return

    acl, _created = Acl.objects.update_or_create(
        esi_id=detail.id,
        defaults={
            "name": detail.name,
            "description": detail.description,
            "allow_everyone": detail.membership.allow_everyone,
            "is_active": True,
            "last_synced_at": timezone.now(),
        },
    )

    # ESI's detail response is a full snapshot of membership, so the
    # simplest correct sync is to replace every entry rather than diff it.
    acl.character_entries.all().delete()
    AclCharacterEntry.objects.bulk_create(
        AclCharacterEntry(
            acl=acl, character_id=entry.character_id, access=entry.access
        )
        for entry in detail.membership.characters
    )
    for entry in detail.membership.characters:
        _ensure_character_cached(entry.character_id)

    acl.corporation_entries.all().delete()
    AclCorporationEntry.objects.bulk_create(
        AclCorporationEntry(
            acl=acl, corporation_id=entry.corporation_id, access=entry.access
        )
        for entry in detail.membership.corporations
    )
    for entry in detail.membership.corporations:
        _ensure_corporation_cached(entry.corporation_id)

    acl.alliance_entries.all().delete()
    AclAllianceEntry.objects.bulk_create(
        AclAllianceEntry(acl=acl, alliance_id=entry.alliance_id, access=entry.access)
        for entry in detail.membership.alliances
    )
    for entry in detail.membership.alliances:
        _ensure_alliance_cached(entry.alliance_id)
