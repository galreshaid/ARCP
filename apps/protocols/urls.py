"""
Protocol URLs
API + UI endpoints for protocol management
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.protocols import views

app_name = "protocols"

# ======================================================
# DRF Router (API)
# ======================================================
router = DefaultRouter()
router.register(r"templates", views.ProtocolTemplateViewSet, basename="template")
router.register(r"assignments", views.ProtocolAssignmentViewSet, basename="assignment")
router.register(r"suggestions", views.ProtocolSuggestionViewSet, basename="suggestion")
# ⚠️ preferences endpoint DISABLED (SAFE MODE)
# router.register(r"preferences", views.RadiologistPreferenceViewSet, basename="preference")

urlpatterns = [
    # ==================================================
    # API Endpoints (DRF)
    # ==================================================
    path("", include(router.urls)),

    # ==================================================
    # Deep Link
    # ==================================================
    path(
        "deeplink/",
        views.ProtocolDeepLinkView.as_view(),
        name="protocol-deeplink",
    ),

    # ==================================================
    # UI Views
    # ==================================================
    path(
        "radiologist/assign/<uuid:exam_id>/",
        views.radiologist_assign,
        name="radiologist_assign",
    ),
    path(
        "technologist/view/<uuid:exam_id>/",
        views.technologist_view,
        name="technologist_view",
    ),
    path(
        "technologist/print/<uuid:exam_id>/",
        views.technologist_print_protocol,
        name="technologist_print",
    ),
]
