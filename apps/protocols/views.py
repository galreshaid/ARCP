"""
Protocol Views
API endpoints + UI views for protocol management
"""
from datetime import datetime

# ============================================================
# Django imports
# ============================================================

from django.db.models import Q
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_GET
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.urls import reverse

from apps.core.constants import Permission, UserRole
from apps.core.models import Exam, Facility
from apps.core.deeplinks.validator import deeplink_validator, DeepLinkValidationError

# ============================================================
# DRF imports
# ============================================================

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView

# ============================================================
# Protocol imports
# ============================================================

from apps.protocols.models import (
    ProtocolTemplate,
    ProtocolAssignment,
    ProtocolComment,
    AssignmentMethod,
    AssignmentStatus,
)

from apps.protocols.serializers import (
    ProtocolTemplateListSerializer,
    ProtocolTemplateDetailSerializer,
    ProtocolAssignmentSerializer,
    ProtocolAssignmentUpdateSerializer,
    ProtocolSuggestionSerializer,
    ProtocolAssignmentStatsSerializer,
    ProtocolDeepLinkResponseSerializer,
    ExamSummarySerializer,
)

from apps.protocols.services.assignment import protocol_assignment_service
from apps.protocols.services.preference_learning import preference_learning_service
from apps.protocols.services.notifications import (
    notify_radiologist_of_technologist_update,
    send_direct_user_message,
    notify_technologist_of_radiologist_revision,
)
from apps.protocols.services.suggestion import protocol_suggestion_service


# ============================================================
# Helpers
# ============================================================

def _role(user) -> str:
    return getattr(user, "role", "") or ""


def _display_name(user) -> str:
    if not user:
        return ""

    full_name = ""
    if hasattr(user, "get_full_name"):
        full_name = (user.get_full_name() or "").strip()

    return full_name or getattr(user, "username", "") or ""


def _can_access_radiologist_review(user) -> bool:
    if user.is_superuser:
        return True

    return (
        user.has_permission(Permission.PROTOCOL_ASSIGN)
        and _role(user) in (UserRole.RADIOLOGIST, UserRole.ADMIN)
    )


def _can_access_technologist_review(user) -> bool:
    if user.is_superuser:
        return True

    return (
        user.has_permission(Permission.PROTOCOL_VIEW)
        and _role(user) in (UserRole.TECHNOLOGIST, UserRole.ADMIN)
    )


def _can_confirm_protocol(user) -> bool:
    return _can_access_technologist_review(user)


def _direct_message_target_url(recipient, exam_id) -> str:
    recipient_role = _role(recipient)
    if recipient_role == UserRole.TECHNOLOGIST:
        return reverse("protocoling-technologist-view", args=[exam_id])
    if recipient_role in (UserRole.RADIOLOGIST, UserRole.ADMIN):
        return reverse("protocoling-radiologist-review", args=[exam_id])
    return ""


def _message_recipients_for(user):
    user_model = get_user_model()
    return user_model.objects.filter(
        is_active=True,
    ).exclude(pk=user.pk).order_by("first_name", "last_name", "username")


def _build_assignment_timeline(assignment: ProtocolAssignment | None):
    if not assignment:
        return []

    events = []
    modifications = dict(assignment.modifications or {})
    history = modifications.get("history") or []

    initial_protocol_label = f"{assignment.protocol.code} - {assignment.protocol.name}"
    for item in history:
        summary = str(item.get("summary") or "").strip()
        if not summary.startswith("Protocol changed from "):
            continue

        previous_code = (
            summary.removeprefix("Protocol changed from ")
            .split(" to ", 1)[0]
            .strip()
            .rstrip(".")
        )
        if previous_code:
            initial_protocol_label = f"{previous_code} - Initial assignment"
            break

    events.append({
        "event_type": "assignment",
        "title": "Protocol assigned",
        "actor": _display_name(assignment.assigned_by) or "System",
        "occurred_at": assignment.created_at or assignment.assigned_at,
        "body": initial_protocol_label,
    })

    for item in history:
        occurred_at = None
        raw_occurred_at = str(item.get("at") or "").strip()
        if raw_occurred_at:
            try:
                occurred_at = datetime.fromisoformat(raw_occurred_at)
                if timezone.is_naive(occurred_at):
                    occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())
            except ValueError:
                occurred_at = None

        events.append({
            "event_type": "update",
            "title": "Assignment updated",
            "actor": str(item.get("by") or "").strip() or _display_name(assignment.assigned_by) or "System",
            "occurred_at": occurred_at or assignment.updated_at,
            "body": str(item.get("summary") or "").strip() or "Assignment details were updated.",
        })

    if assignment.notification_sent_at:
        events.append({
            "event_type": "notification",
            "title": "Technologist notified",
            "actor": "System",
            "occurred_at": assignment.notification_sent_at,
            "body": "The assignment was marked as delivered to the technologist workflow.",
        })

    if assignment.sent_to_ris_at:
        events.append({
            "event_type": "ris",
            "title": "Sent to RIS",
            "actor": "System",
            "occurred_at": assignment.sent_to_ris_at,
            "body": "The protocol assignment was sent to the RIS integration.",
        })

    if assignment.ris_ack_at:
        events.append({
            "event_type": "ris",
            "title": "RIS acknowledged",
            "actor": "System",
            "occurred_at": assignment.ris_ack_at,
            "body": "The RIS acknowledged the protocol assignment message.",
        })

    if assignment.acknowledged_at:
        events.append({
            "event_type": "acknowledged",
            "title": "Protocol acknowledged",
            "actor": _display_name(assignment.acknowledged_by) or "Technologist",
            "occurred_at": assignment.acknowledged_at,
            "body": "The technologist confirmed the protocol can proceed.",
        })

    for comment in assignment.comments.all().order_by("created_at"):
        events.append({
            "event_type": "comment",
            "title": "Comment added",
            "actor": _display_name(comment.author) or comment.author_role or "User",
            "occurred_at": comment.created_at,
            "body": comment.message,
        })

    events.sort(key=lambda item: item.get("occurred_at") or assignment.created_at)
    return events


# ============================================================
# API – Protocol Templates
# ============================================================

class ProtocolTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = ProtocolTemplate.objects.all()

    def get_queryset(self):
        user = self.request.user
        qs = ProtocolTemplate.objects.filter(is_active=True)

        if not user.is_superuser:
            qs = qs.filter(
                Q(facility__isnull=True)
                | Q(facility__in=user.facilities.all())
            )

        if modality := self.request.query_params.get("modality"):
            qs = qs.filter(modality__code=modality)

        if body_part := self.request.query_params.get("body_part"):
            qs = qs.filter(body_region__icontains=body_part)

        if facility := self.request.query_params.get("facility"):
            qs = qs.filter(Q(facility__isnull=True) | Q(facility__code=facility))

        return qs.select_related("modality", "facility")

    def get_serializer_class(self):
        return (
            ProtocolTemplateDetailSerializer
            if self.action == "retrieve"
            else ProtocolTemplateListSerializer
        )

    @action(detail=False, methods=["get"])
    def search(self, request):
        q = request.query_params.get("q", "").strip()
        if not q:
            return Response({"results": []})

        qs = self.get_queryset().filter(
            Q(code__icontains=q)
            | Q(name__icontains=q)
            | Q(body_region__icontains=q)
        )[:20]

        return Response({"results": self.get_serializer(qs, many=True).data})


# ============================================================
# API – Protocol Assignment
# ============================================================

class ProtocolAssignmentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = ProtocolAssignment.objects.all()
    serializer_class = ProtocolAssignmentSerializer

    def get_queryset(self):
        qs = ProtocolAssignment.objects.select_related(
            "exam", "protocol", "assigned_by"
        )

        user = self.request.user
        if not user.is_superuser:
            qs = qs.filter(exam__facility__in=user.facilities.all())

        if status_q := self.request.query_params.get("status"):
            qs = qs.filter(status=status_q)

        return qs

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return ProtocolAssignmentUpdateSerializer
        return ProtocolAssignmentSerializer

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        assignment = self.get_object()
        protocol_assignment_service.acknowledge_assignment(
            assignment=assignment,
            technologist=request.user,
        )
        return Response(self.get_serializer(assignment).data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        radiologist = None
        facility = None

        if rid := request.query_params.get("radiologist"):
            from django.contrib.auth import get_user_model
            radiologist = get_object_or_404(get_user_model(), id=rid)

        if fcode := request.query_params.get("facility"):
            facility = get_object_or_404(Facility, code=fcode)

        days = int(request.query_params.get("days", 30))

        stats = protocol_assignment_service.get_assignment_stats(
            radiologist=radiologist,
            facility=facility,
            days=days,
        )

        return Response(ProtocolAssignmentStatsSerializer(stats).data)


# ============================================================
# API – Protocol Suggestions
# ============================================================

class ProtocolSuggestionViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProtocolSuggestionSerializer

    def list(self, request):
        exam_id = request.query_params.get("exam_id")
        if not exam_id:
            return Response(
                {"error": "exam_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exam = get_object_or_404(Exam, id=exam_id)

        suggestions = protocol_suggestion_service.suggest_protocols(
            exam=exam,
            radiologist=request.user,
            max_suggestions=int(request.query_params.get("max", 5)),
        )
        manual_protocols = ProtocolTemplate.objects.filter(
            is_active=True,
            modality=exam.modality,
        ).filter(
            Q(facility__isnull=True) | Q(facility=exam.facility)
        ).select_related("modality", "facility").order_by("priority", "code")

        return Response({
            "exam_id": str(exam.id),
            "exam": ExamSummarySerializer(exam).data,
            "manual_protocols": ProtocolTemplateListSerializer(
                manual_protocols,
                many=True,
            ).data,
            "suggestions": self.serializer_class(
                [
                    {
                        "protocol": s.protocol,
                        "score": s.score,
                        "match_percent": s.match_percent,
                        "rank": s.rank,
                        "reasoning": s.reasoning,
                    }
                    for s in suggestions
                ],
                many=True,
            ).data,
        })


# ============================================================
# API – Deep Link
# ============================================================

class ProtocolDeepLinkView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProtocolDeepLinkResponseSerializer

    def get(self, request):
        token = request.query_params.get("token")
        if not token:
            return Response({"error": "Token is required"}, status=400)

        try:
            payload = deeplink_validator.validate_for_user(
                token=token,
                user=request.user,
                required_type="protocol",
            )

            ctx = deeplink_validator.extract_exam_context(payload)
            exam = get_object_or_404(Exam, id=ctx["exam_id"])

            assignment = getattr(exam, "protocol_assignment", None)
            suggestions = protocol_suggestion_service.suggest_protocols(
                exam=exam,
                radiologist=request.user,
                max_suggestions=5,
            )

            return Response(self.get_serializer({
                "exam": ExamSummarySerializer(exam).data,
                "existing_assignment": (
                    ProtocolAssignmentSerializer(assignment).data
                    if assignment else None
                ),
                "suggestions": ProtocolSuggestionSerializer(
                    [
                        {
                            "protocol": s.protocol,
                            "score": s.score,
                            "match_percent": s.match_percent,
                            "rank": s.rank,
                            "reasoning": s.reasoning,
                        }
                        for s in suggestions
                    ],
                    many=True,
                ).data,
            }).data)

        except DeepLinkValidationError as e:
            return Response({"error": str(e)}, status=400)


# ============================================================
# UI – Radiologist Assign
# ============================================================

@login_required
@require_http_methods(["GET", "POST"])
def radiologist_assign(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    form_error = None
    direct_message_error = None
    saved = request.GET.get("saved") == "1"
    message_sent = request.GET.get("message_sent") == "1"
    auto_update_comment = ""

    if not _can_access_radiologist_review(request.user):
        return HttpResponseForbidden("Not allowed")

    assignment = getattr(exam, "protocol_assignment", None)
    candidates = ProtocolTemplate.objects.filter(
        is_active=True,
        modality=exam.modality,
    ).order_by("priority", "code")

    suggestions = protocol_suggestion_service.suggest_protocols(
        exam=exam,
        radiologist=request.user,
        max_suggestions=8,
    )
    current_protocol = assignment.protocol if assignment else None
    current_sequences = (
        current_protocol.sequences.all().order_by("ser")
        if current_protocol else []
    )
    comments = assignment.comments.all().order_by("created_at") if assignment else []
    user_model = get_user_model()
    message_recipients = _message_recipients_for(request.user)
    message_title = ""
    message_body = ""
    message_recipient_id = ""

    if request.method == "POST":
        form_action = (request.POST.get("form_action") or "save_assignment").strip()

        if form_action == "send_message":
            message_title = request.POST.get("message_title", "").strip()
            message_body = request.POST.get("message_body", "").strip()
            message_recipient_id = request.POST.get("message_recipient_id", "").strip()

            if not message_recipient_id:
                direct_message_error = "Select a user before sending a direct message."
            elif not message_body:
                direct_message_error = "Enter a message before sending."
            else:
                recipient = get_object_or_404(
                    user_model,
                    id=message_recipient_id,
                    is_active=True,
                )
                message_subject = (
                    message_title
                    or f"Protocol message for {exam.accession_number}"
                )
                notification = send_direct_user_message(
                    sender=request.user,
                    recipient=recipient,
                    title=message_subject,
                    message=message_body,
                    target_url=_direct_message_target_url(recipient, exam.id),
                )

                if notification is None:
                    direct_message_error = "Unable to send the direct message."
                else:
                    if assignment:
                        recipient_name = _display_name(recipient) or recipient.username
                        ProtocolComment.objects.create(
                            assignment=assignment,
                            author=request.user,
                            author_role=_role(request.user),
                            message=(
                                f"Direct message sent to {recipient_name}: "
                                f"{message_subject}"
                            ),
                        )

                    return redirect(
                        f'{reverse("protocoling-radiologist-review", args=[exam.id])}?message_sent=1'
                    )
        else:
            comment_text = request.POST.get("comment", "").strip()
            selected_protocol_id = (
                request.POST.get("manual_protocol_id")
                or request.POST.get("suggested_protocol_id")
            )

            if not selected_protocol_id:
                form_error = "Select a protocol before saving."
            else:
                protocol = get_object_or_404(
                    ProtocolTemplate,
                    id=selected_protocol_id,
                )

                method = (
                    AssignmentMethod.AI
                    if request.POST.get("ai_selected") == "1"
                    else AssignmentMethod.MANUAL
                )
                radiologist_note = request.POST.get("radiologist_note", "").strip()
                acknowledged_technologist = None
                protocol_changed = False
                method_changed = False
                note_changed = False
                comment_added = bool(comment_text)
                notify_technologist_after_save = False

                if assignment:
                    now = timezone.now()
                    change_descriptions = []
                    acknowledged_technologist = assignment.acknowledged_by
                    was_acknowledged = bool(assignment.acknowledged_at)
                    protocol_changed = assignment.protocol_id != protocol.id
                    method_changed = assignment.assignment_method != method
                    note_changed = (assignment.radiologist_note or "").strip() != radiologist_note
                    reopens_workflow = protocol_changed or method_changed or note_changed or comment_added

                    if protocol_changed:
                        change_descriptions.append(
                            f"Protocol changed from {assignment.protocol.code} to {protocol.code}."
                        )
                    if method_changed:
                        change_descriptions.append(
                            f"Assignment method changed from {assignment.assignment_method} to {method}."
                        )
                    if note_changed:
                        change_descriptions.append("Radiologist handoff note updated.")

                    assignment.protocol = protocol
                    assignment.assigned_by = request.user
                    assignment.assignment_method = method
                    assignment.radiologist_note = radiologist_note
                    if reopens_workflow:
                        assignment.status = AssignmentStatus.PENDING
                    if protocol_changed or method_changed:
                        assignment.assigned_at = now

                    update_fields = [
                        "protocol",
                        "assigned_by",
                        "assignment_method",
                        "radiologist_note",
                    ]
                    if reopens_workflow:
                        update_fields.append("status")
                    if protocol_changed or method_changed:
                        update_fields.append("assigned_at")

                    if was_acknowledged and (protocol_changed or method_changed or note_changed or comment_added):
                        assignment.acknowledged_by = None
                        assignment.acknowledged_at = None
                        change_descriptions.append("Technologist confirmation cleared; re-review required.")
                        update_fields.extend([
                            "acknowledged_by",
                            "acknowledged_at",
                        ])

                    if change_descriptions:
                        modifications = dict(assignment.modifications or {})
                        history = list(modifications.get("history") or [])
                        auto_update_comment = " ".join(change_descriptions)
                        history.append({
                            "at": now.isoformat(),
                            "by": _display_name(request.user) or "System",
                            "summary": auto_update_comment,
                        })
                        modifications["history"] = history
                        modifications["changes"] = change_descriptions
                        modifications["last_updated_at"] = now.isoformat()
                        modifications["last_updated_by"] = _display_name(request.user) or "System"

                        assignment.is_modified = True
                        assignment.modifications = modifications
                        assignment.modification_notes = auto_update_comment
                        update_fields.extend([
                            "is_modified",
                            "modifications",
                            "modification_notes",
                        ])

                    assignment.save(update_fields=update_fields)

                    notify_technologist_after_save = (
                        acknowledged_technologist
                        and was_acknowledged
                        and (protocol_changed or method_changed or note_changed or comment_added)
                    )
                else:
                    assignment = ProtocolAssignment.objects.create(
                        exam=exam,
                        protocol=protocol,
                        assigned_by=request.user,
                        assignment_method=method,
                        status=AssignmentStatus.PENDING,
                        radiologist_note=radiologist_note,
                    )

                if comment_text:
                    ProtocolComment.objects.create(
                        assignment=assignment,
                        author=request.user,
                        author_role=_role(request.user),
                        message=comment_text,
                    )

                if auto_update_comment:
                    ProtocolComment.objects.create(
                        assignment=assignment,
                        author=request.user,
                        author_role=_role(request.user),
                        message=f"Update: {auto_update_comment}",
                    )

                if notify_technologist_after_save:
                    notify_technologist_of_radiologist_revision(
                        assignment=assignment,
                        radiologist=request.user,
                        technologist=acknowledged_technologist,
                        protocol_changed=protocol_changed,
                        note_changed=note_changed,
                        method_changed=method_changed,
                        comment_added=comment_added,
                    )

                try:
                    preference_learning_service.update_preference(
                        radiologist=request.user,
                        exam=exam,
                        selected_protocol=protocol,
                        was_suggested=method == AssignmentMethod.AI,
                    )
                except Exception:
                    pass

                # TODO: send ORR to GERIS
                return redirect(
                    f'{reverse("protocoling-radiologist-review", args=[exam.id])}?saved=1'
                )

    timeline_events = _build_assignment_timeline(assignment)

    return render(request, "protocoling/radiologist_assign.html", {
        "exam": exam,
        "assignment": assignment,
        "suggestions": suggestions,
        "candidates": candidates,
        "current_protocol": current_protocol,
        "current_sequences": current_sequences,
        "comments": comments,
        "timeline_events": timeline_events,
        "form_error": form_error,
        "direct_message_error": direct_message_error,
        "message_sent": message_sent,
        "message_recipients": message_recipients,
        "message_title": message_title,
        "message_body": message_body,
        "message_recipient_id": message_recipient_id,
        "saved": saved,
        "technologist_view_url": reverse("protocoling-technologist-view", args=[exam.id]),
        "technologist_print_url": (
            reverse("protocoling-technologist-print", args=[exam.id])
            if assignment else ""
        ),
    })


# ============================================================
# UI – Technologist View
# ============================================================

@login_required
@require_http_methods(["GET", "POST"])
def technologist_view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    form_error = None
    direct_message_error = None
    confirmation_saved = request.GET.get("confirmed") == "1"
    message_sent = request.GET.get("message_sent") == "1"

    if not _can_access_technologist_review(request.user):
        return HttpResponseForbidden("Not allowed")

    assignment = getattr(exam, "protocol_assignment", None)
    user_model = get_user_model()
    message_recipients = _message_recipients_for(request.user)
    message_title = ""
    message_body = ""
    message_recipient_id = ""
    if not assignment:
        return render(
            request,
            "protocoling/technologist_view.html",
            {
                "exam": exam,
                "confirmation_saved": confirmation_saved,
                "form_error": form_error,
                "direct_message_error": direct_message_error,
                "message_sent": message_sent,
                "message_recipients": message_recipients,
                "message_title": message_title,
                "message_body": message_body,
                "message_recipient_id": message_recipient_id,
                "can_confirm_protocol": _can_confirm_protocol(request.user),
                "timeline_events": [],
                "print_url": "",
            },
        )

    if request.method == "POST":
        form_action = (request.POST.get("form_action") or "confirm_protocol").strip()

        if form_action == "send_message":
            message_title = request.POST.get("message_title", "").strip()
            message_body = request.POST.get("message_body", "").strip()
            message_recipient_id = request.POST.get("message_recipient_id", "").strip()

            if not message_recipient_id:
                direct_message_error = "Select a user before sending a direct message."
            elif not message_body:
                direct_message_error = "Enter a message before sending."
            else:
                recipient = get_object_or_404(
                    user_model,
                    id=message_recipient_id,
                    is_active=True,
                )
                message_subject = (
                    message_title
                    or f"Protocol message for {exam.accession_number}"
                )
                notification = send_direct_user_message(
                    sender=request.user,
                    recipient=recipient,
                    title=message_subject,
                    message=message_body,
                    target_url=_direct_message_target_url(recipient, exam.id),
                )

                if notification is None:
                    direct_message_error = "Unable to send the direct message."
                else:
                    recipient_name = _display_name(recipient) or recipient.username
                    ProtocolComment.objects.create(
                        assignment=assignment,
                        author=request.user,
                        author_role=_role(request.user),
                        message=(
                            f"Direct message sent to {recipient_name}: "
                            f"{message_subject}"
                        ),
                    )

                    return redirect(
                        f'{reverse("protocoling-technologist-view", args=[exam.id])}?message_sent=1'
                    )
        elif not _can_confirm_protocol(request.user):
            form_error = "Only technologists or admins can confirm a protocol."
        elif request.POST.get("confirm_review") != "1":
            form_error = "Please confirm you reviewed the protocol before saving."
        else:
            now = timezone.now()
            previous_technologist_note = (assignment.technologist_note or "").strip()
            comment_text = request.POST.get("comment", "").strip()
            assignment.technologist_note = request.POST.get("technologist_note", "").strip()
            assignment.status = AssignmentStatus.ACKNOWLEDGED
            assignment.acknowledged_by = request.user
            assignment.acknowledged_at = now

            update_fields = [
                "technologist_note",
                "status",
                "acknowledged_by",
                "acknowledged_at",
            ]

            if previous_technologist_note != assignment.technologist_note:
                modifications = dict(assignment.modifications or {})
                history = list(modifications.get("history") or [])
                history.append({
                    "at": now.isoformat(),
                    "by": _display_name(request.user) or "System",
                    "summary": "Technologist note updated.",
                })
                modifications["history"] = history
                modifications["changes"] = ["Technologist note updated."]
                modifications["last_updated_at"] = now.isoformat()
                modifications["last_updated_by"] = _display_name(request.user) or "System"

                assignment.is_modified = True
                assignment.modifications = modifications
                assignment.modification_notes = "Technologist note updated."
                update_fields.extend([
                    "is_modified",
                    "modifications",
                    "modification_notes",
                ])

            assignment.save(update_fields=update_fields)
            technologist_note_changed = previous_technologist_note != assignment.technologist_note

            if comment_text:
                ProtocolComment.objects.create(
                    assignment=assignment,
                    author=request.user,
                    author_role=_role(request.user),
                    message=comment_text,
                )

            notify_radiologist_of_technologist_update(
                assignment=assignment,
                technologist=request.user,
                technologist_note_changed=technologist_note_changed,
                comment_added=bool(comment_text),
            )

            return redirect(
                f'{reverse("protocoling-technologist-view", args=[exam.id])}?confirmed=1'
            )

    timeline_events = _build_assignment_timeline(assignment)

    return render(request, "protocoling/technologist_view.html", {
        "exam": exam,
        "assignment": assignment,
        "sequences": assignment.protocol.sequences.all().order_by("ser"),
        "comments": assignment.comments.all().order_by("created_at"),
        "timeline_events": timeline_events,
        "form_error": form_error,
        "direct_message_error": direct_message_error,
        "confirmation_saved": confirmation_saved,
        "message_sent": message_sent,
        "message_recipients": message_recipients,
        "message_title": message_title,
        "message_body": message_body,
        "message_recipient_id": message_recipient_id,
        "can_confirm_protocol": _can_confirm_protocol(request.user),
        "print_url": reverse("protocoling-technologist-print", args=[exam.id]),
    })


# ============================================================
# UI – Technologist Printable View
# ============================================================

@login_required
@require_GET
def technologist_print_protocol(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)

    if not _can_access_technologist_review(request.user):
        return HttpResponseForbidden("Not allowed")

    assignment = getattr(exam, "protocol_assignment", None)

    return render(
        request,
        "protocoling/technologist_print.html",
        {
            "exam": exam,
            "assignment": assignment,
            "protocol": assignment.protocol if assignment else None,
            "sequences": assignment.protocol.sequences.all().order_by("ser") if assignment else [],
            "comments": assignment.comments.all().order_by("created_at") if assignment else [],
        },
    )
