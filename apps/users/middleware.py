from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return self.get_response(request)

        if not getattr(user, "must_change_password", False):
            return self.get_response(request)

        static_url = str(getattr(settings, "STATIC_URL", "") or "")
        media_url = str(getattr(settings, "MEDIA_URL", "") or "")
        if (static_url and request.path.startswith(static_url)) or (
            media_url and request.path.startswith(media_url)
        ):
            return self.get_response(request)

        force_change_path = reverse("user-force-password-change")
        logout_path = reverse("logout")
        if request.path in {force_change_path, logout_path}:
            return self.get_response(request)

        return redirect(force_change_path)
