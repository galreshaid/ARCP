from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.users.forms import EmailAuthenticationForm
from apps.users.models import UserNotification
from apps.protocols.services.notifications import send_direct_user_message


class UserLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy('home')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'title': 'Sign In',
            'ldap_enabled': bool(getattr(settings, 'LDAP_AUTH_ENABLED', False)),
            'ldap_available': bool(getattr(settings, 'LDAP_AUTH_AVAILABLE', False)),
        })
        return context


class UserLogoutView(LogoutView):
    next_page = reverse_lazy('login')


def _notifications_available() -> bool:
    try:
        UserNotification.objects.exists()
        return True
    except (OperationalError, ProgrammingError):
        return False


def _redirect_back(request, fallback_name='home'):
    referrer = str(request.META.get('HTTP_REFERER') or '').strip()
    if referrer and url_has_allowed_host_and_scheme(
        url=referrer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referrer)

    return redirect(fallback_name)


def _notification_display_name(user) -> str:
    if not user:
        return "System"

    full_name = ""
    if hasattr(user, "get_full_name"):
        full_name = str(user.get_full_name() or "").strip()

    return full_name or str(getattr(user, "username", "") or "").strip() or "User"


def _prefixed_title(prefix: str, title: str) -> str:
    clean_prefix = str(prefix or "").strip().upper()
    clean_title = str(title or "").strip() or "Direct message"
    prefix_token = f"{clean_prefix}: "
    if clean_title.upper().startswith(prefix_token):
        return clean_title
    return f"{prefix_token}{clean_title}"


def _build_compose_defaults(*, user, notification=None, mode: str = ""):
    defaults = {
        'compose_mode': 'new',
        'compose_heading': 'Send Direct Message',
        'compose_help': 'Send an internal message and email another user from one place.',
        'compose_recipient_id': '',
        'compose_title': '',
        'compose_body': '',
        'compose_target_url': '',
        'compose_submit_label': 'Send + Email',
    }

    if not notification:
        return defaults

    sender_name = _notification_display_name(getattr(notification, 'sender', None))
    created_label = notification.created_at.strftime('%Y-%m-%d %H:%M')
    target_url = str(getattr(notification, 'target_url', '') or '').strip()
    quoted_message = str(getattr(notification, 'message', '') or '').strip()

    if mode == 'reply':
        defaults.update({
            'compose_mode': 'reply',
            'compose_heading': 'Reply to Message',
            'compose_help': 'Reply to the sender. The message will be stored internally and sent by email.',
            'compose_recipient_id': (
                str(notification.sender_id)
                if getattr(notification, 'sender_id', None) and notification.sender_id != user.id
                else ''
            ),
            'compose_title': _prefixed_title('RE', notification.title),
            'compose_body': (
                "\n\n----- Original message -----\n"
                f"From: {sender_name}\n"
                f"Sent: {created_label}\n\n"
                f"{quoted_message}"
            ),
            'compose_target_url': target_url,
            'compose_submit_label': 'Reply + Email',
        })
        return defaults

    if mode == 'forward':
        defaults.update({
            'compose_mode': 'forward',
            'compose_heading': 'Forward Message',
            'compose_help': 'Forward the message to another user and keep an emailed copy.',
            'compose_title': _prefixed_title('FW', notification.title),
            'compose_body': "\n".join([
                "Forwarded message:",
                f"From: {sender_name}",
                f"Sent: {created_label}",
                "",
                quoted_message,
            ]),
            'compose_target_url': target_url,
            'compose_submit_label': 'Forward + Email',
        })
        return defaults

    if mode == 'share':
        link_line = target_url or 'No workflow link attached to this message.'
        defaults.update({
            'compose_mode': 'share',
            'compose_heading': 'Share Workflow Link',
            'compose_help': 'Share the related workflow page and include the message context.',
            'compose_title': f"Shared update: {notification.title}",
            'compose_body': "\n".join([
                "Please review the shared workflow item.",
                f"Shared by: {sender_name}",
                "",
                f"Linked page: {link_line}",
                "",
                quoted_message,
            ]),
            'compose_target_url': target_url,
            'compose_submit_label': 'Share + Email',
        })
        return defaults

    if mode == 'email':
        defaults.update({
            'compose_mode': 'email',
            'compose_heading': 'Send Emailed Copy',
            'compose_help': 'Send this message to another user and force an emailed copy through the normal message flow.',
            'compose_title': f"Email copy: {notification.title}",
            'compose_body': "\n".join([
                "Please review this copied message.",
                f"Original sender: {sender_name}",
                f"Original sent at: {created_label}",
                "",
                quoted_message,
            ]),
            'compose_target_url': target_url,
            'compose_submit_label': 'Send Email Copy',
        })

    return defaults


@login_required
def inbox_view(request):
    inbox_available = _notifications_available()
    notifications = []
    sent_notifications = []
    unread_count = 0
    read_count = 0
    selected_notification = None
    selected_is_recipient = False
    selected_is_sender = False
    compose_defaults = _build_compose_defaults(user=request.user)

    if inbox_available:
        notifications = list(
            UserNotification.objects.filter(
                recipient=request.user,
            ).select_related('sender')[:100]
        )
        sent_notifications = list(
            UserNotification.objects.filter(
                sender=request.user,
            ).select_related('recipient')[:40]
        )
        unread_count = sum(1 for item in notifications if item.read_at is None)
        read_count = len(notifications) - unread_count

        selected_id = str(request.GET.get('message') or '').strip()
        accessible_notifications = UserNotification.objects.filter(
            Q(recipient=request.user) | Q(sender=request.user),
        ).select_related('sender', 'recipient')

        if selected_id:
            try:
                selected_notification = get_object_or_404(
                    accessible_notifications,
                    id=selected_id,
                )
            except ValidationError:
                selected_notification = None
        if not selected_notification and notifications:
            selected_notification = notifications[0]
        elif not selected_notification and sent_notifications:
            selected_notification = sent_notifications[0]

        if selected_notification:
            selected_is_recipient = selected_notification.recipient_id == request.user.id
            selected_is_sender = selected_notification.sender_id == request.user.id
            compose_mode = str(request.GET.get('compose') or '').strip().lower()
            compose_defaults = _build_compose_defaults(
                user=request.user,
                notification=selected_notification,
                mode=compose_mode,
            )

    context = {
        'notifications': notifications,
        'sent_notifications': sent_notifications,
        'unread_count': unread_count,
        'read_count': read_count,
        'inbox_available': inbox_available,
        'selected_notification': selected_notification,
        'selected_is_recipient': selected_is_recipient,
        'selected_is_sender': selected_is_sender,
        'compose_mode': compose_defaults['compose_mode'],
        'compose_heading': compose_defaults['compose_heading'],
        'compose_help': compose_defaults['compose_help'],
        'compose_recipient_id': compose_defaults['compose_recipient_id'],
        'compose_title': compose_defaults['compose_title'],
        'compose_body': compose_defaults['compose_body'],
        'compose_target_url': compose_defaults['compose_target_url'],
        'compose_submit_label': compose_defaults['compose_submit_label'],
    }
    return render(request, 'users/inbox.html', context)


@login_required
@require_http_methods(["POST"])
def notification_open_view(request, notification_id):
    if not _notifications_available():
        return _redirect_back(request)

    notification = get_object_or_404(
        UserNotification,
        id=notification_id,
        recipient=request.user,
    )
    notification.mark_read()

    return redirect(notification.target_url or reverse('home'))


@login_required
@require_http_methods(["POST"])
def notification_mark_read_view(request, notification_id):
    if not _notifications_available():
        return _redirect_back(request)

    notification = get_object_or_404(
        UserNotification,
        id=notification_id,
        recipient=request.user,
    )
    notification.mark_read()
    return _redirect_back(request)


@login_required
@require_http_methods(["POST"])
def notification_mark_all_read_view(request):
    if not _notifications_available():
        return _redirect_back(request)

    UserNotification.objects.filter(
        recipient=request.user,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return _redirect_back(request)


@login_required
@require_http_methods(["POST"])
def notification_delete_view(request, notification_id):
    if not _notifications_available():
        return redirect('user-inbox')

    notification = get_object_or_404(
        UserNotification,
        id=notification_id,
        recipient=request.user,
    )
    notification.delete()
    return redirect('user-inbox')


@login_required
@require_http_methods(["POST"])
def notification_send_view(request):
    user_model = get_user_model()
    recipient_id = str(request.POST.get('message_recipient_id') or '').strip()
    title = str(request.POST.get('message_title') or '').strip()
    message = str(request.POST.get('message_body') or '').strip()
    target_url = str(request.POST.get('target_url') or '').strip()

    if not recipient_id or not message:
        return _redirect_back(request)

    try:
        recipient = get_object_or_404(
            user_model,
            id=recipient_id,
            is_active=True,
        )
    except ValidationError:
        return _redirect_back(request, fallback_name='user-inbox')

    send_direct_user_message(
        sender=request.user,
        recipient=recipient,
        title=title or 'Direct message',
        message=message,
        target_url=target_url,
    )
    return _redirect_back(request)
