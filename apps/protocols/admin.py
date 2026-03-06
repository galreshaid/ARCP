"""
Protocol Admin - Final (Clean)
Advanced administration for protocols, sequences, and assignments
"""

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.urls import reverse

from apps.protocols.models import (
    ProtocolTemplate,
    ProtocolSequence,
    ProtocolAssignment,
    ProtocolComment,
)

# ======================================================
# Inline Forms & Validation
# ======================================================

class ProtocolSequenceInlineForm(forms.ModelForm):
    class Meta:
        model = ProtocolSequence
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        errors = {}

        ser = cleaned.get("ser")
        scan_plane = (cleaned.get("scan_plane") or "").strip()
        pulse_sequence = (cleaned.get("pulse_sequence") or "").strip()

        if not ser or ser <= 0:
            errors["ser"] = "SER is required and must be > 0."
        if not scan_plane:
            errors["scan_plane"] = "SCAN PLANE is required."
        if not pulse_sequence:
            errors["pulse_sequence"] = "PULSE SEQ. is required."

        if errors:
            raise ValidationError(errors)

        return cleaned


class ProtocolSequenceInline(admin.TabularInline):
    model = ProtocolSequence
    form = ProtocolSequenceInlineForm
    extra = 0
    ordering = ("ser",)
    fields = (
        "ser",
        "coil",
        "phase_array",
        "scan_plane",
        "pulse_sequence",
        "options",
        "comments",
        "parameters",
    )
    show_change_link = True


class ProtocolAssignmentInline(admin.TabularInline):
    model = ProtocolAssignment
    extra = 0
    readonly_fields = ("exam", "assigned_by", "created_at", "status")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ======================================================
# Protocol Template Admin
# ======================================================

@admin.register(ProtocolTemplate)
class ProtocolTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "modality",
        "procedure",
        "is_active",
        "priority",
        "requires_contrast",
        "usage_badge",
        "assignment_count",
    )
    list_filter = (
        "modality",
        "is_active",
        "requires_contrast",
    )
    search_fields = (
        "code",
        "name",
        "procedure__code",
        "procedure__name",
    )
    ordering = ("modality__code", "priority", "code")
    readonly_fields = ("id", "created_at", "updated_at")

    inlines = [
        ProtocolSequenceInline,
        ProtocolAssignmentInline,
    ]

    fieldsets = (
        ("Identity", {
            "fields": (
                "id",
                "code",
                "name",
                "modality",
                "procedure",
                "body_region",
                "is_active",
                "priority",
            )
        }),
        ("Clinical", {
            "fields": (
                "indications",
                "patient_prep",
                "contraindications",
                "safety_notes",
            )
        }),
        ("Contrast", {
            "fields": (
                "requires_contrast",
                "contrast_type",
                "contrast_phase",
                "contrast_notes",
            ),
            "classes": ("collapse",),
        }),
        ("Technical / Notes", {
            "fields": (
                "post_processing",
                "general_notes",
                "clinical_keywords",
                "metadata",
            ),
            "classes": ("collapse",),
        }),
        ("Usage", {
            "fields": ("usage_count",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    # -------------------------
    # Custom badges
    # -------------------------

    def usage_badge(self, obj):
        if obj.usage_count >= 100:
            color = "green"
        elif obj.usage_count >= 50:
            color = "orange"
        else:
            color = "gray"

        return format_html(
            '<b style="color:{};">{}</b>',
            color,
            obj.usage_count,
        )

    usage_badge.short_description = "Usage"
    usage_badge.admin_order_field = "usage_count"

    def assignment_count(self, obj):
        count = obj.assignments.count()
        if count:
            url = (
                reverse("admin:protocols_protocolassignment_changelist")
                + f"?protocol__id__exact={obj.id}"
            )
            return format_html('<a href="{}">{} assignments</a>', url, count)
        return "0"

    assignment_count.short_description = "Assignments"


# ======================================================
# Protocol Assignment Admin
# ======================================================

class ProtocolCommentInline(admin.TabularInline):
    model = ProtocolComment
    extra = 0
    readonly_fields = ("created_at",)
    fields = ("created_at", "author", "author_role", "message")


@admin.register(ProtocolAssignment)
class ProtocolAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "exam",
        "protocol",
        "assignment_method",
        "status",
        "assigned_by",
        "created_at",
        "hl7_badge",
    )
    list_filter = (
        "assignment_method",
        "status",
        "protocol__modality",
    )
    search_fields = (
        "exam__accession_number",
        "exam__mrn",
        "protocol__code",
        "protocol__name",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "sent_to_ris_at",
        "ris_ack_at",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        ("Core", {
            "fields": (
                "exam",
                "protocol",
                "assigned_by",
                "assignment_method",
                "status",
            )
        }),
        ("Notes", {
            "fields": (
                "radiologist_note",
                "technologist_note",
            )
        }),
        ("Integration", {
            "fields": (
                "sent_to_ris_at",
                "ris_ack_at",
                "metadata",
            ),
            "classes": ("collapse",),
        }),
    )

    inlines = [ProtocolCommentInline]

    def hl7_badge(self, obj):
        if obj.sent_to_ris_at:
            return format_html('<span style="color:green;">✓ Sent</span>')
        return format_html('<span style="color:gray;">Pending</span>')

    hl7_badge.short_description = "RIS / HL7"
