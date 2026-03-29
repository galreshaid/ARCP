from django.urls import path

from apps.users.views import (
    force_password_change_view,
    UserLoginView,
    UserLogoutView,
    notification_delete_view,
    inbox_view,
    notification_mark_all_read_view,
    notification_mark_read_view,
    notification_open_view,
    notification_send_view,
)


urlpatterns = [
    path('login/', UserLoginView.as_view(), name='login'),
    path('logout/', UserLogoutView.as_view(), name='logout'),
    path('password/change-required/', force_password_change_view, name='user-force-password-change'),
    path('inbox/', inbox_view, name='user-inbox'),
    path('inbox/read-all/', notification_mark_all_read_view, name='user-inbox-read-all'),
    path('inbox/send/', notification_send_view, name='user-notification-send'),
    path('inbox/<uuid:notification_id>/delete/', notification_delete_view, name='user-notification-delete'),
    path('inbox/<uuid:notification_id>/open/', notification_open_view, name='user-notification-open'),
    path('inbox/<uuid:notification_id>/read/', notification_mark_read_view, name='user-notification-read'),
]
