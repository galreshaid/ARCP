from django.urls import path

from apps.hl7_core.views import inbound_hl7_http


urlpatterns = [
    path("orm/", inbound_hl7_http, name="hl7-http-orm"),
]
