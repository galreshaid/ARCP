from __future__ import annotations

from django import template


register = template.Library()


@register.filter
def protocol_note_lines(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        if line[:1] in {"-", "*", "•"}:
            line = line[1:].strip()

        lines.append(line)

    return lines


@register.filter
def suggestion_reasoning_lines(value):
    if not isinstance(value, dict):
        return []

    lines = []

    if value.get("procedure_match"):
        lines.append("Exact procedure match")

    procedure_name_score = float(value.get("procedure_name_score") or 0)
    if procedure_name_score > 0:
        lines.append(f"Procedure name match {round(procedure_name_score * 100)}%")

    if value.get("body_part_match"):
        lines.append("Body region match")

    keyword_score = float(value.get("keyword_score") or 0)
    if keyword_score > 0:
        lines.append(f"Keyword overlap {round(keyword_score * 100)}%")

    behavior_context_score = float(value.get("behavior_context_score") or 0)
    if behavior_context_score > 0:
        lines.append(f"Behavioral context fit {round(behavior_context_score * 100)}%")

    if float(value.get("behavior_facility_score") or 0) > 0:
        lines.append("Facility-specific behavior match")

    if float(value.get("learned_preference_score") or 0) > 0:
        lines.append("Learns from your protocol selections")

    if value.get("is_default"):
        lines.append("Default protocol")

    if float(value.get("recent_usage_score") or 0) > 0:
        lines.append("Used recently")

    if float(value.get("usage_score") or 0) > 0:
        lines.append("Usage history")

    if float(value.get("priority_score") or 0) > 0:
        lines.append("Priority-ranked")

    return lines
