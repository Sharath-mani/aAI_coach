from __future__ import annotations

from typing import Any, Dict, List, Optional

from prompts import PROMPT_VERSION, build_quiz_generation_prompt


def _normalize_difficulty(value: str) -> str:
    normalized = (value or "medium").strip().lower()
    if normalized not in {"easy", "medium", "hard"}:
        return "medium"
    return normalized


def _teaching_mode_phrase(teaching_mode: Optional[str]) -> str:
    mode = (teaching_mode or "").strip()
    mapping = {
        "Pictorial / visual": "Use highly concrete, image-friendly wording and visual examples.",
        "Step-by-step": "Phrase questions with clear sequence cues and process words.",
        "Example-based": "Prefer practical examples in the stem whenever possible.",
        "Story-based": "Frame questions in short scenario-based storytelling style.",
        "Simple language": "Use short, low-complexity sentences and simple terms.",
        "Activity-based": "Use action-oriented and task-driven phrasing.",
        "Practice-focused": "Emphasize repeated practice and applied understanding.",
        "Bilingual hints": "Keep the selected language as primary and add light English hints only if needed.",
    }
    return mapping.get(mode, "Match language and phrasing to the learner profile.")


def _grade_vocabulary_instruction(student_class: Optional[str]) -> str:
    try:
        grade = int(str(student_class or "").strip())
    except ValueError:
        grade = 0

    if grade <= 0:
        return "Use age-appropriate vocabulary for the given learner profile."
    if grade <= 4:
        return "Use very simple vocabulary and short direct question stems."
    if grade <= 8:
        return "Use moderate vocabulary with concrete examples and minimal jargon."
    return "Use grade-appropriate academic vocabulary with precise terminology."


def _compute_consecutive_failures(history: List[Dict[str, Any]], pass_mark: float = 50.0) -> int:
    failures = 0
    for item in reversed(history):
        score = float(item.get("score_percent") or 0)
        if score < pass_mark:
            failures += 1
        else:
            break
    return failures


def _derive_category_strengths(history: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    latest_quiz = None
    for item in reversed(history):
        if str(item.get("module", "")).lower() == "quiz":
            latest_quiz = item
            break

    if not latest_quiz:
        return {"weak": [], "strong": []}

    metadata = latest_quiz.get("metadata") or {}
    category_percentages = metadata.get("category_percentages") or {}
    weak = [name for name, pct in category_percentages.items() if float(pct) < 50.0]
    strong = [name for name, pct in category_percentages.items() if float(pct) >= 80.0]
    return {"weak": weak, "strong": strong}


def derive_student_performance(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    profile = profile or {}
    history_raw = profile.get("performance_history") or []
    history = history_raw if isinstance(history_raw, list) else []

    last_score = 0.0
    if history:
        last_score = float((history[-1] or {}).get("score_percent") or 0)

    strengths = _derive_category_strengths(history)
    return {
        "last_score": last_score,
        "consecutive_failures": _compute_consecutive_failures(history),
        "weak_categories": strengths["weak"],
        "strong_categories": strengths["strong"],
        "history_count": len(history),
    }


def build_adaptive_quiz_prompt(request: Any, student_performance: Dict[str, Any]) -> Dict[str, Any]:
    requested_difficulty = _normalize_difficulty(getattr(request, "difficulty", "medium"))
    weak_categories = [c for c in (student_performance.get("weak_categories") or []) if str(c).strip()]

    last_score = float(student_performance.get("last_score") or 0)
    if last_score < 40:
        effective_difficulty = "easy"
        difficulty_reason = "Last score below 40%, force easy difficulty for recovery."
    elif last_score > 85:
        effective_difficulty = "hard"
        difficulty_reason = "Last score above 85%, force hard difficulty for progression."
    else:
        effective_difficulty = requested_difficulty
        difficulty_reason = "Last score in adaptive band, keep requested difficulty."

    requested_focus = getattr(request, "focus_categories", None) or []
    effective_focus_categories = weak_categories if weak_categories else requested_focus

    adaptive_instructions = [
        f"Adaptive policy version: {PROMPT_VERSION}",
        f"Difficulty rule applied: {difficulty_reason}",
        f"Consecutive failures: {int(student_performance.get('consecutive_failures') or 0)}",
        f"Strong categories: {', '.join(student_performance.get('strong_categories') or []) or 'None'}",
        "If consecutive failures >= 2, keep distractors simple and explanations confidence-building.",
        _grade_vocabulary_instruction(getattr(request, "student_class", None)),
        _teaching_mode_phrase(getattr(request, "teaching_mode", None)),
    ]

    prompt_text = build_quiz_generation_prompt(
        topic=getattr(request, "topic", ""),
        language=getattr(request, "language", "English"),
        difficulty=effective_difficulty,
        quiz_count=int(getattr(request, "quiz_count", 5)),
        focus_categories=effective_focus_categories,
        student_class=getattr(request, "student_class", None),
        curriculum=getattr(request, "curriculum", None),
        subject=getattr(request, "subject", None),
        teaching_mode=getattr(request, "teaching_mode", None),
        learner_context=getattr(request, "learner_context", None),
        adaptive_notes="\n".join(adaptive_instructions),
    )

    return {
        "prompt": prompt_text,
        "effective_difficulty": effective_difficulty,
        "effective_focus_categories": effective_focus_categories,
        "requested_difficulty": requested_difficulty,
        "requested_focus_categories": requested_focus,
        "last_score": last_score,
    }
