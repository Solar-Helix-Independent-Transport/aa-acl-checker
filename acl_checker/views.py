"""App Views"""

# Django
from django.contrib import messages
from django.contrib.auth.decorators import (
    login_required,
    permission_required,
    user_passes_test,
)
from django.core.handlers.wsgi import WSGIRequest
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter
from esi.decorators import token_required

# AA ACL Checker
from acl_checker.models import Acl, AclCharacterEntry, Owner
from acl_checker.tasks import sync_owner_acls


def _can_view_or_manage(user) -> bool:
    """Anyone who can either view flagged characters or link a token"""

    return user.has_perm("acl_checker.basic_access") or user.has_perm(
        "acl_checker.add_owner"
    )


@login_required
@user_passes_test(_can_view_or_manage)
def index(request: WSGIRequest) -> HttpResponse:
    """List characters granted access on a tracked ACL but not registered in Auth

    Viewing the flagged list and linking a character are independent
    permissions - someone with only `add_owner` still lands here (to reach
    the "Add / Refresh Character" button) but sees no flagged rows, and
    someone with only `basic_access` sees the flagged rows but no button.
    """

    can_view_flagged = request.user.has_perm("acl_checker.basic_access")
    rows = None
    acl_rows = None

    if can_view_flagged:
        # A viewer only ever sees ACLs they themselves have Admin access on
        # (directly, or via their character's corp/alliance) - not every
        # tracked ACL. Superusers see everything.
        visible_acl_ids = list(
            Acl.objects.visible_to(request.user)
            .filter(is_active=True)
            .values_list("pk", flat=True)
        )

        flagged_entries = list(
            AclCharacterEntry.objects.flagged()
            .filter(acl_id__in=visible_acl_ids)
            .select_related("acl")
            .order_by("acl__name", "character_id")
        )

        character_names = dict(
            EveCharacter.objects.filter(
                character_id__in=[entry.character_id for entry in flagged_entries]
            ).values_list("character_id", "character_name")
        )

        rows = [
            {
                "acl": entry.acl,
                "character_id": entry.character_id,
                "character_name": character_names.get(entry.character_id, ""),
                "access": entry.access,
                "reason": (
                    _("Not registered")
                    if not entry.is_registered
                    else _("State not allowed (%(state)s)")
                    % {"state": entry.owner_state_name or _("Unknown")}
                ),
            }
            for entry in flagged_entries
        ]

        flagged_counts = dict(
            AclCharacterEntry.objects.flagged()
            .filter(acl_id__in=visible_acl_ids)
            .values("acl_id")
            .annotate(count=Count("id"))
            .values_list("acl_id", "count")
        )

        acls = (
            Acl.objects.filter(pk__in=visible_acl_ids)
            .annotate(
                character_count=Count("character_entries", distinct=True),
                corporation_count=Count("corporation_entries", distinct=True),
                alliance_count=Count("alliance_entries", distinct=True),
            )
            .order_by("name")
        )

        acl_rows = [
            {
                "acl": acl,
                "character_count": acl.character_count,
                "corporation_count": acl.corporation_count,
                "alliance_count": acl.alliance_count,
                "flagged_count": flagged_counts.get(acl.pk, 0),
            }
            for acl in acls
        ]

    context = {
        "can_view_flagged": can_view_flagged,
        "rows": rows,
        "acl_rows": acl_rows,
    }

    return render(request, "acl_checker/index.html", context)


@login_required
@permission_required("acl_checker.add_owner")
@token_required(scopes=Owner.get_esi_scopes())
def add_owner(request: WSGIRequest, token) -> HttpResponse:
    """Link a character's managed/administered Access Lists for tracking"""

    character = EveCharacter.objects.get_character_by_id(
        token.character_id
    ) or EveCharacter.objects.create_character(token.character_id)

    owner, created = Owner.objects.get_or_create(
        character=character, defaults={"is_active": True}
    )
    if not created and not owner.is_active:
        owner.is_active = True
        owner.save(update_fields=["is_active"])

    sync_owner_acls.delay(owner.pk)

    messages.success(
        request,
        _("Linked %(character)s for Access List tracking.")
        % {"character": character},
    )

    return redirect("acl_checker:index")
