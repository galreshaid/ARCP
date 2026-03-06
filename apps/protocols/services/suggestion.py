"""
Protocol Suggestion Service
Adaptive protocol suggestion system
Combines rule-based scoring with behavior-based learning signals.
"""

import re
from collections import Counter
from typing import List, Dict, Optional, Tuple
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.protocols.models import (
    ProtocolTemplate,
    ProtocolAssignment,
    RadiologistPreference,
)
from apps.core.models import Exam, Procedure


BODY_REGION_ALIASES = {
    "head": "HEAD",
    "brain": "HEAD",
    "skull": "HEAD",
    "neck": "NECK",
    "chest": "CHEST",
    "thorax": "CHEST",
    "lung": "CHEST",
    "pulmonary": "CHEST",
    "abdomen": "ABDOMEN",
    "abdominal": "ABDOMEN",
    "renal": "ABDOMEN",
    "kidney": "ABDOMEN",
    "pelvis": "PELVIS",
    "pelvic": "PELVIS",
    "spine": "SPINE",
    "spinal": "SPINE",
    "upper extremity": "UPPER EXTREMITY",
    "arm": "UPPER EXTREMITY",
    "elbow": "UPPER EXTREMITY",
    "wrist": "UPPER EXTREMITY",
    "hand": "UPPER EXTREMITY",
    "shoulder": "UPPER EXTREMITY",
    "lower extremity": "LOWER EXTREMITY",
    "leg": "LOWER EXTREMITY",
    "knee": "LOWER EXTREMITY",
    "ankle": "LOWER EXTREMITY",
    "foot": "LOWER EXTREMITY",
    "hip": "LOWER EXTREMITY",
    "breast": "BREAST",
    "body": "BODY",
    "whole body": "BODY",
    "multi area": "MULTI AREA",
    "nonspecific": "NONSPECIFIC",
}


class ProtocolSuggestion:
    """
    Protocol suggestion with score
    """

    MAX_POSSIBLE_SCORE = 45.0

    def __init__(
        self,
        protocol: ProtocolTemplate,
        score: float,
        reasoning: Dict,
        rank: int = 0,
    ):
        self.protocol = protocol
        self.score = score
        self.reasoning = reasoning
        self.rank = rank

    def to_dict(self) -> Dict:
        return {
            "protocol_id": str(self.protocol.id),
            "protocol_code": self.protocol.code,
            "protocol_name": self.protocol.name,
            "score": round(self.score, 3),
            "match_percent": self.match_percent,
            "rank": self.rank,
            "reasoning": self.reasoning,
        }

    @property
    def match_percent(self) -> int:
        if self.score <= 0:
            return 0

        percent = round((self.score / self.MAX_POSSIBLE_SCORE) * 100)
        return max(1, min(100, percent))


class ProtocolSuggestionService:
    """
    Suggests protocols based on exam context and radiologist behavior.
    """

    # -----------------------------
    # Scoring weights
    # -----------------------------
    WEIGHT_EXACT_MATCH = 10.0
    WEIGHT_PROCEDURE_NAME = 6.0
    WEIGHT_BODY_PART = 7.0
    WEIGHT_KEYWORD_MATCH = 3.0
    WEIGHT_USAGE_FREQUENCY = 2.0
    WEIGHT_DEFAULT_PROTOCOL = 1.5
    WEIGHT_RECENT_USAGE = 1.0
    WEIGHT_BEHAVIOR_CONTEXT = 8.0
    WEIGHT_BEHAVIOR_FACILITY = 2.5
    WEIGHT_LEARNED_PREFERENCE = 4.0

    # -----------------------------
    # Public API
    # -----------------------------

    def suggest_protocols(
        self,
        exam: Exam,
        radiologist,
        max_suggestions: int = 5,
        log_suggestions: bool = False,
    ) -> List[ProtocolSuggestion]:
        del log_suggestions

        candidates = self._get_candidate_protocols(exam)
        if not candidates:
            return []

        behavior_profile = self._build_behavior_profile(
            exam=exam,
            radiologist=radiologist,
            candidates=candidates,
        )

        suggestions: List[ProtocolSuggestion] = []

        for protocol in candidates:
            score, reasoning = self._score_protocol(
                protocol=protocol,
                exam=exam,
                radiologist=radiologist,
                behavior_profile=behavior_profile,
            )
            suggestions.append(
                ProtocolSuggestion(
                    protocol=protocol,
                    score=score,
                    reasoning=reasoning,
                )
            )

        # Sort DESC
        suggestions.sort(key=lambda x: x.score, reverse=True)

        # Assign ranks
        for idx, s in enumerate(suggestions[:max_suggestions], start=1):
            s.rank = idx

        return suggestions[:max_suggestions]

    def get_top_suggestion(
        self,
        exam: Exam,
        radiologist,
    ) -> Optional[ProtocolSuggestion]:
        results = self.suggest_protocols(
            exam=exam,
            radiologist=radiologist,
            max_suggestions=1,
        )
        return results[0] if results else None

    # -----------------------------
    # Internal logic
    # -----------------------------

    def _get_candidate_protocols(self, exam: Exam) -> List[ProtocolTemplate]:
        qs = ProtocolTemplate.objects.filter(
            modality=exam.modality,
            is_active=True,
        ).filter(
            Q(facility__isnull=True) |
            Q(facility=exam.facility)
        )

        candidates = list(qs.select_related("modality", "facility", "procedure"))
        exam_regions = self._get_exam_regions(exam)
        if not exam_regions:
            return candidates

        region_matched = []
        region_unknown = []

        for protocol in candidates:
            protocol_regions = self._get_protocol_regions(protocol)
            if not protocol_regions:
                region_unknown.append(protocol)
                continue
            if exam_regions & protocol_regions:
                region_matched.append(protocol)

        if region_matched:
            return region_matched + region_unknown

        return candidates

    def _score_protocol(
        self,
        protocol: ProtocolTemplate,
        exam: Exam,
        radiologist,
        behavior_profile: Dict,
    ) -> Tuple[float, Dict]:

        score = 0.0
        reasoning: Dict = {}

        # 1️⃣ Exact procedure match
        if self._procedure_match(exam, protocol):
            score += self.WEIGHT_EXACT_MATCH
            reasoning["procedure_match"] = True

        # 1b️⃣ Procedure-name similarity
        procedure_name_score = self._procedure_name_score(exam, protocol)
        if procedure_name_score:
            score += procedure_name_score * self.WEIGHT_PROCEDURE_NAME
            reasoning["procedure_name_score"] = round(procedure_name_score, 3)

        # 2️⃣ Body part match
        if self._body_part_match(exam, protocol):
            score += self.WEIGHT_BODY_PART
            reasoning["body_part_match"] = True

        # 3️⃣ Clinical keywords
        keyword_ratio = self._keyword_score(exam, protocol)
        if keyword_ratio:
            score += keyword_ratio * self.WEIGHT_KEYWORD_MATCH
            reasoning["keyword_score"] = keyword_ratio

        # 4️⃣ Global usage frequency
        usage_score = min(1.0, protocol.usage_count / 1000)
        score += usage_score * self.WEIGHT_USAGE_FREQUENCY
        reasoning["usage_score"] = usage_score

        # 5️⃣ Default protocol
        if protocol.is_default:
            score += self.WEIGHT_DEFAULT_PROTOCOL
            reasoning["is_default"] = True

        # 6️⃣ Recent usage by radiologist
        recent_count = int(behavior_profile.get("recent_counts", {}).get(protocol.id, 0) or 0)
        recent_score = min(1.0, recent_count / 10)
        score += recent_score * self.WEIGHT_RECENT_USAGE
        reasoning["recent_usage_score"] = recent_score

        # 7️⃣ Context-aware behavioral signal (radiologist history)
        context_counts = behavior_profile.get("context_counts", {})
        context_total = int(behavior_profile.get("context_total", 0) or 0)
        context_count = int(context_counts.get(protocol.id, 0) or 0)
        if context_total > 0 and context_count > 0:
            sample_factor = float(behavior_profile.get("context_sample_factor", 1.0) or 0.0)
            context_ratio = context_count / context_total
            behavior_context_score = context_ratio * sample_factor
            score += behavior_context_score * self.WEIGHT_BEHAVIOR_CONTEXT
            reasoning["behavior_context_score"] = round(behavior_context_score, 3)
            reasoning["behavior_context_count"] = context_count

            facility_context_counts = behavior_profile.get("facility_context_counts", {})
            facility_context_count = int(facility_context_counts.get(protocol.id, 0) or 0)
            if facility_context_count > 0:
                facility_ratio = facility_context_count / context_count
                score += facility_ratio * self.WEIGHT_BEHAVIOR_FACILITY
                reasoning["behavior_facility_score"] = round(facility_ratio, 3)

        # 8️⃣ Learned preference signal (RadiologistPreference layer)
        learned_scores = behavior_profile.get("learned_preference_scores", {})
        learned_preference_score = float(learned_scores.get(protocol.id, 0.0) or 0.0)
        if learned_preference_score > 0:
            score += learned_preference_score * self.WEIGHT_LEARNED_PREFERENCE
            reasoning["learned_preference_score"] = round(learned_preference_score, 3)

        # 9️⃣ Priority
        priority_score = max(0, (200 - protocol.priority) / 200)
        score += priority_score
        reasoning["priority_score"] = priority_score

        return score, reasoning

    def _build_behavior_profile(
        self,
        exam: Exam,
        radiologist,
        candidates: List[ProtocolTemplate],
    ) -> Dict:
        now = timezone.now()
        candidate_ids = [protocol.id for protocol in candidates]
        if not candidate_ids:
            return {
                "recent_counts": {},
                "context_counts": {},
                "facility_context_counts": {},
                "context_total": 0,
                "context_sample_factor": 0.0,
                "learned_preference_scores": {},
            }

        cutoff_recent = now - timedelta(days=30)
        cutoff_behavior = now - timedelta(days=180)
        recent_counts: Counter = Counter()
        context_counts: Counter = Counter()
        facility_context_counts: Counter = Counter()

        exam_regions = self._get_exam_regions(exam)
        history = ProtocolAssignment.objects.filter(
            assigned_by=radiologist,
            protocol_id__in=candidate_ids,
            exam__modality=exam.modality,
            assigned_at__gte=cutoff_behavior,
        ).select_related("exam", "protocol")

        context_total = 0
        for assignment in history:
            protocol_id = assignment.protocol_id
            if not protocol_id:
                continue

            assigned_at = assignment.assigned_at or assignment.created_at
            if assigned_at and assigned_at >= cutoff_recent:
                recent_counts[protocol_id] += 1

            historical_exam = assignment.exam
            if not historical_exam:
                continue

            if not self._is_context_match(exam, historical_exam, exam_regions):
                continue

            context_counts[protocol_id] += 1
            context_total += 1

            if exam.facility_id and historical_exam.facility_id == exam.facility_id:
                facility_context_counts[protocol_id] += 1

        learned_preference_scores = self._learned_preference_scores(
            exam=exam,
            radiologist=radiologist,
            candidate_ids=candidate_ids,
        )

        return {
            "recent_counts": dict(recent_counts),
            "context_counts": dict(context_counts),
            "facility_context_counts": dict(facility_context_counts),
            "context_total": context_total,
            "context_sample_factor": min(1.0, context_total / 3.0),
            "learned_preference_scores": learned_preference_scores,
        }

    def _is_context_match(self, current_exam: Exam, historical_exam: Exam, current_exam_regions: set[str]) -> bool:
        current_code = str(current_exam.procedure_code or "").strip().upper()
        historical_code = str(historical_exam.procedure_code or "").strip().upper()
        if current_code and historical_code and current_code == historical_code:
            return True

        current_name = self._normalize_match_text(current_exam.procedure_name or "")
        historical_name = self._normalize_match_text(historical_exam.procedure_name or "")
        if current_name and historical_name:
            if current_name == historical_name:
                return True
            token_overlap = self._token_overlap_ratio(current_name, historical_name)
            if token_overlap >= 0.6:
                return True

        historical_regions = self._extract_regions(
            str((historical_exam.metadata or {}).get("body_part", "") or ""),
            historical_exam.procedure_name or "",
            historical_exam.reason_for_exam or "",
        )
        if current_exam_regions and historical_regions and (current_exam_regions & historical_regions):
            return True

        return False

    def _learned_preference_scores(
        self,
        exam: Exam,
        radiologist,
        candidate_ids: List,
    ) -> Dict:
        body_parts = {
            str(region or "").strip().lower()
            for region in self._get_exam_regions(exam)
            if str(region or "").strip()
        }

        qs = RadiologistPreference.objects.filter(
            radiologist=radiologist,
            modality=exam.modality,
            preferred_protocol_id__in=candidate_ids,
        ).filter(
            Q(facility__isnull=True) | Q(facility=exam.facility)
        )

        if body_parts:
            qs = qs.filter(
                Q(body_part__in=body_parts)
                | Q(body_part="")
                | Q(body_part="unknown")
            )

        scores: Dict = {}
        for preference in qs:
            confidence = max(0.0, min(1.0, float(preference.confidence_score or 0.0)))
            selection_factor = min(1.0, float(preference.selection_count or 0) / 20.0)
            if selection_factor <= 0:
                continue
            signal = confidence * selection_factor
            protocol_id = preference.preferred_protocol_id
            if not protocol_id:
                continue
            scores[protocol_id] = max(float(scores.get(protocol_id, 0.0) or 0.0), signal)

        return scores

    def _token_overlap_ratio(self, left_text: str, right_text: str) -> float:
        left_tokens = {token for token in left_text.split() if token}
        right_tokens = {token for token in right_text.split() if token}
        if not left_tokens or not right_tokens:
            return 0.0
        shared_tokens = left_tokens & right_tokens
        return len(shared_tokens) / max(len(left_tokens), len(right_tokens))

    def _body_part_match(self, exam: Exam, protocol: ProtocolTemplate) -> bool:
        exam_regions = self._get_exam_regions(exam)
        protocol_regions = self._get_protocol_regions(protocol)

        if not exam_regions or not protocol_regions:
            return False

        return bool(exam_regions & protocol_regions)

    def _keyword_score(self, exam: Exam, protocol: ProtocolTemplate) -> float:
        if not protocol.clinical_keywords:
            return 0.0

        text = " ".join([
            exam.clinical_history or "",
            exam.reason_for_exam or "",
            exam.procedure_name or "",
        ]).lower()

        matches = sum(
            1 for kw in protocol.clinical_keywords
            if kw.lower() in text
        )

        return matches / len(protocol.clinical_keywords)

    def _procedure_match(self, exam: Exam, protocol: ProtocolTemplate) -> bool:
        if not exam.procedure_code:
            exam_code_match = False
        else:
            exam_code_match = protocol.code == exam.procedure_code

        if protocol.procedure_id and protocol.procedure and protocol.procedure.code == exam.procedure_code:
            return True

        return exam_code_match

    def _procedure_name_score(self, exam: Exam, protocol: ProtocolTemplate) -> float:
        exam_name = self._normalize_match_text(exam.procedure_name or "")
        if not exam_name:
            return 0.0

        exam_tokens = set(exam_name.split())
        if not exam_tokens:
            return 0.0

        best_score = 0.0
        candidates = [
            protocol.name or "",
            protocol.code or "",
        ]

        if protocol.procedure_id and protocol.procedure:
            candidates.append(protocol.procedure.name or "")
            candidates.append(protocol.procedure.code or "")

        for raw_candidate in candidates:
            candidate = self._normalize_match_text(raw_candidate)
            if not candidate:
                continue

            if candidate == exam_name:
                return 1.0

            if len(exam_name) >= 6 and exam_name in candidate:
                best_score = max(best_score, 0.9)

            candidate_tokens = set(candidate.split())
            if not candidate_tokens:
                continue

            shared_tokens = exam_tokens & candidate_tokens
            if shared_tokens:
                token_ratio = len(shared_tokens) / len(exam_tokens)
                best_score = max(best_score, min(0.85, token_ratio))

        return best_score

    def _normalize_match_text(self, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
        return " ".join(normalized.split())

    def _get_exam_regions(self, exam: Exam) -> set[str]:
        texts = [
            (exam.metadata or {}).get("body_part", ""),
            exam.procedure_name or "",
            exam.reason_for_exam or "",
        ]

        if exam.procedure_code:
            procedure = Procedure.objects.filter(code=exam.procedure_code).only("body_region").first()
            if procedure and procedure.body_region:
                texts.append(procedure.body_region)

        return self._extract_regions(*texts)

    def _get_protocol_regions(self, protocol: ProtocolTemplate) -> set[str]:
        texts = [
            protocol.body_part or "",
            protocol.body_region or "",
            protocol.name or "",
            protocol.code or "",
        ]

        if protocol.procedure_id and protocol.procedure:
            texts.append(protocol.procedure.body_region or "")
            texts.append(protocol.procedure.name or "")

        return self._extract_regions(*texts)

    def _extract_regions(self, *values: str) -> set[str]:
        normalized_text = " ".join((value or "").lower() for value in values if value).strip()
        if not normalized_text:
            return set()

        normalized_text = re.sub(r"[_/-]+", " ", normalized_text)
        regions = {
            region
            for phrase, region in BODY_REGION_ALIASES.items()
            if phrase in normalized_text
        }
        return regions


# Singleton
protocol_suggestion_service = ProtocolSuggestionService()
