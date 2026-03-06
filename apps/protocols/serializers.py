"""
Protocol Serializers
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.protocols.models import (
    ProtocolTemplate,
    ProtocolAssignment,
)
from apps.core.models import Exam, Modality, Facility
from apps.users.models import User


# ============================================================
# Core Reference Serializers
# ============================================================

class ModalitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Modality
        fields = ["id", "code", "name"]


class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = ["id", "code", "name"]


class UserSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "full_name", "role"]


# ============================================================
# Protocol Templates
# ============================================================

class ProtocolTemplateListSerializer(serializers.ModelSerializer):
    modality = ModalitySerializer(read_only=True)
    facility = FacilitySerializer(read_only=True)

    class Meta:
        model = ProtocolTemplate
        fields = [
            "id",
            "code",
            "name",
            "modality",
            "facility",
            "body_region",
            "is_active",
            "priority",
            "requires_contrast",
        ]


class ProtocolTemplateDetailSerializer(serializers.ModelSerializer):
    modality = ModalitySerializer(read_only=True)
    facility = FacilitySerializer(read_only=True)

    class Meta:
        model = ProtocolTemplate
        fields = [
            "id",
            "code",
            "name",
            "modality",
            "facility",
            "body_region",
            "priority",
            "requires_contrast",
            "contrast_type",
            "contrast_phase",
            "contrast_notes",
            "indications",
            "patient_prep",
            "contraindications",
            "safety_notes",
            "post_processing",
            "general_notes",
            "clinical_keywords",
            "metadata",
            "is_active",
            "created_at",
            "updated_at",
        ]


# ============================================================
# Exam Summary
# ============================================================

class ExamSummarySerializer(serializers.ModelSerializer):
    modality = ModalitySerializer(read_only=True)
    facility = FacilitySerializer(read_only=True)

    class Meta:
        model = Exam
        fields = [
            "id",
            "accession_number",
            "order_id",
            "mrn",
            "patient_name",
            "patient_dob",
            "patient_gender",
            "modality",
            "facility",
            "procedure_code",
            "procedure_name",
            "clinical_history",
            "reason_for_exam",
            "scheduled_datetime",
            "exam_datetime",
            "status",
        ]


# ============================================================
# Protocol Suggestions (Non-model)
# ============================================================

class ProtocolSuggestionSerializer(serializers.Serializer):
    protocol = ProtocolTemplateListSerializer()
    score = serializers.FloatField()
    match_percent = serializers.IntegerField()
    rank = serializers.IntegerField()
    reasoning = serializers.JSONField()


# ============================================================
# Protocol Assignment
# ============================================================

class ProtocolAssignmentSerializer(serializers.ModelSerializer):
    exam = ExamSummarySerializer(read_only=True)
    protocol = ProtocolTemplateListSerializer(read_only=True)
    assigned_by = UserSummarySerializer(read_only=True)

    exam_id = serializers.UUIDField(write_only=True)
    protocol_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = ProtocolAssignment
        fields = [
            "id",
            "exam",
            "protocol",
            "assigned_by",
            "assignment_method",
            "status",
            "radiologist_note",
            "technologist_note",
            "sent_to_ris_at",
            "ris_ack_at",
            "metadata",
            "created_at",
            "updated_at",
            "exam_id",
            "protocol_id",
        ]
        read_only_fields = [
            "id",
            "assigned_by",
            "sent_to_ris_at",
            "ris_ack_at",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        from apps.protocols.services.assignment import protocol_assignment_service

        exam = Exam.objects.get(id=validated_data.pop("exam_id"))
        protocol = ProtocolTemplate.objects.get(id=validated_data.pop("protocol_id"))
        user = self.context["request"].user

        try:
            return protocol_assignment_service.assign_protocol(
                exam=exam,
                protocol=protocol,
                assigned_by=user,
                assignment_method=validated_data.get("assignment_method", "MANUAL"),
                notes=validated_data.get("radiologist_note", ""),
            )
        except DjangoValidationError as exc:
            messages = list(getattr(exc, "messages", None) or [str(exc)])
            raise serializers.ValidationError({"non_field_errors": messages})


class ProtocolAssignmentUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProtocolAssignment
        fields = ["radiologist_note", "technologist_note", "status"]


# ============================================================
# Analytics / Logs
# ============================================================


# ============================================================
# Deep Link Response
# ============================================================

class ProtocolDeepLinkResponseSerializer(serializers.Serializer):
    exam = serializers.JSONField()
    existing_assignment = serializers.JSONField(allow_null=True)
    suggestions = serializers.ListField()

# ============================================================
# Stats Serializers
# ============================================================

class ProtocolAssignmentStatsSerializer(serializers.Serializer):
    total_assignments = serializers.IntegerField()
    manual_assignments = serializers.IntegerField()
    suggested_assignments = serializers.IntegerField()
    modified_assignments = serializers.IntegerField()
    avg_suggestion_score = serializers.FloatField(allow_null=True)
    suggestion_acceptance_rate = serializers.FloatField()
