"""Admin models"""

# Third Party
from solo.admin import SingletonModelAdmin

# Django
from django.contrib import admin

# AA ACL Checker
from acl_checker.models import (
    Acl,
    AclAllianceEntry,
    AclCharacterEntry,
    AclCorporationEntry,
    Config,
    Owner,
)


class AclCharacterEntryInline(admin.TabularInline):
    """Inline listing of an ACL's character entries"""

    model = AclCharacterEntry
    extra = 0
    readonly_fields = ("character_id", "access")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class AclCorporationEntryInline(admin.TabularInline):
    """Inline listing of an ACL's corporation entries"""

    model = AclCorporationEntry
    extra = 0
    readonly_fields = ("corporation_id", "access")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class AclAllianceEntryInline(admin.TabularInline):
    """Inline listing of an ACL's alliance entries"""

    model = AclAllianceEntry
    extra = 0
    readonly_fields = ("alliance_id", "access")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Acl)
class AclAdmin(admin.ModelAdmin):
    """Admin for the Acl model"""

    list_display = (
        "name",
        "esi_id",
        "allow_everyone",
        "is_active",
        "last_synced_at",
    )
    list_filter = ("is_active", "allow_everyone")
    search_fields = ("name", "esi_id")
    readonly_fields = ("last_synced_at",)
    inlines = (
        AclCharacterEntryInline,
        AclCorporationEntryInline,
        AclAllianceEntryInline,
    )


@admin.register(AclCharacterEntry)
class AclCharacterEntryAdmin(admin.ModelAdmin):
    """Admin for the AclCharacterEntry model"""

    list_display = ("character_id", "acl", "access")
    list_filter = ("acl", "access")
    search_fields = ("character_id",)


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    """Admin for the Owner model"""

    list_display = ("character", "is_active", "last_synced_at")
    list_filter = ("is_active",)
    search_fields = ("character__character_name",)
    readonly_fields = ("last_synced_at",)


@admin.register(Config)
class ConfigAdmin(SingletonModelAdmin):
    """Admin for the Config singleton"""

    filter_horizontal = ("allowed_states",)
