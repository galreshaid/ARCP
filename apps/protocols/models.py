"""
Protocol Models
Manual protocol definition, assignment, suggestions (AI), preference learning, and comments.

Goals:
- Admin-driven ProtocolTemplate (master definition)
- ProtocolAssignment = source of truth (Radiologist → Technologist workflow)
- ProtocolSuggestionLog = audit/explainability for AI suggestions
- RadiologistPreference = learning layer per radiologist
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import (
    BaseModel,
    SoftDeleteModel,
    Exam,
    Modality,
    Procedure,
    Facility,
)


# ============================================================
# Enums
# ============================================================

class AssignmentMethod(models.TextChoices):
    AI = "AI", _("AI Suggested")
    MANUAL = "MANUAL", _("Manual")
    OVERRIDE = "OVERRIDE", _("Override AI")


class AssignmentStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    ACKNOWLEDGED = "ACKNOWLEDGED", _("Acknowledged")
    DONE = "DONE", _("Done")
    CANCELLED = "CANCELLED", _("Cancelled")


# ============================================================
# Protocol Template (Master Definition)
# ============================================================

class ProtocolTemplate(BaseModel, SoftDeleteModel):
    """
    Radiology Protocol Definition
    Created and maintained manually from Admin UI
    """

    # Identity
    code = models.CharField(_("Protocol Code"), max_length=80, unique=True, db_index=True)
    name = models.CharField(_("Protocol Name"), max_length=255, db_index=True)

    # Scope (keep facility to match current viewset filtering logic)
    facility = models.ForeignKey(
        Facility,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="protocol_templates",
        help_text=_("If set, protocol is facility-specific. If empty, protocol is global."),
    )

    modality = models.ForeignKey(
        Modality,
        on_delete=models.PROTECT,
        related_name="protocol_templates",
    )

    # Optional linkage to RIS Procedure
    procedure = models.ForeignKey(
        Procedure,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="protocol_templates",
    )

    # For compatibility with existing API filters/search
    body_part = models.CharField(_("Body Part"), max_length=120, blank=True, db_index=True)
    body_region = models.CharField(_("Body Region"), max_length=40, blank=True, db_index=True)

    laterality = models.CharField(_("Laterality"), max_length=20, blank=True)

    # Priority & availability
    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)
    is_default = models.BooleanField(_("Is Default"), default=False, db_index=True)
    priority = models.PositiveIntegerField(_("Priority"), default=50, db_index=True)

    # Usage tracking (needed by suggestion scoring + admin readonly_fields)
    usage_count = models.PositiveIntegerField(_("Usage Count"), default=0, db_index=True)

    # Contrast
    requires_contrast = models.BooleanField(_("Requires Contrast"), default=False)
    contrast_type = models.CharField(_("Contrast Type"), max_length=120, blank=True)
    contrast_phase = models.CharField(_("Contrast Phase"), max_length=120, blank=True)
    contrast_notes = models.TextField(_("Contrast Notes"), blank=True)

    # Clinical & operational notes
    indications = models.TextField(_("Indications"), blank=True)
    patient_prep = models.TextField(_("Patient Preparation"), blank=True)
    contraindications = models.TextField(_("Contraindications"), blank=True)
    safety_notes = models.TextField(_("Safety Notes"), blank=True)
    post_processing = models.TextField(_("Post Processing"), blank=True)
    general_notes = models.TextField(_("General Notes"), blank=True)

    # Structured content (flexible)
    clinical_keywords = models.JSONField(_("Clinical Keywords"), default=list, blank=True)
    technical_parameters = models.JSONField(_("Technical Parameters"), default=dict, blank=True)
    tags = models.JSONField(_("Tags"), default=list, blank=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Protocol Template")
        verbose_name_plural = _("Protocol Templates")
        ordering = ["modality__code", "priority", "code"]
        indexes = [
            models.Index(fields=["modality", "is_active", "priority"]),
            models.Index(fields=["code", "is_active"]),
            models.Index(fields=["facility", "is_active"]),
            models.Index(fields=["body_part"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    def clean(self):
        # If facility is set, it must be active in general (optional business rule)
        # You can add stricter checks later.
        if not self.code.strip():
            raise ValidationError({"code": "Protocol code is required."})

    def increment_usage(self, step: int = 1):
        self.usage_count = (self.usage_count or 0) + int(step)
        self.save(update_fields=["usage_count"])


# ============================================================
# Protocol Sequence (SER Table)
# ============================================================

class ProtocolSequence(BaseModel):
    """
    One sequence row in protocol table
    Matches SER / COIL / PLANE / SEQ / OPTIONS / COMMENTS
    """

    protocol = models.ForeignKey(
        ProtocolTemplate,
        on_delete=models.CASCADE,
        related_name="sequences",
    )

    ser = models.PositiveIntegerField(_("SER (Sequence #)"))

    coil = models.CharField(_("COIL"), max_length=120, blank=True)
    phase_array = models.CharField(_("PH. ARRAY"), max_length=120, blank=True)

    scan_plane = models.CharField(_("SCAN PLANE"), max_length=80)
    pulse_sequence = models.CharField(_("PULSE SEQ."), max_length=120)

    options = models.CharField(_("OPTIONS"), max_length=200, blank=True)
    comments = models.TextField(_("COMMENTS"), blank=True)

    # Optional structured parameters (FOV, TR, TE, TI, slices, phases…)
    parameters = models.JSONField(_("Parameters"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Protocol Sequence")
        verbose_name_plural = _("Protocol Sequences")
        ordering = ["protocol", "ser"]
        constraints = [
            models.UniqueConstraint(fields=["protocol", "ser"], name="uq_protocol_ser")
        ]

    def clean(self):
        errors = {}
        if not self.ser or self.ser <= 0:
            errors["ser"] = "SER is required and must be greater than zero."
        if not (self.scan_plane or "").strip():
            errors["scan_plane"] = "SCAN PLANE is required."
        if not (self.pulse_sequence or "").strip():
            errors["pulse_sequence"] = "PULSE SEQ. is required."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.protocol.code} | SER {self.ser} | {self.scan_plane} | {self.pulse_sequence}"


# ============================================================
# Assignment
# ============================================================

class ProtocolAssignment(BaseModel):
    """
    Actual protocol assignment on an Exam
    Radiologist → Technologist workflow
    """

    exam = models.OneToOneField(
        Exam,
        on_delete=models.CASCADE,
        related_name="protocol_assignment",
    )

    protocol = models.ForeignKey(
        ProtocolTemplate,
        on_delete=models.PROTECT,
        related_name="assignments",
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="protocol_assignments",
    )

    assigned_at = models.DateTimeField(_("Assigned At"), default=timezone.now, db_index=True)

    assignment_method = models.CharField(
        _("Assignment Method"),
        max_length=15,
        choices=AssignmentMethod.choices,
        default=AssignmentMethod.MANUAL,
        db_index=True,
    )

    status = models.CharField(
        _("Status"),
        max_length=15,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.PENDING,
        db_index=True,
    )

    # Notes
    radiologist_note = models.TextField(_("Radiologist Note"), blank=True)
    technologist_note = models.TextField(_("Technologist Note"), blank=True)
    assignment_notes = models.TextField(_("Assignment Notes"), blank=True)

    # Modification tracking (used by assignment service)
    is_modified = models.BooleanField(_("Is Modified"), default=False)
    modifications = models.JSONField(_("Modifications"), default=dict, blank=True)
    modification_notes = models.TextField(_("Modification Notes"), blank=True)

    # AI traceability
    was_suggested = models.BooleanField(_("Was Suggested"), default=False)
    suggestion_rank = models.PositiveIntegerField(_("Suggestion Rank"), null=True, blank=True)
    suggestion_score = models.FloatField(_("Suggestion Score"), null=True, blank=True)

    # Technologist acknowledgement
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_protocols",
    )
    acknowledged_at = models.DateTimeField(_("Acknowledged At"), null=True, blank=True)

    # Notifications
    technologist_notified = models.BooleanField(_("Technologist Notified"), default=False)
    notification_sent_at = models.DateTimeField(_("Notification Sent At"), null=True, blank=True)

    # HL7 / RIS tracking (keep compatible)
    hl7_sent = models.BooleanField(_("HL7 Sent"), default=False)
    hl7_sent_at = models.DateTimeField(_("HL7 Sent At"), null=True, blank=True)
    hl7_message_id = models.CharField(_("HL7 Message ID"), max_length=64, blank=True)

    sent_to_ris_at = models.DateTimeField(_("Sent to RIS At"), null=True, blank=True)
    ris_ack_at = models.DateTimeField(_("RIS Acknowledged At"), null=True, blank=True)

    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Protocol Assignment")
        verbose_name_plural = _("Protocol Assignments")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assignment_method", "status", "-created_at"]),
            models.Index(fields=["assigned_at"]),
            models.Index(fields=["was_suggested"]),
        ]

    def __str__(self) -> str:
        acc = getattr(self.exam, "accession_number", "N/A")
        return f"{acc} → {self.protocol.code}"

    def acknowledge(self, technologist):
        self.status = AssignmentStatus.ACKNOWLEDGED
        self.acknowledged_by = technologist
        self.acknowledged_at = timezone.now()
        self.save(update_fields=["status", "acknowledged_by", "acknowledged_at"])

    def set_ris_sent(self):
        self.sent_to_ris_at = timezone.now()
        self.save(update_fields=["sent_to_ris_at"])

    def set_ris_ack(self):
        self.ris_ack_at = timezone.now()
        self.save(update_fields=["ris_ack_at"])


# ============================================================
# Comments
# ============================================================

class ProtocolComment(BaseModel, SoftDeleteModel):
    """
    Comments on protocol assignment
    Used by radiologists and technologists
    """

    assignment = models.ForeignKey(
        ProtocolAssignment,
        on_delete=models.CASCADE,
        related_name="comments",
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="protocol_comments",
    )

    author_role = models.CharField(_("Author Role"), max_length=40, blank=True)
    message = models.TextField(_("Message"))

    class Meta:
        verbose_name = _("Protocol Comment")
        verbose_name_plural = _("Protocol Comments")
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        acc = getattr(self.assignment.exam, "accession_number", "N/A")
        return f"Comment on {acc}"


# ============================================================
# AI Suggestion Log (Audit / Explainability)
# ============================================================

class ProtocolSuggestionLog(BaseModel):
    """
    Stores AI suggestion results for auditing, analytics, and explainability.
    This should NEVER be removed in production systems.
    """

    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="protocol_suggestion_logs",
    )

    radiologist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="protocol_suggestion_logs",
    )

    suggested_protocols = models.JSONField(_("Suggested Protocols"), default=list)
    clinical_context = models.JSONField(_("Clinical Context"), default=dict)

    top_suggestion = models.ForeignKey(
        ProtocolTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="top_suggestion_logs",
    )

    selected_protocol = models.ForeignKey(
        ProtocolTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="selected_in_logs",
    )

    selected_rank = models.PositiveIntegerField(_("Selected Rank"), null=True, blank=True)
    selected_at = models.DateTimeField(_("Selected At"), null=True, blank=True)

    suggestion_algorithm_version = models.CharField(_("Algorithm Version"), max_length=20, default="1.0")
    suggestion_generated_at = models.DateTimeField(_("Suggestion Generated At"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("Protocol Suggestion Log")
        verbose_name_plural = _("Protocol Suggestion Logs")
        ordering = ["-suggestion_generated_at"]
        indexes = [
            models.Index(fields=["exam", "-suggestion_generated_at"]),
            models.Index(fields=["radiologist", "-suggestion_generated_at"]),
        ]

    def __str__(self) -> str:
        acc = getattr(self.exam, "accession_number", "N/A")
        return f"SuggestionLog {acc} ({self.radiologist_id})"

    def record_selection(self, selected_protocol: ProtocolTemplate, rank: int | None):
        self.selected_protocol = selected_protocol
        self.selected_rank = rank
        self.selected_at = timezone.now()
        self.save(update_fields=["selected_protocol", "selected_rank", "selected_at"])


# ============================================================
# Radiologist Preference (Learning Layer)
# ============================================================

class RadiologistPreference(BaseModel):
    """
    Learns and stores radiologist-specific preferences to influence suggestions.
    """

    radiologist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="radiologist_preferences",
    )

    modality = models.ForeignKey(
        Modality,
        on_delete=models.CASCADE,
        related_name="radiologist_preferences",
    )

    # Optional facility scope
    facility = models.ForeignKey(
        Facility,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="radiologist_preferences",
    )

    body_part = models.CharField(_("Body Part"), max_length=120, blank=True, db_index=True)

    preferred_protocol = models.ForeignKey(
        ProtocolTemplate,
        on_delete=models.CASCADE,
        related_name="preferred_by_radiologists",
    )

    # Stored pattern from clinical context (keywords, procedure name, etc.)
    clinical_pattern = models.JSONField(_("Clinical Pattern"), default=dict, blank=True)

    confidence_score = models.FloatField(_("Confidence Score"), default=0.5, db_index=True)
    selection_count = models.PositiveIntegerField(_("Selection Count"), default=0, db_index=True)
    last_selected_at = models.DateTimeField(_("Last Selected At"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("Radiologist Preference")
        verbose_name_plural = _("Radiologist Preferences")
        ordering = ["-confidence_score", "-last_selected_at"]
        indexes = [
            models.Index(fields=["radiologist", "modality"]),
            models.Index(fields=["radiologist", "modality", "body_part"]),
            models.Index(fields=["preferred_protocol"]),
            models.Index(fields=["facility"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["radiologist", "modality", "body_part", "preferred_protocol", "facility"],
                name="uq_radiologist_pref_context",
            )
        ]

    def __str__(self) -> str:
        return f"{self.radiologist_id} → {self.preferred_protocol.code} ({self.confidence_score:.2f})"

    def increment_selection(self, step: int = 1):
        self.selection_count = (self.selection_count or 0) + int(step)
        self.last_selected_at = timezone.now()
        self.save(update_fields=["selection_count", "last_selected_at"])
