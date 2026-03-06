from django.urls import path

from apps.qc import views

app_name = "qc"

urlpatterns = [
    path("", views.qc_worklist, name="worklist"),
    path("analytics/", views.qc_analytics, name="analytics"),
    path("launch/", views.qc_launch, name="launch"),
    path("review/<uuid:exam_id>/", views.qc_review, name="review"),
    path("deeplink/", views.qc_deeplink_entry, name="deeplink-entry"),
    path("api/exams/", views.qc_exams_api, name="exams-api"),
    path("api/session/<uuid:exam_id>/", views.qc_session_api, name="session-api"),
]
