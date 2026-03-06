"""
Protocol Assignment Service
Handles protocol assignment to exams
(SAFE MODE – No AI learning, No logging dependencies)
"""

from typing import Optional, Dict, List
from datetime import timedelta

from django.db import transaction, models
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.protocols.models import (
    ProtocolTemplate,
    ProtocolAssignment,
)
from apps.core.models import Exam
from apps.protocols.services.preference_learning import preference_learning_service


class ProtocolAssignmentService:
    """
    Service for assigning protocols to exams
    """

    # --------------------------------------------------
    # Core Assignment
    # --------------------------------------------------

    @transaction.atomic
    def assign_protocol(
        self,
        exam: Exam,
        protocol: ProtocolTemplate,
        assigned_by,
        assignment_method: str = "MANUAL",
        suggestion_context: Optional[Dict] = None,
        modifications: Optional[Dict] = None,
        notes: str = "",
    ) -> ProtocolAssignment:
        """
        Assign protocol to an exam
        """

        self._validate_assignment(exam, protocol, assigned_by)

        if hasattr(exam, "protocol_assignment"):
            raise ValidationError(
                f"Exam {exam.accession_number} already has a protocol assigned"
            )

        assignment = ProtocolAssignment.objects.create(
            exam=exam,
            protocol=protocol,
            assigned_by=assigned_by,
            assignment_method=assignment_method,
            assignment_notes=notes,
            is_modified=bool(modifications),
            modifications=modifications or {},
        )

        if suggestion_context:
            assignment.was_suggested = True
            assignment.suggestion_rank = suggestion_context.get("rank")
            assignment.suggestion_score = suggestion_context.get("score")
            assignment.save(
                update_fields=[
                    "was_suggested",
                    "suggestion_rank",
                    "suggestion_score",
                ]
            )

        # Increment protocol usage safely
        protocol.increment_usage()

        try:
            preference_learning_service.update_preference(
                radiologist=assigned_by,
                exam=exam,
                selected_protocol=protocol,
                was_suggested=assignment_method == "AI",
            )
        except Exception:
            pass

        return assignment

    # --------------------------------------------------
    # Reassign / Modify
    # --------------------------------------------------

    @transaction.atomic
    def reassign_protocol(
        self,
        assignment: ProtocolAssignment,
        new_protocol: ProtocolTemplate,
        reassigned_by,
        reason: str = "",
    ) -> ProtocolAssignment:
        """
        Reassign exam to a different protocol
        """

        exam = assignment.exam
        self._validate_assignment(exam, new_protocol, reassigned_by)

        assignment.status = "CANCELLED"
        assignment.modification_notes = (
            f"Reassigned to {new_protocol.code}. Reason: {reason}"
        )
        assignment.save()

        return self.assign_protocol(
            exam=exam,
            protocol=new_protocol,
            assigned_by=reassigned_by,
            assignment_method="MANUAL",
            notes=f"Reassigned from {assignment.protocol.code}. {reason}",
        )

    def modify_assignment(
        self,
        assignment: ProtocolAssignment,
        modifications: Dict,
        modified_by,
        notes: str = "",
    ) -> ProtocolAssignment:
        """
        Modify existing assignment
        """

        if not modified_by.has_permission("protocol.edit"):
            raise ValidationError("User does not have permission to modify protocols")

        assignment.is_modified = True
        assignment.modifications.update(modifications)
        assignment.modification_notes = notes
        assignment.save()

        return assignment

    # --------------------------------------------------
    # Technologist ACK
    # --------------------------------------------------

    @transaction.atomic
    def acknowledge_assignment(
        self,
        assignment: ProtocolAssignment,
        technologist,
    ) -> ProtocolAssignment:
        """
        Technologist acknowledges protocol assignment
        """

        from apps.core.constants import UserRole

        if technologist.role != UserRole.TECHNOLOGIST:
            raise ValidationError("Only technologists can acknowledge protocols")

        assignment.acknowledge(technologist)
        return assignment

    # --------------------------------------------------
    # HL7 / Notifications
    # --------------------------------------------------

    def send_hl7_notification(self, assignment: ProtocolAssignment) -> bool:
        """
        Send HL7 ORR message
        """

        from apps.hl7_core.senders.orr_sender import send_protocol_assignment_orr

        try:
            message_id = send_protocol_assignment_orr(assignment)

            assignment.hl7_sent = True
            assignment.hl7_sent_at = timezone.now()
            assignment.hl7_message_id = message_id
            assignment.save(
                update_fields=[
                    "hl7_sent",
                    "hl7_sent_at",
                    "hl7_message_id",
                ]
            )
            return True
        except Exception:
            return False

    def send_technologist_notification(self, assignment: ProtocolAssignment) -> bool:
        """
        Notify technologist
        """

        from apps.communication.services.notification import (
            send_protocol_notification,
        )

        try:
            send_protocol_notification(assignment)

            assignment.technologist_notified = True
            assignment.notification_sent_at = timezone.now()
            assignment.save(
                update_fields=[
                    "technologist_notified",
                    "notification_sent_at",
                ]
            )
            return True
        except Exception:
            return False

    # --------------------------------------------------
    # Queries / Stats
    # --------------------------------------------------

    def get_exam_protocol(self, exam: Exam) -> Optional[ProtocolAssignment]:
        return getattr(exam, "protocol_assignment", None)

    def get_radiologist_assignments(
        self,
        radiologist,
        facility=None,
        days: int = 30,
    ) -> List[ProtocolAssignment]:

        qs = ProtocolAssignment.objects.filter(
            assigned_by=radiologist,
            assigned_at__gte=timezone.now() - timedelta(days=days),
        ).select_related("exam", "protocol")

        if facility:
            qs = qs.filter(exam__facility=facility)

        return list(qs)

    def get_assignment_stats(
        self,
        radiologist=None,
        facility=None,
        days: int = 30,
    ) -> Dict:

        qs = ProtocolAssignment.objects.filter(
            assigned_at__gte=timezone.now() - timedelta(days=days)
        )

        if radiologist:
            qs = qs.filter(assigned_by=radiologist)
        if facility:
            qs = qs.filter(exam__facility=facility)

        stats = qs.aggregate(
            total_assignments=models.Count("id"),
            manual_assignments=models.Count(
                "id",
                filter=models.Q(assignment_method="MANUAL"),
            ),
            suggested_assignments=models.Count(
                "id",
                filter=models.Q(assignment_method="SUGGESTED"),
            ),
            modified_assignments=models.Count(
                "id",
                filter=models.Q(is_modified=True),
            ),
            avg_suggestion_score=models.Avg("suggestion_score"),
        )

        total = stats["total_assignments"] or 0
        stats["suggestion_acceptance_rate"] = (
            stats["suggested_assignments"] / total * 100 if total else 0
        )

        return stats

    # --------------------------------------------------
    # Validation
    # --------------------------------------------------

    def _validate_assignment(
        self,
        exam: Exam,
        protocol: ProtocolTemplate,
        user,
    ):
        if not protocol.is_active:
            raise ValidationError(f"Protocol {protocol.code} is not active")

        if exam.modality != protocol.modality:
            raise ValidationError(
                f"Protocol modality ({protocol.modality.code}) "
                f"does not match exam modality ({exam.modality.code})"
            )

        if protocol.facility and protocol.facility != exam.facility:
            raise ValidationError(
                f"Protocol is specific to {protocol.facility.code} "
                f"but exam is at {exam.facility.code}"
            )

        if not user.has_permission("protocol.assign"):
            raise ValidationError("User does not have permission to assign protocols")

        if not user.has_facility_access(exam.facility):
            raise ValidationError("User does not have access to this facility")


# Singleton
protocol_assignment_service = ProtocolAssignmentService()
