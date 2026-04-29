from types import SimpleNamespace

from adaptive_quiz import build_adaptive_quiz_prompt


def _request(difficulty="medium", focus=None):
    return SimpleNamespace(
        topic="Fractions",
        language="English",
        difficulty=difficulty,
        quiz_count=6,
        focus_categories=focus,
        student_class="6",
        curriculum="CBSE",
        subject="Mathematics",
        teaching_mode="Step-by-step",
        learner_context="",
    )


def test_forces_easy_when_last_score_low():
    result = build_adaptive_quiz_prompt(
        _request(difficulty="hard"),
        {"last_score": 35, "weak_categories": [], "strong_categories": [], "consecutive_failures": 1},
    )
    assert result["effective_difficulty"] == "easy"


def test_forces_hard_when_last_score_high():
    result = build_adaptive_quiz_prompt(
        _request(difficulty="easy"),
        {"last_score": 91, "weak_categories": [], "strong_categories": [], "consecutive_failures": 0},
    )
    assert result["effective_difficulty"] == "hard"


def test_keeps_requested_difficulty_in_mid_band():
    result = build_adaptive_quiz_prompt(
        _request(difficulty="medium"),
        {"last_score": 62, "weak_categories": [], "strong_categories": [], "consecutive_failures": 0},
    )
    assert result["effective_difficulty"] == "medium"


def test_weak_categories_override_focus_categories():
    result = build_adaptive_quiz_prompt(
        _request(difficulty="medium", focus=["geometry", "algebra"]),
        {"last_score": 62, "weak_categories": ["fractions"], "strong_categories": [], "consecutive_failures": 0},
    )
    assert result["effective_focus_categories"] == ["fractions"]


def test_prompt_contains_grade_and_teaching_mode_guidance():
    result = build_adaptive_quiz_prompt(
        _request(difficulty="medium"),
        {"last_score": 62, "weak_categories": [], "strong_categories": ["number sense"], "consecutive_failures": 2},
    )
    prompt = result["prompt"]
    assert "grade-appropriate" in prompt.lower() or "vocabulary" in prompt.lower()
    assert "step-by-step" in prompt.lower() or "sequence" in prompt.lower()
