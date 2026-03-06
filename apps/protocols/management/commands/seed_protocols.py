from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import Modality, Procedure
from apps.protocols.models import (
    ProtocolTemplate,
    ProtocolSequence,
)


class Command(BaseCommand):
    help = "Seed initial Protocols, Procedures, and Sequences"

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Seeding Protocol data..."))

        # --------------------------------------------------
        # Modalities
        # --------------------------------------------------
        ct, _ = Modality.objects.get_or_create(
            code="CT",
            defaults={"name": "Computed Tomography"},
        )

        mr, _ = Modality.objects.get_or_create(
            code="MR",
            defaults={"name": "Magnetic Resonance Imaging"},
        )

        # --------------------------------------------------
        # Procedures
        # --------------------------------------------------
        ct_brain, _ = Procedure.objects.get_or_create(
            code="CT_BRAIN",
            modality=ct,
            defaults={
                "name": "CT Brain",
                "body_region": "HEAD",
                "is_active": True,
            },
        )

        ct_chest, _ = Procedure.objects.get_or_create(
            code="CT_CHEST",
            modality=ct,
            defaults={
                "name": "CT Chest",
                "body_region": "CHEST",
                "is_active": True,
            },
        )

        mr_brain, _ = Procedure.objects.get_or_create(
            code="MR_BRAIN",
            modality=mr,
            defaults={
                "name": "MR Brain",
                "body_region": "HEAD",
                "is_active": True,
            },
        )

        # --------------------------------------------------
        # Protocol Templates
        # --------------------------------------------------
        ct_brain_plain, _ = ProtocolTemplate.objects.get_or_create(
            code="CT-BRAIN-PLAIN",
            defaults={
                "name": "CT Brain Plain",
                "modality": ct,
                "procedure": ct_brain,
                "body_region": "HEAD",
                "priority": 10,
                "requires_contrast": False,
                "indications": "Headache, trauma, stroke screening",
                "patient_prep": "Remove metallic objects",
                "safety_notes": "Check pregnancy status",
                "clinical_keywords": [
                    "headache",
                    "trauma",
                    "stroke",
                    "loss of consciousness",
                ],
            },
        )

        ct_chest_pe, _ = ProtocolTemplate.objects.get_or_create(
            code="CT-CHEST-PE",
            defaults={
                "name": "CT Chest Pulmonary Embolism",
                "modality": ct,
                "procedure": ct_chest,
                "body_region": "CHEST",
                "priority": 5,
                "requires_contrast": True,
                "contrast_type": "IV Contrast",
                "contrast_phase": "Pulmonary Arterial Phase",
                "indications": "Suspected pulmonary embolism",
                "patient_prep": "18–20G IV cannula",
                "safety_notes": "Check renal function",
                "clinical_keywords": [
                    "pulmonary embolism",
                    "chest pain",
                    "dyspnea",
                    "hypoxia",
                    "d-dimer",
                ],
            },
        )

        mr_brain_routine, _ = ProtocolTemplate.objects.get_or_create(
            code="MR-BRAIN-ROUTINE",
            defaults={
                "name": "MR Brain Routine",
                "modality": mr,
                "procedure": mr_brain,
                "body_region": "HEAD",
                "priority": 10,
                "requires_contrast": False,
                "indications": "Headache, seizures, follow-up",
                "patient_prep": "Remove metallic objects",
                "safety_notes": "MR safety screening mandatory",
                "clinical_keywords": [
                    "seizure",
                    "headache",
                    "tumor",
                    "follow-up",
                ],
            },
        )

        # --------------------------------------------------
        # Protocol Sequences (SER Table)
        # --------------------------------------------------
        sequences = [
            # CT Brain Plain
            (ct_brain_plain, 1, "HEAD", "Axial Non-Contrast", {"kVp": 120, "slice": "5mm"}),
            (ct_brain_plain, 2, "HEAD", "Bone Algorithm", {"window": "Bone"}),

            # CT Chest PE
            (ct_chest_pe, 1, "CHEST", "Helical Contrast", {"phase": "Pulmonary"}),
            (ct_chest_pe, 2, "CHEST", "MPR Reconstruction", {"planes": ["Axial", "Coronal"]}),

            # MR Brain Routine
            (mr_brain_routine, 1, "HEAD", "T1 Axial", {"TR": 500, "TE": 10}),
            (mr_brain_routine, 2, "HEAD", "T2 Axial", {"TR": 4000, "TE": 90}),
            (mr_brain_routine, 3, "HEAD", "FLAIR Axial", {"TR": 9000, "TE": 110}),
        ]

        for protocol, ser, plane, pulse, params in sequences:
            ProtocolSequence.objects.get_or_create(
                protocol=protocol,
                ser=ser,
                defaults={
                    "scan_plane": plane,
                    "pulse_sequence": pulse,
                    "parameters": params,
                },
            )

        self.stdout.write(self.style.SUCCESS("✅ Protocol seed completed successfully"))
