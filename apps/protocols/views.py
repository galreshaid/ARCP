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
from apps.core.models import Exam, Facility, Procedure
from apps.core.deeplinks.validator import deeplink_validator, DeepLinkValidationError
from apps.core.services.facility_scope import can_access_facility, scoped_facility_ids
from apps.core.services.subspeciality import (
    append_subspeciality_change_event,
    SUBSPECIALITY_POOL,
    normalize_subspeciality,
    resolve_exam_subspeciality,
    subspeciality_change_events,
)

# ============================================================
# DRF imports
# ============================================================

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
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


def _protocol_note_lines(value) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line[:1] in {"-", "*", "•"}:
            line = line[1:].strip()
        if line:
            lines.append(line)
    return lines


def _protocol_preview_payload(protocol: ProtocolTemplate) -> dict:
    sequences = [
        {
            "ser": sequence.ser,
            "coil": str(sequence.coil or "").strip(),
            "phase_array": str(sequence.phase_array or "").strip(),
            "scan_plane": str(sequence.scan_plane or "").strip(),
            "pulse_sequence": str(sequence.pulse_sequence or "").strip(),
            "options": str(sequence.options or "").strip(),
            "comments": str(sequence.comments or "").strip(),
        }
        for sequence in protocol.sequences.all().order_by("ser")
    ]
    return {
        "id": str(protocol.id),
        "code": str(protocol.code or "").strip(),
        "name": str(protocol.name or "").strip(),
        "indications": _protocol_note_lines(protocol.indications),
        "patient_prep": _protocol_note_lines(protocol.patient_prep),
        "safety_notes": _protocol_note_lines(protocol.safety_notes),
        "general_notes": _protocol_note_lines(protocol.general_notes),
        "sequences": sequences,
    }


def _parse_event_datetime(raw_value):
    if not raw_value:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _build_assignment_timeline(assignment: ProtocolAssignment | None, *, exam: Exam | None = None):
    events = []
    timeline_exam = exam or getattr(assignment, "exam", None)

    if timeline_exam is not None:
        metadata = dict(getattr(timeline_exam, "metadata", {}) or {})
        for item in subspeciality_change_events(metadata):
            events.append(
                {
                    "event_type": "subspeciality",
                    "title": "Subspeciality pool changed",
                    "actor": str(item.get("by") or "").strip() or "System",
                    "occurred_at": _parse_event_datetime(item.get("at")) or getattr(timeline_exam, "updated_at", None),
                    "body": str(item.get("summary") or "").strip() or "Subspeciality routing updated.",
                }
            )

    if not assignment:
        events.sort(key=lambda item: item.get("occurred_at") or timezone.now())
        return events

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
        occurred_at = _parse_event_datetime(item.get("at"))

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


class CanViewProtocolWorkflow(BasePermission):
    message = "Protocol view permission is required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.has_permission(Permission.PROTOCOL_VIEW)
        )


class CanAssignProtocolWorkflow(BasePermission):
    message = "Protocol assignment permission is required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and _can_access_radiologist_review(user)
        )


class CanAcknowledgeProtocolWorkflow(BasePermission):
    message = "Technologist protocol review permission is required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and _can_access_technologist_review(user)
        )


# ============================================================
# API – Protocol Templates
# ============================================================

class ProtocolTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, CanViewProtocolWorkflow]
    queryset = ProtocolTemplate.objects.all()

    def get_queryset(self):
        user = self.request.user
        qs = ProtocolTemplate.objects.filter(is_active=True)

        scoped_ids = scoped_facility_ids(user)
        if scoped_ids:
            qs = qs.filter(
                Q(facility__isnull=True)
                | Q(facility_id__in=scoped_ids)
            )
        elif not user.is_superuser:
            qs = qs.filter(facility__isnull=True)

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

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated(), CanAssignProtocolWorkflow()]
        if self.action == "acknowledge":
            return [IsAuthenticated(), CanAcknowledgeProtocolWorkflow()]
        return [IsAuthenticated(), CanViewProtocolWorkflow()]

    def get_queryset(self):
        qs = ProtocolAssignment.objects.select_related(
            "exam", "protocol", "assigned_by"
        )

        user = self.request.user
        scoped_ids = scoped_facility_ids(user)
        if scoped_ids:
            qs = qs.filter(exam__facility_id__in=scoped_ids)
        elif not user.is_superuser:
            qs = qs.none()

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
        scoped_ids = scoped_facility_ids(request.user)

        if rid := request.query_params.get("radiologist"):
            from django.contrib.auth import get_user_model
            radiologist = get_object_or_404(get_user_model(), id=rid)

        if fcode := request.query_params.get("facility"):
            facility = get_object_or_404(Facility, code=fcode)
            if scoped_ids and str(facility.id) not in scoped_ids:
                return Response(
                    {"error": "Not allowed for this facility."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if not scoped_ids and not request.user.is_superuser:
                return Response(
                    {"error": "Not allowed for this facility."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        days = int(request.query_params.get("days", 30))
        facility_ids = None
        if not facility:
            if scoped_ids:
                facility_ids = list(scoped_ids)
            elif not request.user.is_superuser:
                facility_ids = []

        stats = protocol_assignment_service.get_assignment_stats(
            radiologist=radiologist,
            facility=facility,
            facility_ids=facility_ids,
            days=days,
        )

        return Response(ProtocolAssignmentStatsSerializer(stats).data)


# ============================================================
# API – Protocol Suggestions
# ============================================================

class ProtocolSuggestionViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, CanAssignProtocolWorkflow]
    serializer_class = ProtocolSuggestionSerializer

    def list(self, request):
        exam_id = request.query_params.get("exam_id")
        if not exam_id:
            return Response(
                {"error": "exam_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exam = get_object_or_404(Exam, id=exam_id)
        if not can_access_facility(request.user, exam.facility_id):
            return Response({"error": "Not allowed for this facility."}, status=status.HTTP_403_FORBIDDEN)

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
    permission_classes = [IsAuthenticated, CanViewProtocolWorkflow]
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
            if not can_access_facility(request.user, exam.facility_id):
                return Response({"error": "Not allowed for this facility."}, status=403)

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
    if not can_access_facility(request.user, exam.facility_id):
        return HttpResponseForbidden("Not allowed for this facility")

    assignment = getattr(exam, "protocol_assignment", None)
    procedure_body_region = ""
    procedure_code = str(getattr(exam, "procedure_code", "") or "").strip()
    if procedure_code:
        procedure_body_region = (
            Procedure.objects.filter(code__iexact=procedure_code)
            .values_list("body_region", flat=True)
            .first()
            or ""
        )

    exam_metadata = dict(getattr(exam, "metadata", {}) or {})
    resolved_body_region = (
        str(exam_metadata.get("body_part") or exam_metadata.get("body_region") or "").strip()
        or str(procedure_body_region or "").strip()
    )
    current_subspeciality, inferred_subspeciality = resolve_exam_subspeciality(
        exam,
        body_region=resolved_body_region,
    )
    stored_subspeciality = normalize_subspeciality(
        exam_metadata.get("subspeciality") or exam_metadata.get("subspecialty")
    )

    candidates = ProtocolTemplate.objects.filter(
        is_active=True,
        modality=exam.modality,
    ).filter(
        Q(facility__isnull=True) | Q(facility=exam.facility),
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
    preview_protocol_ids = set(candidates.values_list("id", flat=True))
    preview_protocol_ids.update(
        suggestion.protocol.id
        for suggestion in suggestions
        if getattr(suggestion, "protocol", None) and getattr(suggestion.protocol, "id", None)
    )
    if current_protocol:
        preview_protocol_ids.add(current_protocol.id)
    preview_protocols = ProtocolTemplate.objects.filter(
        id__in=preview_protocol_ids
    ).prefetch_related("sequences")
    protocol_preview_map = {
        str(protocol.id): _protocol_preview_payload(protocol)
        for protocol in preview_protocols
    }
    initial_preview_protocol_id = str(current_protocol.id) if current_protocol else ""

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
            message_title = request.POST.get("message_title", "").strip()
            message_body = request.POST.get("message_body", "").strip()
            message_recipient_id = request.POST.get("message_recipient_id", "").strip()
            message_recipient = None
            message_subject = ""
            message_requested = bool(
                message_title
                or message_body
                or message_recipient_id
            )
            selected_protocol_id = (
                request.POST.get("manual_protocol_id")
                or request.POST.get("suggested_protocol_id")
            )
            if selected_protocol_id:
                initial_preview_protocol_id = str(selected_protocol_id)

            if message_requested:
                if not message_recipient_id:
                    direct_message_error = "Select a user to send the direct message while saving."
                elif not message_body:
                    direct_message_error = "Enter a direct message body before saving."
                else:
                    message_recipient = get_object_or_404(
                        user_model,
                        id=message_recipient_id,
                        is_active=True,
                    )
                    message_subject = (
                        message_title
                        or f"Protocol message for {exam.accession_number}"
                    )

            if not selected_protocol_id and not direct_message_error:
                form_error = "Select a protocol before saving."
            elif not direct_message_error:
                protocol = get_object_or_404(
                    ProtocolTemplate,
                    id=selected_protocol_id,
                    is_active=True,
                    modality=exam.modality,
                )
                if protocol.facility_id and protocol.facility_id != exam.facility_id:
                    form_error = "Selected protocol is not available for this exam facility."
                    protocol = None
                if protocol is None:
                    pass
                else:
                    method = (
                        AssignmentMethod.AI
                        if request.POST.get("ai_selected") == "1"
                        else AssignmentMethod.MANUAL
                    )
                    radiologist_note = request.POST.get("radiologist_note", "").strip()
                    selected_subspeciality = (
                        normalize_subspeciality(request.POST.get("subspeciality", ""))
                        or current_subspeciality
                    )
                    subspeciality_change_note = ""
                    change_timestamp = timezone.now()
                    if selected_subspeciality and selected_subspeciality != current_subspeciality:
                        subspeciality_change_note = (
                            f"Subspeciality changed from {current_subspeciality} to {selected_subspeciality}."
                        )
                    if selected_subspeciality and selected_subspeciality != stored_subspeciality:
                        actor_name = _display_name(request.user) or getattr(request.user, "username", "") or "System"
                        exam_metadata = append_subspeciality_change_event(
                            exam_metadata,
                            previous_subspeciality=current_subspeciality,
                            new_subspeciality=selected_subspeciality,
                            changed_by=actor_name,
                            changed_at=change_timestamp,
                        )
                        exam_metadata["subspeciality"] = selected_subspeciality
                        exam_metadata["subspecialty"] = selected_subspeciality
                        exam.metadata = exam_metadata
                        exam.save(update_fields=["metadata"])
                        stored_subspeciality = selected_subspeciality
                        current_subspeciality = selected_subspeciality

                    acknowledged_technologist = None
                    protocol_changed = False
                    method_changed = False
                    note_changed = False
                    comment_added = bool(comment_text)
                    notify_technologist_after_save = False

                    if assignment:
                        now = change_timestamp
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
                        if subspeciality_change_note:
                            change_descriptions.append(subspeciality_change_note)

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

                    if subspeciality_change_note and not assignment.modification_notes:
                        ProtocolComment.objects.create(
                            assignment=assignment,
                            author=request.user,
                            author_role=_role(request.user),
                            message=f"Update: {subspeciality_change_note}",
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

                    if message_recipient:
                        notification = send_direct_user_message(
                            sender=request.user,
                            recipient=message_recipient,
                            title=message_subject,
                            message=message_body,
                            target_url=_direct_message_target_url(message_recipient, exam.id),
                        )
                        if notification is None:
                            direct_message_error = "Protocol was saved, but direct message could not be sent."
                        else:
                            recipient_name = _display_name(message_recipient) or message_recipient.username
                            ProtocolComment.objects.create(
                                assignment=assignment,
                                author=request.user,
                                author_role=_role(request.user),
                                message=(
                                    f"Direct message sent to {recipient_name}: "
                                    f"{message_subject}"
                                ),
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
                    if not direct_message_error:
                        query_parts = ["saved=1"]
                        if message_recipient:
                            query_parts.append("message_sent=1")
                        return redirect(
                            f'{reverse("protocoling-radiologist-review", args=[exam.id])}?{"&".join(query_parts)}'
                        )

                    saved = True

    timeline_events = _build_assignment_timeline(assignment, exam=exam)

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
        "protocol_preview_map": protocol_preview_map,
        "initial_preview_protocol_id": initial_preview_protocol_id,
        "exam_body_region": resolved_body_region,
        "subspeciality_pool": SUBSPECIALITY_POOL,
        "current_subspeciality": current_subspeciality,
        "inferred_subspeciality": inferred_subspeciality,
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
    if not can_access_facility(request.user, exam.facility_id):
        return HttpResponseForbidden("Not allowed for this facility")

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
                "timeline_events": _build_assignment_timeline(None, exam=exam),
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

    timeline_events = _build_assignment_timeline(assignment, exam=exam)

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
    if not can_access_facility(request.user, exam.facility_id):
        return HttpResponseForbidden("Not allowed for this facility")

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
