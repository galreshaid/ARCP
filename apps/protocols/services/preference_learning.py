"""
Preference Learning Service (SAFE MODE)
Learns radiologist protocol preferences over time.

- If RadiologistPreference model is not available yet, this service will NO-OP safely.
- Once you add RadiologistPreference later, learning will work automatically.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.protocols.models import ProtocolTemplate, ProtocolAssignment
from apps.core.models import Exam


def _get_preference_model():
    """
    Import RadiologistPreference lazily to avoid crashing Django startup
    if the model is not yet defined in apps.protocols.models.
    """
    try:
        from apps.protocols.models import RadiologistPreference  # noqa: F401
        return RadiologistPreference
    except Exception:
        return None


class PreferenceLearningService:
    """
    Learns and updates radiologist preferences based on selections over time.
    SAFE MODE: works even if RadiologistPreference isn't defined yet.
    """

    INITIAL_CONFIDENCE = 0.5
    CONFIDENCE_INCREMENT = 0.05
    CONFIDENCE_DECAY = 0.02
    MIN_SELECTIONS_FOR_PATTERN = 3

    # --------------------------------------------------
    # Public API
    # --------------------------------------------------

    def update_preference(
        self,
        radiologist,
        exam: Exam,
        selected_protocol: ProtocolTemplate,
        was_suggested: bool = False,
    ):
        """
        Update or create radiologist preference based on protocol selection.
        If RadiologistPreference model is missing -> NO-OP (returns None).
        """
        RadiologistPreference = _get_preference_model()
        if RadiologistPreference is None:
            return None

        clinical_pattern = self._extract_clinical_pattern(exam)
        body_part = self._normalize_body_part(exam)

        preference, created = RadiologistPreference.objects.get_or_create(
            radiologist=radiologist,
            modality=exam.modality,
            body_part=body_part,
            preferred_protocol=selected_protocol,
            defaults={
                "clinical_pattern": clinical_pattern,
                "facility": exam.facility,
                "confidence_score": self.INITIAL_CONFIDENCE,
                "selection_count": 0,
            },
        )

        if created:
            preference.increment_selection()
        else:
            self._update_existing_preference(
                preference=preference,
                clinical_pattern=clinical_pattern,
                was_suggested=was_suggested,
            )

        self._decay_competing_preferences(
            radiologist=radiologist,
            modality=exam.modality,
            body_part=body_part,
            exclude_preference=preference,
        )

        return preference

    def get_preferences_for_context(
        self,
        radiologist,
        modality,
        body_part: Optional[str] = None,
        facility=None,
    ) -> List:
        """
        Retrieve ranked preferences for a given context.
        If RadiologistPreference model is missing -> returns [].
        """
        RadiologistPreference = _get_preference_model()
        if RadiologistPreference is None:
            return []

        qs = RadiologistPreference.objects.filter(
            radiologist=radiologist,
            modality=modality,
        )

        if body_part:
            qs = qs.filter(body_part__icontains=body_part)

        if facility:
            qs = qs.filter(Q(facility__isnull=True) | Q(facility=facility))

        return list(qs.order_by("-confidence_score", "-last_selected_at"))

    def analyze_radiologist_patterns(
        self,
        radiologist,
        days: int = 90,
    ) -> Dict:
        """
        Analyze radiologist protocol usage patterns (works regardless of preferences model).
        """
        cutoff = timezone.now() - timedelta(days=days)

        assignments = ProtocolAssignment.objects.filter(
            assigned_by=radiologist,
            assigned_at__gte=cutoff,
        ).select_related("protocol", "exam__modality")

        analysis = {
            "total_assignments": assignments.count(),
            "by_modality": [],
            "by_assignment_method": {},
            "most_used_protocols": [],
            "suggestion_acceptance_rate": 0.0,
            "consistency_score": 0.0,
        }

        if not assignments.exists():
            return analysis

        modality_counts = assignments.values(
            "exam__modality__code",
            "exam__modality__name",
        ).annotate(count=Count("id")).order_by("-count")

        analysis["by_modality"] = [
            {
                "modality": m["exam__modality__code"],
                "name": m["exam__modality__name"],
                "count": m["count"],
            }
            for m in modality_counts
        ]

        for row in assignments.values("assignment_method").annotate(count=Count("id")):
            analysis["by_assignment_method"][row["assignment_method"]] = row["count"]

        protocol_counts = assignments.values(
            "protocol__code",
            "protocol__name",
        ).annotate(count=Count("id")).order_by("-count")[:10]

        analysis["most_used_protocols"] = [
            {
                "code": p["protocol__code"],
                "name": p["protocol__name"],
                "count": p["count"],
                "percentage": round(p["count"] / analysis["total_assignments"] * 100, 1),
            }
            for p in protocol_counts
        ]

        total_suggested = assignments.filter(was_suggested=True).count()
        accepted = assignments.filter(was_suggested=True, assignment_method="SUGGESTED").count()
        if total_suggested:
            analysis["suggestion_acceptance_rate"] = round(accepted / total_suggested * 100, 1)

        analysis["consistency_score"] = self._calculate_consistency_score(assignments)
        return analysis

    def prune_stale_preferences(
        self,
        radiologist=None,
        days_threshold: int = 180,
    ) -> int:
        """
        Remove old, low-confidence preferences.
        If RadiologistPreference model is missing -> returns 0.
        """
        RadiologistPreference = _get_preference_model()
        if RadiologistPreference is None:
            return 0

        cutoff = timezone.now() - timedelta(days=days_threshold)

        qs = RadiologistPreference.objects.filter(
            last_selected_at__lt=cutoff,
            confidence_score__lt=0.3,
        )

        if radiologist:
            qs = qs.filter(radiologist=radiologist)

        count = qs.count()
        qs.delete()
        return count

    # --------------------------------------------------
    # Internal helpers
    # --------------------------------------------------

    def _extract_clinical_pattern(self, exam: Exam) -> Dict:
        text = " ".join([exam.clinical_history or "", exam.reason_for_exam or ""]).lower()
        return {
            "keywords": self._extract_keywords(text),
            "procedure_name": exam.procedure_name,
            "body_part": self._normalize_body_part(exam),
        }

    def _extract_keywords(self, text: str, max_keywords: int = 10) -> List[str]:
        terms = [
            "pain", "trauma", "fracture", "mass", "tumor",
            "infection", "bleeding", "obstruction", "stone",
            "screening", "follow-up", "acute", "chronic",
        ]
        return [t for t in terms if t in text][:max_keywords]

    def _normalize_body_part(self, exam: Exam) -> str:
        body_part = (exam.metadata or {}).get("body_part", "")
        if body_part:
            return body_part.lower()

        name = (exam.procedure_name or "").lower()
        mapping = {
            "chest": "chest",
            "abdomen": "abdomen",
            "pelvis": "pelvis",
            "brain": "head",
            "head": "head",
            "spine": "spine",
            "knee": "knee",
            "shoulder": "shoulder",
        }
        for key, value in mapping.items():
            if key in name:
                return value
        return "unknown"

    def _update_existing_preference(self, preference, clinical_pattern: Dict, was_suggested: bool):
        preference.increment_selection()

        existing = set((preference.clinical_pattern or {}).get("keywords", []))
        new = set(clinical_pattern.get("keywords", []))
        preference.clinical_pattern["keywords"] = list(existing | new)

        if was_suggested:
            preference.confidence_score = min(
                1.0,
                preference.confidence_score + self.CONFIDENCE_INCREMENT * 1.5,
            )

        preference.save()

    def _decay_competing_preferences(self, radiologist, modality, body_part: str, exclude_preference):
        RadiologistPreference = _get_preference_model()
        if RadiologistPreference is None:
            return

        qs = RadiologistPreference.objects.filter(
            radiologist=radiologist,
            modality=modality,
            body_part=body_part,
        ).exclude(id=exclude_preference.id)

        for pref in qs:
            pref.confidence_score = max(0.0, pref.confidence_score - self.CONFIDENCE_DECAY)
            pref.save(update_fields=["confidence_score"])

    def _calculate_consistency_score(self, assignments) -> float:
        if not assignments.exists():
            return 0.0

        contexts = assignments.values("exam__modality", "exam__procedure_name").distinct().count()
        protocols = assignments.values("protocol").distinct().count()

        if contexts == 0:
            return 0.0

        return round(min(1.0, protocols / contexts), 2)


# --------------------------------------------------
# Convenience helpers
# --------------------------------------------------

def update_preference(radiologist, exam: Exam, selected_protocol: ProtocolTemplate, was_suggested: bool = False):
    service = PreferenceLearningService()
    return service.update_preference(
        radiologist=radiologist,
        exam=exam,
        selected_protocol=selected_protocol,
        was_suggested=was_suggested,
    )


# Singleton instance
preference_learning_service = PreferenceLearningService()
