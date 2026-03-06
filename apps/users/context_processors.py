from django.contrib.auth import get_user_model
from django.db.utils import OperationalError, ProgrammingError

from apps.users.models import UserNotification


def inbox_context(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {
            'user_inbox_unread_count': 0,
            'user_inbox_items': [],
            'user_inbox_available': False,
            'user_message_recipients': [],
        }

    user_model = get_user_model()
    message_recipients = list(
        user_model.objects.filter(
            is_active=True,
        ).exclude(pk=request.user.pk).order_by('first_name', 'last_name', 'username')
    )

    try:
        recent_notifications = list(
            UserNotification.objects.filter(
                recipient=request.user,
            ).select_related('sender')[:6]
        )
        unread_count = UserNotification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).count()
        inbox_available = True
    except (OperationalError, ProgrammingError):
        recent_notifications = []
        unread_count = 0
        inbox_available = False

    return {
        'user_inbox_unread_count': unread_count,
        'user_inbox_items': recent_notifications,
        'user_inbox_available': inbox_available,
        'user_message_recipients': message_recipients,
    }
