"""
Core Models
Base models for AAML RadCore Platform
"""

import uuid
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.constants import PROTOCOL_REQUIRED_MODALITY_CODES


# ============================================================
# Abstract Base Models
# ============================================================

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(_('Created At'), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_('Updated At'), auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class BaseModel(TimeStampedModel, UUIDModel):
    class Meta:
        abstract = True


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def get_by_natural_key(self, *args):
        # Use unfiltered manager for natural-key lookup so fixtures can resolve
        # soft-deleted rows when needed.
        if hasattr(self.model, "all_objects"):
            return self.model.all_objects.get_by_natural_key(*args)
        return super().get_by_natural_key(*args)


class FacilityManager(models.Manager):
    def get_by_natural_key(self, code):
        return self.get(code=code)


class ModalityManager(models.Manager):
    def get_by_natural_key(self, code):
        return self.get(code=code)


class ExamAllManager(models.Manager):
    def get_by_natural_key(self, accession_number):
        return self.get(accession_number=accession_number)


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(_('Deleted At'), null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(app_label)s_%(class)s_deleted_by',
    )

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def soft_delete(self, user=None):
        from django.utils import timezone
        self.deleted_at = timezone.now()
        if user:
            self.deleted_by = user
        self.save(update_fields=['deleted_at', 'deleted_by'])

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=['deleted_at', 'deleted_by'])

    @property
    def is_deleted(self):
        return self.deleted_at is not None


# ============================================================
# Facility & Modality
# ============================================================

class Facility(BaseModel):
    objects = FacilityManager()

    code = models.CharField(_('Facility Code'), max_length=50, unique=True)
    name = models.CharField(_('Facility Name'), max_length=200)

    hl7_facility_id = models.CharField(_('HL7 Facility ID'), max_length=100, blank=True)

    address = models.TextField(_('Address'), blank=True)
    contact_email = models.EmailField(_('Contact Email'), blank=True)
    contact_phone = models.CharField(_('Contact Phone'), max_length=50, blank=True)
    qc_service_desk_email = models.EmailField(
        _('QC Service Desk Email'),
        blank=True,
        default='helpdesk@Aaml.com.sa',
    )

    is_active = models.BooleanField(_('Is Active'), default=True)

    config_json = models.JSONField(_('Configuration'), default=dict, blank=True)

    class Meta:
        verbose_name = _('Facility')
        verbose_name_plural = _('Facilities')
        ordering = ['name']

    def __str__(self):
        return f"{self.code} - {self.name}"

    def natural_key(self):
        return (self.code,)


class Modality(BaseModel):
    objects = ModalityManager()

    code = models.CharField(_('Modality Code'), max_length=10, unique=True)
    name = models.CharField(_('Modality Name'), max_length=100)
    description = models.TextField(_('Description'), blank=True)

    is_active = models.BooleanField(_('Is Active'), default=True)

    requires_qc = models.BooleanField(_('Requires QC'), default=True)
    requires_contrast = models.BooleanField(_('Requires Contrast & Materials'), default=True)
    qc_checklist_template = models.JSONField(_('QC Checklist Template'), default=dict, blank=True)

    class Meta:
        verbose_name = _('Modality')
        verbose_name_plural = _('Modalities')
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"

    def natural_key(self):
        return (self.code,)


# ============================================================
# Procedure Dictionary
# ============================================================

class BodyRegion(models.TextChoices):
    HEAD = "Head", _("Head")
    NECK = "Neck", _("Neck")
    CHEST = "Chest", _("Chest")
    ABDOMEN = "Abdomen", _("Abdomen")
    PELVIS = "Pelvis", _("Pelvis")
    SPINE = "Spine", _("Spine")
    UPPER_EXTREMITY = "Upper Extremity", _("Upper Extremity")
    LOWER_EXTREMITY = "Lower Extremity", _("Lower Extremity")
    BREAST = "Breast", _("Breast")
    MULTI_AREA = "Multi Area", _("Multi Area")
    BODY = "Body", _("Body")
    NONSPECIFIC = "Nonspecific", _("Nonspecific")


class Procedure(BaseModel):
    """
    RIS / GERIS Procedure Dictionary
    Maps OBR-4 → Known Procedure
    """

    code = models.CharField(_('Procedure Code'), max_length=50, unique=True, db_index=True)
    name = models.CharField(_('Procedure Name'), max_length=255)

    body_region = models.CharField(
        _('Body Region'),
        max_length=40,
        choices=BodyRegion.choices,
        default=BodyRegion.NONSPECIFIC,
        db_index=True,
    )

    modality = models.ForeignKey(
        Modality,
        on_delete=models.PROTECT,
        related_name='procedures',
    )

    is_active = models.BooleanField(_('Is Active'), default=True, db_index=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)

    class Meta:
        verbose_name = _('Procedure')
        verbose_name_plural = _('Procedures')
        ordering = ['modality__code', 'code']
        indexes = [
            models.Index(fields=['modality', 'body_region', 'is_active']),
            models.Index(fields=['code', 'is_active']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


# ============================================================
# Exam / Order
# ============================================================

class ExamStatus(models.TextChoices):
    ORDER = 'ORDER', _('Order')
    SCHEDULED = 'SCHEDULED', _('Scheduled')
    ARRIVED = 'ARRIVED', _('Patient Arrived')
    IN_PROGRESS = 'IN_PROGRESS', _('In Progress')
    COMPLETED = 'COMPLETED', _('Completed')
    CANCELLED = 'CANCELLED', _('Canceled')
    NO_SHOW = 'NO_SHOW', _('No Show')


class Exam(BaseModel, SoftDeleteModel):
    all_objects = ExamAllManager()

    accession_number = models.CharField(_('Accession Number'), max_length=100, unique=True, db_index=True)
    order_id = models.CharField(_('Order ID'), max_length=100, db_index=True)
    mrn = models.CharField(_('MRN'), max_length=100, db_index=True)

    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='exams')
    modality = models.ForeignKey(Modality, on_delete=models.PROTECT, related_name='exams')

    procedure_code = models.CharField(_('Procedure Code'), max_length=50, blank=True)
    procedure_name = models.CharField(_('Procedure Name'), max_length=200)

    patient_name = models.CharField(_('Patient Name'), max_length=200)
    patient_dob = models.DateField(_('Date of Birth'), null=True, blank=True)
    patient_gender = models.CharField(_('Gender'), max_length=1, blank=True)

    clinical_history = models.TextField(_('Clinical History'), blank=True)
    reason_for_exam = models.TextField(_('Reason for Exam'), blank=True)

    scheduled_datetime = models.DateTimeField(_('Scheduled DateTime'), null=True, blank=True, db_index=True)
    exam_datetime = models.DateTimeField(_('Exam DateTime'), null=True, blank=True, db_index=True)

    ordering_provider = models.CharField(_('Ordering Provider'), max_length=200, blank=True)
    technologist = models.CharField(_('Technologist'), max_length=200, blank=True)

    status = models.CharField(
        _('Status'),
        max_length=20,
        choices=ExamStatus.choices,
        default=ExamStatus.SCHEDULED,
        db_index=True,
    )

    hl7_message_control_id = models.CharField(_('HL7 Message Control ID'), max_length=100, blank=True)
    raw_hl7_message = models.TextField(_('Raw HL7 Message'), blank=True)

    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)

    class Meta:
        verbose_name = _('Exam')
        verbose_name_plural = _('Exams')
        ordering = ['-exam_datetime', '-scheduled_datetime']
        indexes = [
            models.Index(fields=['facility', 'status', '-exam_datetime']),
            models.Index(fields=['modality', 'status']),
            models.Index(fields=['mrn', '-exam_datetime']),
        ]

    def __str__(self):
        return f"{self.accession_number} - {self.procedure_name}"

    def natural_key(self):
        return (self.accession_number,)

    natural_key.dependencies = ["core.facility", "core.modality"]

    @property
    def has_qc(self):
        if hasattr(self, 'qc_evaluation'):
            return True
        if hasattr(self, 'qc_sessions'):
            return self.qc_sessions.exists()
        return False

    @property
    def has_contrast(self):
        if hasattr(self, 'contrast_usages'):
            return self.contrast_usages.exists()
        return False

    @property
    def has_protocol(self):
        return hasattr(self, 'protocol_assignment')

    @staticmethod
    def _normalize_patient_class(value):
        normalized = str(value or '').strip().upper()
        mapping = {
            'I': 'IP',
            'IP': 'IP',
            'INPATIENT': 'IP',
            'O': 'O',
            'OP': 'O',
            'OUTPATIENT': 'O',
            'E': 'E',
            'ER': 'E',
            'ED': 'E',
            'EMERGENCY': 'E',
            'A': 'A',
            'AMBULATORY': 'A',
            'P': 'P',
            'PREADMIT': 'P',
            'PRE-ADMIT': 'P',
            'U': 'U',
            'UNKNOWN': 'U',
            'B': 'B',
            'OB': 'B',
            'OBSTETRICS': 'B',
            'S': 'S',
            'PSYCH': 'S',
            'PSYCHIATRIC': 'S',
            'K': 'K',
            'NEWBORN': 'K',
        }
        return mapping.get(normalized, 'U' if normalized in {'', 'UNK', 'UN'} else normalized)

    @staticmethod
    def _patient_class_label(code):
        labels = {
            'IP': 'Inpatient',
            'O': 'Outpatient',
            'E': 'Emergency',
            'A': 'Ambulatory',
            'P': 'Preadmit',
            'U': 'Unknown',
            'B': 'Obstetrics',
            'S': 'Psychiatric',
            'K': 'Newborn',
        }
        return labels.get(str(code or '').strip().upper(), '')

    @property
    def patient_class(self):
        metadata = dict(self.metadata or {})

        candidates = [
            metadata.get('hl7_patient_class'),
            ((metadata.get('hl7_payload') or {}).get('visit') or {}).get('patient_class'),
            ((metadata.get('hl7_response_payload') or {}).get('visit') or {}).get('patient_class'),
        ]

        for candidate in candidates:
            normalized = self._normalize_patient_class(candidate)
            if normalized:
                return normalized

        return ''

    @property
    def patient_class_display(self):
        return self._patient_class_label(self.patient_class) or '—'

    @property
    def protocol_workflow_status(self):
        metadata = dict(self.metadata or {})
        explicit_status = str(metadata.get('protocol_workflow_status') or '').strip().upper()
        if explicit_status:
            return explicit_status

        if metadata.get('protocol_not_required') and not self.has_protocol:
            return 'NOT_REQUIRED'

        if self.status == ExamStatus.COMPLETED and not self.has_protocol:
            return 'CLOSED'

        if not self.has_protocol:
            return 'UNASSIGNED'

        assignment = getattr(self, 'protocol_assignment', None)
        if self.status == ExamStatus.COMPLETED and assignment and assignment.status != 'DONE':
            return 'DONE'

        return str(getattr(assignment, 'status', '') or 'ASSIGNED').strip().upper()

    @property
    def supports_protocol_workflow(self):
        modality = getattr(self, 'modality', None)
        if not modality or not getattr(modality, 'is_active', False):
            return False

        return str(getattr(modality, 'code', '') or '').strip().upper() in PROTOCOL_REQUIRED_MODALITY_CODES

    @staticmethod
    def _parse_icd_payload(value):
        parts = str(value or '').split('^')
        code = parts[0].strip() if len(parts) > 0 else ''
        description = parts[1].strip() if len(parts) > 1 else ''
        coding_system = parts[2].strip() if len(parts) > 2 else ''

        if description.endswith('-'):
            description = description[:-1].strip()
        else:
            description = re.sub(r'\s+-\s*$', '', description).strip()

        return code, description, coding_system

    def _icd_10_components(self):
        metadata = dict(self.metadata or {})
        code = str(metadata.get('hl7_icd10_code') or '').strip()
        description = str(metadata.get('hl7_icd10_description') or '').strip()
        coding_system = str(metadata.get('hl7_icd10_system') or '').strip()

        if code or description:
            return code, description, coding_system

        payload_candidates = [
            ((metadata.get('hl7_payload') or {}).get('observation_request') or {}).get('reason_for_study'),
            ((metadata.get('hl7_response_payload') or {}).get('observation_request') or {}).get('reason_for_study'),
        ]

        for raw_value in payload_candidates:
            parsed_code, parsed_description, parsed_system = self._parse_icd_payload(raw_value)
            if parsed_code or parsed_description:
                return parsed_code, parsed_description, parsed_system

        return '', '', ''

    @property
    def icd_10_code(self):
        return self._icd_10_components()[0]

    @property
    def icd_10_description(self):
        code, description, _ = self._icd_10_components()
        if code:
            from apps.core.services.icd10_lookup import lookup_icd10_description

            mapped_description = lookup_icd10_description(code)
            if mapped_description:
                return mapped_description

        return description


class ContrastRoute(models.TextChoices):
    IV = "IV", _("IV")
    ORAL = "ORAL", _("Oral")


class MaterialUnit(models.TextChoices):
    ML = "ml", _("mL")
    MG = "mg", _("mg")
    CC = "cc", _("cc")


class MaterialMeasurement(BaseModel):
    code = models.CharField(_("Measurement Code"), max_length=30, unique=True, db_index=True)
    label = models.CharField(_("Measurement Label"), max_length=80)
    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Material Measurement")
        verbose_name_plural = _("Material Measurements")
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.label}"


class MaterialCategory(models.TextChoices):
    CONTRAST = "CONTRAST", _("Contrast")
    DISPOSABLE = "DISPOSABLE", _("Disposable")
    OTHER = "OTHER", _("Other")


class MaterialCatalog(BaseModel):
    material_code = models.CharField(
        _("Material Code"),
        max_length=40,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    name = models.CharField(_("Material Name"), max_length=120, unique=True, db_index=True)
    category = models.CharField(_("Category"), max_length=80, default=MaterialCategory.DISPOSABLE, db_index=True)
    unit = models.CharField(_("Unit"), max_length=30, blank=True)
    pack_size = models.CharField(_("Pack Size"), max_length=40, blank=True)
    modality_scope = models.CharField(_("Modality Scope"), max_length=120, blank=True)
    procedure_mapping_tags = models.TextField(_("Procedure Mapping Tags"), blank=True)
    charge_code = models.CharField(_("Charge Code"), max_length=80, blank=True, db_index=True)
    billing_ref_example = models.CharField(_("Billing Ref Example"), max_length=80, blank=True)
    nphies_code = models.CharField(_("NPHIES Code"), max_length=80, blank=True, db_index=True)
    typical_cost_sar = models.DecimalField(_("Typical Cost (SAR)"), max_digits=12, decimal_places=3, null=True, blank=True)
    default_price_sar = models.DecimalField(_("Default Price (SAR)"), max_digits=12, decimal_places=3, null=True, blank=True)
    billable = models.BooleanField(_("Billable"), default=True, db_index=True)
    cost_center_only = models.BooleanField(_("Cost Center Only"), default=False, db_index=True)
    reorder_level = models.PositiveIntegerField(_("Reorder Level"), default=0)
    notes = models.TextField(_("Notes"), blank=True)
    default_measurement = models.ForeignKey(
        MaterialMeasurement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="materials",
    )
    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Material Catalog Item")
        verbose_name_plural = _("Material Catalog Items")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["category", "is_active"]),
            models.Index(fields=["material_code", "is_active"]),
            models.Index(fields=["modality_scope", "is_active"]),
        ]

    def __str__(self):
        if self.material_code:
            return f"{self.material_code} - {self.name}"
        return self.name


class ProcedureMaterialBundle(BaseModel):
    procedure = models.ForeignKey(
        Procedure,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="material_bundles",
    )
    procedure_code = models.CharField(_("Procedure Code"), max_length=50, db_index=True)
    procedure_name = models.CharField(_("Procedure Name"), max_length=255, blank=True)
    modality_scope = models.CharField(_("Modality Scope"), max_length=120, blank=True)
    rules_notes = models.TextField(_("Rules / Notes"), blank=True)
    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Procedure Material Bundle")
        verbose_name_plural = _("Procedure Material Bundles")
        ordering = ["procedure_code"]
        constraints = [
            models.UniqueConstraint(fields=["procedure_code"], name="uq_core_proc_bundle_code"),
        ]
        indexes = [
            models.Index(fields=["procedure_code", "is_active"]),
            models.Index(fields=["modality_scope", "is_active"]),
        ]

    def __str__(self):
        return f"{self.procedure_code} - {self.procedure_name or 'Bundle'}"


class ProcedureMaterialBundleItem(BaseModel):
    bundle = models.ForeignKey(
        ProcedureMaterialBundle,
        on_delete=models.CASCADE,
        related_name="items",
    )
    material = models.ForeignKey(
        MaterialCatalog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bundle_items",
    )
    material_code = models.CharField(_("Material Code"), max_length=40, blank=True, db_index=True)
    quantity = models.DecimalField(_("Quantity"), max_digits=10, decimal_places=3, default=Decimal("1.000"))
    sort_order = models.PositiveSmallIntegerField(_("Sort Order"), default=10, db_index=True)
    is_optional = models.BooleanField(_("Is Optional"), default=False)
    notes = models.CharField(_("Notes"), max_length=255, blank=True)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Procedure Material Bundle Item")
        verbose_name_plural = _("Procedure Material Bundle Items")
        ordering = ["bundle", "sort_order", "id"]
        indexes = [
            models.Index(fields=["bundle", "sort_order"]),
            models.Index(fields=["material_code"]),
        ]

    def __str__(self):
        label = self.material.name if self.material else self.material_code
        return f"{self.bundle.procedure_code} - {label} x {self.quantity}"


class ContrastUsage(BaseModel):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="contrast_usages",
    )
    pec_number = models.CharField(_("PEC Number"), max_length=100, blank=True, db_index=True)

    contrast_name = models.CharField(_("Contrast Name"), max_length=120)
    concentration_mg_ml = models.DecimalField(_("Concentration (mg/mL)"), max_digits=10, decimal_places=3)
    volume_ml = models.DecimalField(_("Volume (mL)"), max_digits=10, decimal_places=3)
    total_mg = models.DecimalField(_("Total (mg)"), max_digits=12, decimal_places=3, default=Decimal("0.000"))
    injection_rate_ml_s = models.DecimalField(
        _("Injection Rate (mL/s)"),
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
    )
    route = models.CharField(_("Route"), max_length=10, choices=ContrastRoute.choices, default=ContrastRoute.IV)
    lot_number = models.CharField(_("Lot Number"), max_length=100, blank=True)
    expiry_date = models.DateField(_("Expiry Date"), null=True, blank=True)
    patient_weight_kg = models.DecimalField(
        _("Patient Weight (kg)"),
        max_digits=7,
        decimal_places=3,
        null=True,
        blank=True,
    )
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Contrast Usage")
        verbose_name_plural = _("Contrast Usages")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["exam", "-created_at"], name="core_contrast_exam_created_idx"),
            models.Index(fields=["pec_number", "-created_at"], name="core_contrast_pec_created_idx"),
            models.Index(fields=["contrast_name", "-created_at"], name="core_contrast_name_created_idx"),
        ]

    def __str__(self):
        return f"{self.exam.accession_number} - {self.contrast_name}"

    @staticmethod
    def _to_decimal(value) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    def _resolved_patient_weight_kg(self) -> Decimal:
        if self.patient_weight_kg is not None:
            return self._to_decimal(self.patient_weight_kg)

        metadata = dict(getattr(self.exam, "metadata", {}) or {})
        return self._to_decimal(
            metadata.get("patient_weight_kg")
            or metadata.get("weight_kg")
            or metadata.get("patientWeightKg")
            or "0"
        )

    def clean(self):
        errors = {}

        concentration = self._to_decimal(self.concentration_mg_ml)
        volume = self._to_decimal(self.volume_ml)
        if concentration <= 0:
            errors["concentration_mg_ml"] = _("Concentration must be greater than zero.")
        if volume <= 0:
            errors["volume_ml"] = _("Volume must be greater than zero.")

        computed_total = concentration * volume
        self.total_mg = computed_total.quantize(Decimal("0.001")) if computed_total >= 0 else Decimal("0.000")

        if self.exam_id:
            patient_weight = self._resolved_patient_weight_kg()
            max_dose_per_kg = self._to_decimal(getattr(settings, "CONTRAST_MAX_DOSE_MG_PER_KG", 700))
            if patient_weight > 0 and max_dose_per_kg > 0:
                max_allowed_mg = (patient_weight * max_dose_per_kg).quantize(Decimal("0.001"))
                existing_total = (
                    ContrastUsage.objects.filter(exam_id=self.exam_id)
                    .exclude(pk=self.pk)
                    .aggregate(total=models.Sum("total_mg"))
                    .get("total")
                    or Decimal("0")
                )
                cumulative_total = self._to_decimal(existing_total) + self.total_mg
                if cumulative_total > max_allowed_mg:
                    errors["total_mg"] = _(
                        "Cumulative contrast dose exceeds the maximum allowed by patient weight."
                    )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.pec_number and self.exam_id:
            self.pec_number = str(getattr(self.exam, "order_id", "") or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def count_for_exam(cls, exam_or_id) -> int:
        exam_id = getattr(exam_or_id, "id", exam_or_id)
        return cls.objects.filter(exam_id=exam_id).count()

    @classmethod
    def count_for_pec(cls, pec_number: str) -> int:
        reference = str(pec_number or "").strip()
        if not reference:
            return 0
        return cls.objects.filter(pec_number__iexact=reference).count()


class MaterialUsage(BaseModel):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="material_usages",
    )
    pec_number = models.CharField(_("PEC Number"), max_length=100, blank=True, db_index=True)

    material_item = models.ForeignKey(
        MaterialCatalog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usages",
    )
    material_name = models.CharField(_("Material Name"), max_length=120, blank=True)
    measurement = models.ForeignKey(
        MaterialMeasurement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usages",
    )
    unit = models.CharField(_("Unit"), max_length=40, blank=True)
    quantity = models.DecimalField(_("Quantity"), max_digits=10, decimal_places=3)
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Material Usage")
        verbose_name_plural = _("Material Usages")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["exam", "-created_at"], name="core_material_exam_created_idx"),
            models.Index(fields=["pec_number", "-created_at"], name="core_material_pec_created_idx"),
            models.Index(fields=["material_name", "-created_at"], name="core_material_name_created_idx"),
        ]

    def __str__(self):
        return f"{self.exam.accession_number} - {self.material_name}"

    def clean(self):
        try:
            quantity = Decimal(str(self.quantity or 0))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({"quantity": _("Quantity must be a valid number.")})
        if quantity <= 0:
            raise ValidationError({"quantity": _("Quantity must be greater than zero.")})

        if self.material_item and not self.material_name:
            self.material_name = str(self.material_item.name or "").strip()

        if self.measurement:
            self.unit = str(self.measurement.code or "").strip()

        if not str(self.material_name or "").strip():
            raise ValidationError({"material_name": _("Material name is required.")})

        if not str(self.unit or "").strip():
            raise ValidationError({"unit": _("Measurement unit is required.")})

    def save(self, *args, **kwargs):
        if not self.pec_number and self.exam_id:
            self.pec_number = str(getattr(self.exam, "order_id", "") or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def count_for_exam(cls, exam_or_id) -> int:
        exam_id = getattr(exam_or_id, "id", exam_or_id)
        return cls.objects.filter(exam_id=exam_id).count()

    @classmethod
    def count_for_pec(cls, pec_number: str) -> int:
        reference = str(pec_number or "").strip()
        if not reference:
            return 0
        return cls.objects.filter(pec_number__iexact=reference).count()
