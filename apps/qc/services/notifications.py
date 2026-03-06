from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse
from django.utils import timezone

from apps.core.constants import UserRole
from apps.qc.services.access import user_can_supervise_modality
from apps.users.models import User, UserNotification


logger = logging.getLogger(__name__)
QUALITY_DEPARTMENT_GROUP_NAMES = (
    "Quality Department",
    "Quality Department Representative",
    "Quality Officer",
    "Quality Representative",
)


def _display_name(user) -> str:
    if not user:
        return "System"

    full_name = ""
    if hasattr(user, "get_full_name"):
        full_name = str(user.get_full_name() or "").strip()

    return full_name or str(getattr(user, "username", "") or "").strip() or "User"


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
        logger.exception("Unable to send QC notification email.")
        return False


def _create_notification(
    *,
    recipient,
    sender,
    title: str,
    message: str,
    target_url: str = "",
    category: str = "QC",
):
    if not recipient:
        return None

    email_subject = title
    email_body = message
    if target_url:
        email_body = "\n".join([message, "", f"Open: {target_url}"])

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
        logger.warning("User notifications table is not available yet; skipping QC inbox notification.")
        return None

    if email_sent:
        notification.email_sent = True
        notification.emailed_at = timezone.now()
        notification.save(update_fields=["email_sent", "emailed_at"])

    return notification


def _supervisor_recipients_for_exam(exam) -> list[User]:
    qs = User.objects.filter(
        is_active=True,
    ).filter(
        models.Q(is_superuser=True)
        | models.Q(role=UserRole.ADMIN)
        | models.Q(role=UserRole.SUPERVISOR)
    )

    if getattr(exam, "facility_id", None):
        qs = qs.filter(
            models.Q(is_superuser=True)
            | models.Q(role=UserRole.ADMIN)
            | models.Q(facilities__id=exam.facility_id)
            | models.Q(primary_facility_id=exam.facility_id)
        )

    recipients = []
    for candidate in qs.distinct():
        if getattr(candidate, "is_superuser", False):
            recipients.append(candidate)
            continue

        if getattr(candidate, "role", "") == UserRole.SUPERVISOR:
            if user_can_supervise_modality(candidate, exam.modality.code):
                recipients.append(candidate)
            continue

        recipients.append(candidate)

    return recipients


def _quality_representative_recipients_for_exam(exam) -> list[User]:
    qs = User.objects.filter(
        is_active=True,
    ).filter(
        models.Q(is_superuser=True)
        | models.Q(role=UserRole.ADMIN)
        | models.Q(groups__name__in=QUALITY_DEPARTMENT_GROUP_NAMES)
        | models.Q(department__icontains="quality")
    )

    if getattr(exam, "facility_id", None):
        qs = qs.filter(
            models.Q(is_superuser=True)
            | models.Q(role=UserRole.ADMIN)
            | models.Q(facilities__id=exam.facility_id)
            | models.Q(primary_facility_id=exam.facility_id)
        )

    return list(qs.distinct())


def notify_supervisors_of_qc_concern(*, session, raised_by):
    exam = session.exam
    sender_name = _display_name(raised_by)
    target_url = reverse("qc:review", args=[exam.id])
    concern_text = session.notes or "Concern raised without additional note."

    message = "\n".join(
        [
            f"{sender_name} raised a QC concern.",
            f"Accession: {exam.accession_number}",
            f"MRN: {exam.mrn}",
            f"Modality: {exam.modality.code}",
            f"Study: {exam.procedure_name}",
            f"Concern note: {concern_text}",
        ]
    )

    for recipient in _supervisor_recipients_for_exam(exam):
        if recipient.pk == getattr(raised_by, "pk", None):
            continue
        _create_notification(
            recipient=recipient,
            sender=raised_by,
            title=f"QC concern: {exam.accession_number}",
            message=message,
            target_url=target_url,
            category="QC_CONCERN",
        )


def notify_quality_department_of_qc_escalation(*, session, escalated_by, escalation_note: str = ""):
    exam = session.exam
    sender_name = _display_name(escalated_by)
    target_url = reverse("qc:review", args=[exam.id])
    escalation_text = str(escalation_note or "").strip() or "Escalation raised without additional note."

    message = "\n".join(
        [
            f"{sender_name} escalated a QC concern to Quality Department.",
            f"Accession: {exam.accession_number}",
            f"MRN: {exam.mrn}",
            f"Modality: {exam.modality.code}",
            f"Study: {exam.procedure_name}",
            f"Escalation note: {escalation_text}",
        ]
    )

    for recipient in _quality_representative_recipients_for_exam(exam):
        if recipient.pk == getattr(escalated_by, "pk", None):
            continue

        _create_notification(
            recipient=recipient,
            sender=escalated_by,
            title=f"QC escalation: {exam.accession_number}",
            message=message,
            target_url=target_url,
            category="QC_ESCALATION",
        )


def notify_radiologists_of_qc_decision(*, session, reviewed_by, decision: str):
    exam = session.exam
    reviewer_name = _display_name(reviewed_by)
    target_url = reverse("qc:review", args=[exam.id])

    recipients = User.objects.filter(
        is_active=True,
        role=UserRole.RADIOLOGIST,
        qc_sessions__exam=exam,
    ).filter(
        models.Q(qc_sessions__concern_raised=True) | ~models.Q(qc_sessions__notes="")
    ).distinct()

    message = "\n".join(
        [
            f"{reviewer_name} recorded a QC decision: {decision}.",
            f"Accession: {exam.accession_number}",
            f"MRN: {exam.mrn}",
            f"Modality: {exam.modality.code}",
            f"Study: {exam.procedure_name}",
            f"Review note: {session.notes or 'No additional note.'}",
        ]
    )

    for recipient in recipients:
        if recipient.pk == getattr(reviewed_by, "pk", None):
            continue
        _create_notification(
            recipient=recipient,
            sender=reviewed_by,
            title=f"QC decision update: {exam.accession_number}",
            message=message,
            target_url=target_url,
            category="QC_DECISION",
        )
