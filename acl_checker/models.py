"""
App Models
"""

# Third Party
from solo.models import SingletonModel

# Django
from django.db import models
from django.db.models import Exists, OuterRef, Q, Subquery, Value
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.authentication.models import State
from allianceauth.eveonline.models import EveCharacter


class General(models.Model):
    """Meta model for app permissions"""

    class Meta:
        """Meta definitions"""

        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access this app"),
            ("add_owner", "Can link a character's Access Lists for tracking"),
        )


class Config(SingletonModel):
    """App-wide configuration, editable from the Django admin."""

    allowed_states = models.ManyToManyField(
        State,
        blank=True,
        help_text=_(
            "States a registered character's account may be in without being "
            "flagged. Leave empty to skip this check entirely (a registered "
            "character is never flagged purely for its account's state)."
        ),
    )

    class Meta:
        """Meta definitions"""

        default_permissions = ()

    def __str__(self) -> str:
        return "ACL Checker Configuration"


class Owner(models.Model):
    """A character whose managed/administered Access Lists are tracked by this app."""

    character = models.OneToOneField(
        EveCharacter, on_delete=models.CASCADE, related_name="acl_checker_owner"
    )
    is_active = models.BooleanField(
        default=True, help_text=_("Disabled owners are skipped by the sync task.")
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Meta definitions"""

        default_permissions = ()

    def __str__(self) -> str:
        return str(self.character)

    @classmethod
    def get_esi_scopes(cls) -> list[str]:
        """ESI scopes required to list and read this owner's Access Lists."""

        return ["esi-access.read_lists.v1"]


class AccessLevel(models.TextChoices):
    """Matches the `access` enum returned by ESI for Access List entries."""

    UNSPECIFIED = "Unspecified", _("Unspecified")
    ALLOWED = "Allowed", _("Allowed")
    BLOCKED = "Blocked", _("Blocked")
    MANAGER = "Manager", _("Manager")
    ADMIN = "Admin", _("Admin")


# Access levels that ESI still counts as blocked from the ACL and are
# therefore never worth flagging, even if the character is unregistered.
NON_GRANTING_ACCESS_LEVELS = (AccessLevel.BLOCKED, AccessLevel.UNSPECIFIED)


class AclQuerySet(models.QuerySet):
    """Custom queries for Acl"""

    def visible_to(self, user):
        """ACLs the user has Admin access on - directly as a character, or
        via their character's current corporation/alliance being granted
        Admin. Superusers see every ACL regardless.
        """

        if user.is_superuser:
            return self

        owned_characters = EveCharacter.objects.filter(
            character_ownership__user=user
        )
        character_ids = owned_characters.values_list("character_id", flat=True)
        corporation_ids = owned_characters.values_list("corporation_id", flat=True)
        alliance_ids = owned_characters.exclude(
            alliance_id__isnull=True
        ).values_list("alliance_id", flat=True)

        return self.filter(
            Q(
                character_entries__character_id__in=character_ids,
                character_entries__access=AccessLevel.ADMIN,
            )
            | Q(
                corporation_entries__corporation_id__in=corporation_ids,
                corporation_entries__access=AccessLevel.ADMIN,
            )
            | Q(
                alliance_entries__alliance_id__in=alliance_ids,
                alliance_entries__access=AccessLevel.ADMIN,
            )
        ).distinct()


class Acl(models.Model):
    """An Access Control List, as pulled from ESI."""

    esi_id = models.BigIntegerField(
        unique=True, help_text=_("The Access List's ID from ESI.")
    )
    name = models.CharField(max_length=255, help_text=_("The Access List's name."))
    description = models.TextField(blank=True)
    allow_everyone = models.BooleanField(
        default=False,
        help_text=_("Whether everyone is allowed on this ACL unless blocked."),
    )
    is_active = models.BooleanField(
        default=True, help_text=_("Whether this ACL should be kept in sync.")
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    objects = AclQuerySet.as_manager()

    class Meta:
        """Meta definitions"""

        default_permissions = ()
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class AclCharacterEntryQuerySet(models.QuerySet):
    """Custom queries for AclCharacterEntry"""

    def granted(self):
        """Entries that actually grant access, i.e. not Blocked/Unspecified"""

        return self.exclude(access__in=NON_GRANTING_ACCESS_LEVELS)

    def annotate_registered(self):
        """Annotate each entry with whether it is an owned character in Auth"""

        owned_characters = EveCharacter.objects.filter(
            character_id=OuterRef("character_id"),
            character_ownership__isnull=False,
        )

        return self.annotate(is_registered=Exists(owned_characters))

    def annotate_owner_state(self):
        """Annotate each entry with its owning user's account State (if any) and
        whether that State is in the configured allow-list.

        `state_allowed` is forced to True when no allowed_states are
        configured - the state check is opt-in, so an unconfigured site
        never flags a registered character purely for its account's state.
        """

        owner_state_name = EveCharacter.objects.filter(
            character_id=OuterRef("character_id")
        ).values("character_ownership__user__profile__state__name")[:1]

        qs = self.annotate(owner_state_name=Subquery(owner_state_name))

        allowed_state_ids = list(
            Config.get_solo().allowed_states.values_list("pk", flat=True)
        )
        if not allowed_state_ids:
            return qs.annotate(
                state_allowed=Value(True, output_field=models.BooleanField())
            )

        owned_with_allowed_state = EveCharacter.objects.filter(
            character_id=OuterRef("character_id"),
            character_ownership__user__profile__state_id__in=allowed_state_ids,
        )

        return qs.annotate(state_allowed=Exists(owned_with_allowed_state))

    def flagged(self):
        """Granted entries that are either unregistered, or registered under
        an account State outside the configured allow-list.
        """

        return (
            self.granted()
            .annotate_registered()
            .annotate_owner_state()
            .filter(Q(is_registered=False) | Q(state_allowed=False))
        )


class AclCharacterEntry(models.Model):
    """A single character entry on an ACL, as last seen from ESI.

    ESI's Access List detail only ever reports a character_id and access
    level per entry - never a name - so this deliberately has no cached
    name field; resolve display names from EveCharacter (or ESI) at read time.
    """

    acl = models.ForeignKey(
        Acl, on_delete=models.CASCADE, related_name="character_entries"
    )
    character_id = models.PositiveIntegerField()
    access = models.CharField(max_length=20, choices=AccessLevel)

    objects = AclCharacterEntryQuerySet.as_manager()

    class Meta:
        """Meta definitions"""

        default_permissions = ()
        unique_together = ("acl", "character_id")
        ordering = ["character_id"]

    def __str__(self) -> str:
        return f"{self.character_id} ({self.acl.name})"


class AclCorporationEntry(models.Model):
    """A single corporation entry on an ACL, as last seen from ESI."""

    acl = models.ForeignKey(
        Acl, on_delete=models.CASCADE, related_name="corporation_entries"
    )
    corporation_id = models.PositiveIntegerField()
    access = models.CharField(max_length=20, choices=AccessLevel)

    class Meta:
        """Meta definitions"""

        default_permissions = ()
        unique_together = ("acl", "corporation_id")
        ordering = ["corporation_id"]

    def __str__(self) -> str:
        return f"{self.corporation_id} ({self.acl.name})"


class AclAllianceEntry(models.Model):
    """A single alliance entry on an ACL, as last seen from ESI."""

    acl = models.ForeignKey(
        Acl, on_delete=models.CASCADE, related_name="alliance_entries"
    )
    alliance_id = models.PositiveIntegerField()
    access = models.CharField(max_length=20, choices=AccessLevel)

    class Meta:
        """Meta definitions"""

        default_permissions = ()
        unique_together = ("acl", "alliance_id")
        ordering = ["alliance_id"]

    def __str__(self) -> str:
        return f"{self.alliance_id} ({self.acl.name})"
