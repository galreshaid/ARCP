from django.contrib import admin

from apps.qc.models import (
    QCAnnotation,
    QCChecklist,
    QCImage,
    QCResult,
    QCSession,
)


class QCAnnotationInline(admin.TabularInline):
    model = QCAnnotation
    extra = 0
    readonly_fields = ("created_at", "updated_at")


@admin.register(QCImage)
class QCImageAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "accession_number",
        "capture_order",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("accession_number", "session__exam__accession_number")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [QCAnnotationInline]


class QCImageInline(admin.TabularInline):
    model = QCImage
    extra = 0
    readonly_fields = ("created_at", "updated_at")


@admin.register(QCSession)
class QCSessionAdmin(admin.ModelAdmin):
    list_display = (
        "accession_number",
        "mrn",
        "modality_code",
        "concern_raised",
        "status",
        "reviewer",
        "created_at",
    )
    list_filter = ("status", "concern_raised", "modality_code", "created_at")
    search_fields = ("accession_number", "mrn", "exam__order_id")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [QCImageInline]


@admin.register(QCChecklist)
class QCChecklistAdmin(admin.ModelAdmin):
    list_display = (
        "modality",
        "key",
        "label",
        "is_required",
        "is_active",
        "sort_order",
    )
    list_filter = ("modality", "is_required", "is_active")
    search_fields = ("modality__code", "key", "label")
    ordering = ("modality__code", "sort_order", "key")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(QCAnnotation)
class QCAnnotationAdmin(admin.ModelAdmin):
    list_display = ("image", "tool", "created_by", "created_at")
    list_filter = ("tool", "created_at")
    search_fields = ("image__accession_number", "text_note")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(QCResult)
class QCResultAdmin(admin.ModelAdmin):
    list_display = ("exam", "decision", "reviewed_by", "reviewed_at")
    list_filter = ("decision", "reviewed_at")
    search_fields = ("exam__accession_number", "exam__mrn")
    readonly_fields = ("id", "created_at", "updated_at")
