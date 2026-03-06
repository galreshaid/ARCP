"""
Core Admin - Enhanced
إدارة متقدمة للـ Facilities, Modalities, Procedures, and Exams
"""

from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse

from apps.core.models import (
    ContrastUsage,
    Exam,
    Facility,
    MaterialCatalog,
    MaterialMeasurement,
    MaterialUsage,
    Modality,
    ProcedureMaterialBundle,
    ProcedureMaterialBundleItem,
    Procedure,
)
from apps.qc.models import QCDecision


# ============================================================
# Facility Admin
# ============================================================

@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "hl7_facility_id",
        "is_active",
        "exam_count",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("code", "name", "hl7_facility_id", "address")
    readonly_fields = ("id", "created_at", "updated_at")

    fieldsets = (
        ("Basic Information", {
            "fields": ("id", "code", "name", "is_active")
        }),
        ("HL7 Integration", {
            "fields": ("hl7_facility_id",)
        }),
        ("Contact Information", {
            "fields": ("address", "contact_email", "contact_phone")
        }),
        ("Configuration", {
            "fields": ("config_json",),
            "classes": ("collapse",)
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def exam_count(self, obj):
        count = obj.exams.count()
        url = reverse("admin:core_exam_changelist") + f"?facility__id__exact={obj.id}"
        return format_html('<a href="{}">{} exams</a>', url, count)

    exam_count.short_description = "Exams"


# ============================================================
# Modality Admin
# ============================================================

@admin.register(Modality)
class ModalityAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "requires_qc",
        "requires_contrast",
        "is_active",
        "exam_count",
        "protocol_count",
        "created_at",
    )
    list_filter = ("requires_qc", "requires_contrast", "is_active", "created_at")
    search_fields = ("code", "name", "description")
    readonly_fields = ("id", "created_at", "updated_at")

    fieldsets = (
        ("Basic Information", {
            "fields": ("id", "code", "name", "description", "is_active")
        }),
        ("QC Configuration", {
            "fields": ("requires_qc", "qc_checklist_template")
        }),
        ("Contrast Configuration", {
            "fields": ("requires_contrast",)
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def exam_count(self, obj):
        count = obj.exams.count()
        url = reverse("admin:core_exam_changelist") + f"?modality__id__exact={obj.id}"
        return format_html('<a href="{}">{} exams</a>', url, count)

    exam_count.short_description = "Exams"

    def protocol_count(self, obj):
        # related_name="protocol_templates"
        count = obj.protocol_templates.count()
        url = reverse("admin:protocols_protocoltemplate_changelist") + f"?modality__id__exact={obj.id}"
        return format_html('<a href="{}">{} protocols</a>', url, count)

    protocol_count.short_description = "Protocols"


# ============================================================
# Procedure Admin
# ============================================================

@admin.register(Procedure)
class ProcedureAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "modality",
        "body_region",
        "is_active",
        "created_at",
    )
    list_filter = ("modality", "body_region", "is_active")
    search_fields = ("code", "name")
    ordering = ("modality__code", "code")
    readonly_fields = ("id", "created_at", "updated_at")


# ============================================================
# Exam Admin
# ============================================================

@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = (
        "accession_number",
        "patient_name",
        "mrn",
        "modality",
        "facility",
        "status",
        "exam_datetime",
        "has_protocol",
        "qc_status_badge",
        "contrast_status_badge",
    )

    list_filter = (
        "status",
        "modality",
        "facility",
        "exam_datetime",
        "created_at",
    )

    search_fields = (
        "accession_number",
        "order_id",
        "mrn",
        "patient_name",
        "procedure_name",
        "clinical_history",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "has_protocol",
        "protocol_link",
        "qc_link",
        "contrast_link",
    )

    date_hierarchy = "exam_datetime"

    fieldsets = (
        ("Identifiers", {
            "fields": ("id", "accession_number", "order_id", "mrn")
        }),
        ("Exam Details", {
            "fields": (
                "facility",
                "modality",
                "procedure_code",
                "procedure_name",
                "scheduled_datetime",
                "exam_datetime",
                "status",
            )
        }),
        ("Patient Information", {
            "fields": ("patient_name", "patient_dob", "patient_gender"),
            "description": "Limited per privacy policy",
        }),
        ("Clinical Context", {
            "fields": ("clinical_history", "reason_for_exam")
        }),
        ("Staff", {
            "fields": ("ordering_provider", "technologist")
        }),
        ("HL7 Integration", {
            "fields": ("hl7_message_control_id", "raw_hl7_message"),
            "classes": ("collapse",),
        }),
        ("Related Records", {
            "fields": ("protocol_link", "qc_link", "contrast_link"),
            "classes": ("collapse",),
        }),
        ("Metadata", {
            "fields": ("metadata", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    actions = (
        "mark_as_completed",
        "mark_as_cancelled",
        "generate_deeplinks",
    )

    # -------------------------
    # Display helpers
    # -------------------------

    def has_protocol(self, obj):
        return hasattr(obj, "protocol_assignment")

    has_protocol.boolean = True
    has_protocol.short_description = "Has Protocol"

    def _latest_qc_result(self, obj):
        if not hasattr(obj, "qc_results"):
            return None
        return obj.qc_results.select_related("reviewed_by").order_by("-reviewed_at", "-created_at").first()

    def _latest_qc_session(self, obj):
        if not hasattr(obj, "qc_sessions"):
            return None
        return obj.qc_sessions.select_related("reviewer").order_by("-created_at").first()

    def qc_status_badge(self, obj):
        latest_result = self._latest_qc_result(obj)
        if latest_result:
            if latest_result.decision == QCDecision.APPROVED:
                return format_html('<span style="color:green;">✔ APPROVED</span>')
            return format_html('<span style="color:red;">✘ REJECTED</span>')

        latest_session = self._latest_qc_session(obj)
        if latest_session:
            return format_html('<span style="color:orange;">⚠ DRAFT</span>')

        if hasattr(obj, "qc_evaluation"):
            legacy_qc = obj.qc_evaluation
            if legacy_qc.outcome == "PASS":
                return format_html('<span style="color:green;">✔ PASS</span>')
            if legacy_qc.outcome == "FAIL":
                return format_html('<span style="color:red;">✘ FAIL</span>')
            return format_html('<span style="color:orange;">⚠ CONDITIONAL</span>')
        return format_html('<span style="color:gray;">—</span>')

    qc_status_badge.short_description = "QC"

    def contrast_status_badge(self, obj):
        if hasattr(obj, "contrast_usages") and obj.contrast_usages.exists():
            return format_html('<span style="color:blue;">✔ Documented</span>')
        return format_html('<span style="color:gray;">—</span>')

    contrast_status_badge.short_description = "Contrast"

    def protocol_link(self, obj):
        if hasattr(obj, "protocol_assignment"):
            a = obj.protocol_assignment
            url = reverse("admin:protocols_protocolassignment_change", args=[a.id])
            return format_html('<a href="{}">{} - {}</a>', url, a.protocol.code, a.protocol.name)
        return "No protocol assigned"

    protocol_link.short_description = "Protocol"

    def qc_link(self, obj):
        latest_result = self._latest_qc_result(obj)
        if latest_result:
            url = reverse("admin:qc_qcresult_change", args=[latest_result.id])
            return format_html('<a href="{}">View QC Result</a>', url)

        latest_session = self._latest_qc_session(obj)
        if latest_session:
            url = reverse("admin:qc_qcsession_change", args=[latest_session.id])
            return format_html('<a href="{}">View QC Session</a>', url)

        if hasattr(obj, "qc_evaluation"):
            legacy_qc = obj.qc_evaluation
            url = reverse("admin:qc_qcevaluation_change", args=[legacy_qc.id])
            return format_html('<a href="{}">View QC</a>', url)
        return "No QC"

    qc_link.short_description = "QC"

    def contrast_link(self, obj):
        if hasattr(obj, "contrast_usages"):
            count = obj.contrast_usages.count()
            if count:
                url = reverse("admin:core_contrastusage_changelist") + f"?exam__id__exact={obj.id}"
                return format_html('<a href="{}">View Contrast ({})</a>', url, count)
        return "No Contrast"

    contrast_link.short_description = "Contrast"

    # -------------------------
    # Actions
    # -------------------------

    @admin.action(description="Mark selected exams as COMPLETED")
    def mark_as_completed(self, request, queryset):
        updated = queryset.update(status="COMPLETED")
        self.message_user(request, f"{updated} exams marked as COMPLETED")

    @admin.action(description="Mark selected exams as CANCELLED")
    def mark_as_cancelled(self, request, queryset):
        updated = queryset.update(status="CANCELLED")
        self.message_user(request, f"{updated} exams marked as CANCELLED")

    @admin.action(description="Generate protocol deep links")
    def generate_deeplinks(self, request, queryset):
        from apps.core.deeplinks.generator import deeplink_generator

        for exam in queryset:
            deeplink_generator.generate_protocol_link(
                exam_id=str(exam.id),
                accession_number=exam.accession_number,
                mrn=exam.mrn,
                facility_code=exam.facility.code,
                expiry_hours=72,
            )

        self.message_user(request, f"Generated {queryset.count()} protocol deep links")


# ============================================================
# Admin Branding
# ============================================================

admin.site.site_header = "AAML RadCore Platform Administration"
admin.site.site_title = "AIP Admin"
admin.site.index_title = "Welcome to AAML RadCore Platform"


@admin.register(ContrastUsage)
class ContrastUsageAdmin(admin.ModelAdmin):
    list_display = (
        "exam",
        "pec_number",
        "contrast_name",
        "route",
        "volume_ml",
        "concentration_mg_ml",
        "total_mg",
        "created_at",
    )
    list_filter = ("route", "contrast_name", "created_at")
    search_fields = ("exam__accession_number", "exam__order_id", "pec_number", "contrast_name", "lot_number")
    readonly_fields = ("id", "total_mg", "created_at", "updated_at")


@admin.register(MaterialUsage)
class MaterialUsageAdmin(admin.ModelAdmin):
    list_display = ("exam", "pec_number", "material_name", "measurement", "unit", "quantity", "created_at")
    list_filter = ("measurement", "unit", "material_name", "created_at")
    search_fields = ("exam__accession_number", "exam__order_id", "pec_number", "material_name", "material_item__name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(MaterialMeasurement)
class MaterialMeasurementAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("code", "label")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(MaterialCatalog)
class MaterialCatalogAdmin(admin.ModelAdmin):
    list_display = (
        "material_code",
        "name",
        "category",
        "unit",
        "charge_code",
        "nphies_code",
        "billable",
        "cost_center_only",
        "is_active",
        "created_at",
    )
    list_filter = ("category", "billable", "cost_center_only", "is_active", "created_at")
    search_fields = ("material_code", "name", "charge_code", "nphies_code", "procedure_mapping_tags")
    readonly_fields = ("id", "created_at", "updated_at")


class ProcedureMaterialBundleItemInline(admin.TabularInline):
    model = ProcedureMaterialBundleItem
    extra = 0
    autocomplete_fields = ("material",)


@admin.register(ProcedureMaterialBundle)
class ProcedureMaterialBundleAdmin(admin.ModelAdmin):
    list_display = (
        "procedure_code",
        "procedure_name",
        "modality_scope",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("procedure_code", "procedure_name", "modality_scope")
    readonly_fields = ("id", "created_at", "updated_at")
    autocomplete_fields = ("procedure",)
    inlines = (ProcedureMaterialBundleItemInline,)


@admin.register(ProcedureMaterialBundleItem)
class ProcedureMaterialBundleItemAdmin(admin.ModelAdmin):
    list_display = ("bundle", "material", "material_code", "quantity", "sort_order", "is_optional")
    list_filter = ("is_optional", "created_at")
    search_fields = ("bundle__procedure_code", "material__name", "material_code")
    readonly_fields = ("id", "created_at", "updated_at")
    autocomplete_fields = ("bundle", "material")
