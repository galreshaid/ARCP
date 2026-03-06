from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel, Exam, Modality


class QCSessionStatus(models.TextChoices):
    SAVED = "SAVED", _("Saved")
    DRAFT = "DRAFT", _("Draft")
    ACKNOWLEDGED = "ACKNOWLEDGED", _("Acknowledged")
    REPLIED = "REPLIED", _("Replied")
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")


class QCSession(BaseModel):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="qc_sessions",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qc_sessions",
    )
    accession_number = models.CharField(_("Accession Number"), max_length=100, db_index=True)
    mrn = models.CharField(_("MRN"), max_length=100, blank=True, db_index=True)
    modality_code = models.CharField(_("Modality"), max_length=16, blank=True)
    study_name = models.CharField(_("Study"), max_length=255, blank=True)

    checklist_state = models.JSONField(_("Checklist State"), default=dict, blank=True)
    notes = models.TextField(_("Notes"), blank=True)
    concern_raised = models.BooleanField(_("Concern Raised"), default=False, db_index=True)

    status = models.CharField(
        _("Session Status"),
        max_length=16,
        choices=QCSessionStatus.choices,
        default=QCSessionStatus.DRAFT,
        db_index=True,
    )
    submitted_at = models.DateTimeField(_("Submitted At"), null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = _("QC Session")
        verbose_name_plural = _("QC Sessions")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["exam", "-created_at"], name="qc_session_exam_created_idx"),
            models.Index(fields=["reviewer", "-created_at"], name="qc_session_rev_created_idx"),
            models.Index(fields=["accession_number", "-created_at"], name="qc_session_acc_created_idx"),
            models.Index(fields=["status", "-created_at"], name="qc_session_status_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.accession_number} ({self.status})"

    def mark_submitted(self, status: str):
        self.status = status
        self.submitted_at = timezone.now()
        self.save(update_fields=["status", "submitted_at"])


class QCChecklist(BaseModel):
    modality = models.ForeignKey(
        Modality,
        on_delete=models.CASCADE,
        related_name="qc_checklists",
    )
    key = models.SlugField(_("Checklist Key"), max_length=80)
    label = models.CharField(_("Checklist Label"), max_length=200)
    help_text = models.TextField(_("Help Text"), blank=True)
    is_required = models.BooleanField(_("Required"), default=True)
    is_active = models.BooleanField(_("Is Active"), default=True)
    sort_order = models.PositiveSmallIntegerField(_("Sort Order"), default=10, db_index=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("QC Checklist Item")
        verbose_name_plural = _("QC Checklist Items")
        ordering = ["modality__code", "sort_order", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["modality", "key"],
                name="uq_qc_checklist_modality_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["modality", "is_active", "sort_order"],
                name="qc_check_mod_act_sort_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.modality.code} - {self.label}"


class QCImage(BaseModel):
    session = models.ForeignKey(
        QCSession,
        on_delete=models.CASCADE,
        related_name="images",
    )
    accession_number = models.CharField(_("Accession Number"), max_length=100, db_index=True)
    image = models.FileField(_("PNG Evidence"), upload_to="evidence/qc/%Y/%m/%d/")
    original_filename = models.CharField(_("Original Filename"), max_length=255, blank=True)
    pacs_link = models.CharField(_("PACS Link"), max_length=500, blank=True)
    width = models.PositiveIntegerField(_("Width"), null=True, blank=True)
    height = models.PositiveIntegerField(_("Height"), null=True, blank=True)
    capture_order = models.PositiveIntegerField(_("Capture Order"), default=1, db_index=True)

    class Meta:
        verbose_name = _("QC Image")
        verbose_name_plural = _("QC Images")
        ordering = ["session", "capture_order", "created_at"]
        indexes = [
            models.Index(fields=["session", "capture_order"], name="qc_image_session_order_idx"),
            models.Index(fields=["accession_number", "-created_at"], name="qc_image_acc_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.accession_number} image #{self.capture_order}"


class AnnotationTool(models.TextChoices):
    ARROW = "ARROW", _("Arrow")
    CIRCLE = "CIRCLE", _("Circle")
    FREE_DRAW = "FREE_DRAW", _("Free Draw")
    TEXT = "TEXT", _("Text")


class QCAnnotation(BaseModel):
    image = models.ForeignKey(
        QCImage,
        on_delete=models.CASCADE,
        related_name="annotations",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qc_annotations",
    )
    tool = models.CharField(_("Tool"), max_length=20, choices=AnnotationTool.choices)
    payload = models.JSONField(_("Payload"), default=dict, blank=True)
    text_note = models.TextField(_("Text Note"), blank=True)
    color = models.CharField(_("Color"), max_length=16, blank=True)
    stroke_width = models.PositiveSmallIntegerField(_("Stroke Width"), default=2)

    class Meta:
        verbose_name = _("QC Annotation")
        verbose_name_plural = _("QC Annotations")
        ordering = ["image", "created_at"]
        indexes = [
            models.Index(fields=["image", "created_at"], name="qc_annot_image_created_idx"),
            models.Index(fields=["tool", "created_at"], name="qc_annot_tool_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.tool} on {self.image_id}"


class QCDecision(models.TextChoices):
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")


class QCResult(BaseModel):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="qc_results",
    )
    session = models.OneToOneField(
        QCSession,
        on_delete=models.CASCADE,
        related_name="result",
    )
    decision = models.CharField(_("Decision"), max_length=16, choices=QCDecision.choices, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qc_results",
    )
    reviewed_at = models.DateTimeField(_("Reviewed At"), default=timezone.now, db_index=True)
    checklist_results = models.JSONField(_("Checklist Results"), default=dict, blank=True)
    summary = models.TextField(_("Summary"), blank=True)

    class Meta:
        verbose_name = _("QC Result")
        verbose_name_plural = _("QC Results")
        ordering = ["-reviewed_at", "-created_at"]
        indexes = [
            models.Index(fields=["exam", "-reviewed_at"], name="qc_result_exam_reviewed_idx"),
            models.Index(fields=["decision", "-reviewed_at"], name="qc_result_decision_review_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.exam.accession_number} - {self.decision}"
