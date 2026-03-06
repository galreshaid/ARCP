import logging
from types import SimpleNamespace

from django.conf import settings
from django.core.mail import send_mail
from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse
from django.utils import timezone

from apps.protocols.models import ProtocolAssignment, ProtocolComment
from apps.users.models import UserNotification


logger = logging.getLogger(__name__)


def _display_name(user) -> str:
    if not user:
        return ""

    full_name = ""
    if hasattr(user, "get_full_name"):
        full_name = (user.get_full_name() or "").strip()

    return full_name or getattr(user, "username", "") or ""


def _join_phrases(parts: list[str]) -> str:
    cleaned = [part.strip() for part in parts if str(part or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _add_system_comment(assignment: ProtocolAssignment, message: str):
    if not str(message or "").strip():
        return None

    return ProtocolComment.objects.create(
        assignment=assignment,
        author=None,
        author_role="SYSTEM",
        message=message.strip(),
    )


def _send_email(recipient, subject: str, body: str) -> bool:
    email = str(getattr(recipient, "email", "") or "").strip()
    if not email:
        return False

    from_email = (
        getattr(settings, "DEFAULT_FROM_EMAIL", "")
        or getattr(settings, "SERVER_EMAIL", "")
        or "no-reply@localhost"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Unable to send protocol workflow notification email.")
        return False


def _create_user_notification(
    *,
    recipient,
    sender,
    title: str,
    message: str,
    target_url: str = "",
    category: str = "PROTOCOL",
    email_subject: str = "",
    email_body: str = "",
):
    if not recipient:
        return None

    email_sent = False
    if email_subject and email_body:
        email_sent = _send_email(recipient, email_subject, email_body)

    try:
        notification = UserNotification.objects.create(
            recipient=recipient,
            sender=sender,
            title=title.strip(),
            message=message.strip(),
            category=category,
            target_url=target_url,
        )
    except (OperationalError, ProgrammingError):
        logger.warning(
            "User notification inbox table is not available yet; "
            "skipping inbox record until migrations are applied."
        )
        return SimpleNamespace(email_sent=email_sent)

    if email_sent:
        notification.email_sent = True
        notification.emailed_at = timezone.now()
        notification.save(update_fields=["email_sent", "emailed_at"])

    return notification


def notify_radiologist_of_technologist_update(
    assignment: ProtocolAssignment,
    technologist,
    *,
    technologist_note_changed: bool = False,
    comment_added: bool = False,
) -> bool:
    recipient = assignment.assigned_by
    technologist_name = _display_name(technologist) or "Technologist"
    radiologist_name = _display_name(recipient) or "Radiologist"

    actions = ["confirmed the protocol"]
    if technologist_note_changed:
        actions.append("updated the technologist note")
    if comment_added:
        actions.append("added a workflow comment")

    action_summary = _join_phrases(actions)
    review_path = reverse("protocoling-radiologist-review", args=[assignment.exam.id])
    protocol_label = f"{assignment.protocol.code} - {assignment.protocol.name}"
    inbox_message = "\n".join([
        f"{technologist_name} {action_summary}.",
        f"Accession: {assignment.exam.accession_number}",
        f"Order: {assignment.exam.order_id}",
        f"Patient: {assignment.exam.patient_name}",
        f"Procedure: {assignment.exam.procedure_name}",
        f"Protocol: {protocol_label}",
        f"Technologist note: {assignment.technologist_note or '—'}",
    ])

    notification = _create_user_notification(
        recipient=recipient,
        sender=technologist,
        title=f"Protocol confirmation update: {assignment.exam.accession_number}",
        message=inbox_message,
        target_url=review_path,
        category="PROTOCOL_CONFIRMATION",
        email_subject=f"Protocol confirmation update: {assignment.exam.accession_number}",
        email_body="\n".join([
            inbox_message,
            "",
            f"Review page: {review_path}",
        ]),
    )
    email_sent = bool(notification and notification.email_sent)

    email_status = (
        f"An email was sent to {radiologist_name}."
        if email_sent else
        f"No email was sent to {radiologist_name}."
    )
    _add_system_comment(
        assignment,
        (
            f"System notice: {technologist_name} {action_summary}. "
            f"{email_status}"
        ),
    )
    return email_sent


def notify_technologist_of_radiologist_revision(
    assignment: ProtocolAssignment,
    radiologist,
    technologist,
    *,
    protocol_changed: bool = False,
    note_changed: bool = False,
    method_changed: bool = False,
    comment_added: bool = False,
) -> bool:
    radiologist_name = _display_name(radiologist) or "Radiologist"
    technologist_name = _display_name(technologist) or "Technologist"

    updates = []
    if protocol_changed:
        updates.append("changed the protocol")
    if method_changed:
        updates.append("updated the assignment method")
    if note_changed:
        updates.append("updated the radiologist note")
    if comment_added:
        updates.append("added a new comment")

    update_summary = _join_phrases(updates) or "updated the assignment"
    review_path = reverse("protocoling-technologist-view", args=[assignment.exam.id])
    protocol_label = f"{assignment.protocol.code} - {assignment.protocol.name}"
    inbox_message = "\n".join([
        f"{radiologist_name} {update_summary} after the protocol was already confirmed.",
        "Please reopen the technologist review page.",
        f"Accession: {assignment.exam.accession_number}",
        f"Order: {assignment.exam.order_id}",
        f"Patient: {assignment.exam.patient_name}",
        f"Procedure: {assignment.exam.procedure_name}",
        f"Current protocol: {protocol_label}",
        f"Radiologist note: {assignment.radiologist_note or '—'}",
    ])

    notification = _create_user_notification(
        recipient=technologist,
        sender=radiologist,
        title=f"Protocol changed after confirmation: {assignment.exam.accession_number}",
        message=inbox_message,
        target_url=review_path,
        category="PROTOCOL_REVIEW_REQUIRED",
        email_subject=f"Protocol changed after confirmation: {assignment.exam.accession_number}",
        email_body="\n".join([
            inbox_message,
            "",
            f"Review page: {review_path}",
        ]),
    )
    email_sent = bool(notification and notification.email_sent)

    email_status = (
        f"An email was sent to {technologist_name}."
        if email_sent else
        f"No email was sent to {technologist_name}."
    )
    _add_system_comment(
        assignment,
        (
            f"System notice: {radiologist_name} {update_summary} after technologist confirmation. "
            f"Technologist follow-up is required. {email_status}"
        ),
    )
    return email_sent


def send_direct_user_message(
    *,
    sender,
    recipient,
    title: str,
    message: str,
    target_url: str = "",
):
    sender_name = _display_name(sender) or "User"
    clean_title = str(title or "").strip() or "Direct message"
    clean_message = str(message or "").strip()
    if not clean_message:
        return None

    email_body = "\n".join([
        f"Message from: {sender_name}",
        f"Title: {clean_title}",
        "",
        clean_message,
    ])
    if target_url:
        email_body = "\n".join([
            email_body,
            "",
            f"Open: {target_url}",
        ])

    return _create_user_notification(
        recipient=recipient,
        sender=sender,
        title=clean_title,
        message=clean_message,
        target_url=target_url,
        category="DIRECT_MESSAGE",
        email_subject=f"Direct message: {clean_title}",
        email_body=email_body,
    )
