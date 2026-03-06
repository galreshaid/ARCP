from django.urls import path

from apps.users.views import (
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
    path('inbox/', inbox_view, name='user-inbox'),
    path('inbox/read-all/', notification_mark_all_read_view, name='user-inbox-read-all'),
    path('inbox/send/', notification_send_view, name='user-notification-send'),
    path('inbox/<uuid:notification_id>/delete/', notification_delete_view, name='user-notification-delete'),
    path('inbox/<uuid:notification_id>/open/', notification_open_view, name='user-notification-open'),
    path('inbox/<uuid:notification_id>/read/', notification_mark_read_view, name='user-notification-read'),
]
