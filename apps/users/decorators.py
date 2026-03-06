from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def app_permission_required(permission):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.has_permission(permission):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied(f'Missing permission: {permission}')

        return _wrapped_view

    return decorator
