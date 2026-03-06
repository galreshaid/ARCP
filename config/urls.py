"""
Main URLs Configuration
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

from apps.core.views import (
    home,
    health_check,
    worklist_page,        # Protocoling worklist
    protocoling_page,     # Radiologist assignment page
    contrast_materials_analytics_page,
    contrast_materials_analytics_export_csv,
    contrast_materials_exams_api,
    contrast_materials_page,
    contrast_materials_review_page,
    contrast_materials_saved_entry_update_api,
    contrast_materials_session_api,
    system_admin_page,
    system_admin_resource_list,
    system_admin_resource_create,
    system_admin_resource_update,
    system_admin_hl7_message_detail,
    exams_api,
    mark_exam_protocol_not_required,
)
from apps.qc.views import qc_worklist
from apps.protocols.views import (
    radiologist_assign as radiologist_review_page,
    technologist_view as technologist_protocol_page,
    technologist_print_protocol as technologist_print_page,
)

urlpatterns = [
    # ======================================================
    # Core / UI
    # ======================================================
    path("", include("apps.users.urls")),
    path("", home, name="home"),
    path("health/", health_check, name="health-check"),

    # ======================================================
    # Protocoling UI
    # ======================================================
    path(
        "protocoling/",
        worklist_page,
        name="protocoling-worklist",
    ),
    path(
        "protocoling/assign/",
        protocoling_page,
        name="protocoling-assign",
    ),
    path(
        "quality-control/",
        qc_worklist,
        name="quality-control",
    ),
    path(
        "quality-control/",
        include(("apps.qc.urls", "qc"), namespace="qc"),
    ),
    path(
        "contrast-materials/",
        contrast_materials_page,
        name="contrast-materials",
    ),
    path(
        "contrast-materials/analytics/",
        contrast_materials_analytics_page,
        name="contrast-materials-analytics",
    ),
    path(
        "contrast-materials/analytics/export.csv",
        contrast_materials_analytics_export_csv,
        name="contrast-materials-analytics-export",
    ),
    path(
        "contrast-materials/review/<uuid:exam_id>/",
        contrast_materials_review_page,
        name="contrast-materials-review",
    ),
    path(
        "contrast-materials/api/exams/",
        contrast_materials_exams_api,
        name="contrast-materials-api-exams",
    ),
    path(
        "contrast-materials/api/session/<uuid:exam_id>/",
        contrast_materials_session_api,
        name="contrast-materials-api-session",
    ),
    path(
        "contrast-materials/api/entry/<uuid:exam_id>/",
        contrast_materials_saved_entry_update_api,
        name="contrast-materials-api-entry-update",
    ),
    path(
        "protocoling/review/<uuid:exam_id>/",
        radiologist_review_page,
        name="protocoling-radiologist-review",
    ),
    path(
        "protocoling/technologist/<uuid:exam_id>/",
        technologist_protocol_page,
        name="protocoling-technologist-view",
    ),
    path(
        "protocoling/technologist/<uuid:exam_id>/print/",
        technologist_print_page,
        name="protocoling-technologist-print",
    ),
    path(
        "system-admin/",
        system_admin_page,
        name="system-admin",
    ),
    path(
        "system-admin/<str:resource_key>/",
        system_admin_resource_list,
        name="system-admin-resource-list",
    ),
    path(
        "system-admin/<str:resource_key>/new/",
        system_admin_resource_create,
        name="system-admin-resource-create",
    ),
    path(
        "system-admin/<str:resource_key>/<str:object_id>/edit/",
        system_admin_resource_update,
        name="system-admin-resource-update",
    ),
    path(
        "system-admin/hl7-messages/<uuid:object_id>/view/",
        system_admin_hl7_message_detail,
        name="system-admin-hl7-message-detail",
    ),

    # ======================================================
    # Admin
    # ======================================================
    path("admin/", admin.site.urls),

    # ======================================================
    # APIs
    # ======================================================
    path("integration/api/hl7/", include("apps.hl7_core.urls")),
    path("api/core/exams/", exams_api, name="exams-api"),
    path(
        "api/core/exams/<uuid:exam_id>/mark-not-required/",
        mark_exam_protocol_not_required,
        name="exam-mark-not-required",
    ),
    path("api/protocols/", include("apps.protocols.urls")),

    # ======================================================
    # API Schema / Docs
    # ======================================================
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/schema/swagger/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]

# ==========================================================
# Static & Media (Development Only)
# ==========================================================
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=settings.STATIC_ROOT,
    )
