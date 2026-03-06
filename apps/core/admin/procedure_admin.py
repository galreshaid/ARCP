from django.contrib import admin
from apps.core.models import Procedure


@admin.register(Procedure)
class ProcedureAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "modality",
        "body_region",
        "is_active",
    )

    list_filter = (
        "modality",
        "body_region",
        "is_active",
    )

    search_fields = (
        "code",
        "name",
    )

    ordering = ("modality__code", "body_region", "code")

    fieldsets = (
        ("Procedure Info", {
            "fields": ("code", "name")
        }),
        ("Classification", {
            "fields": ("modality", "body_region")
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
    )
