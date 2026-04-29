from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Optional, Any
import os
import time
import logging
import json
import sqlite3
import re
import difflib
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from prompts import (
    build_essay_topic_prompt,
    build_essay_parameter_prompt,
    build_essay_question_prompt,
    build_sample_essay_prompt,
)

# Load environment variables from .env file
load_dotenv()

import google.generativeai as genai
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.piecharts import Pie

# ---------- Gemini Configuration ----------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY or GEMINI_API_KEY.strip() in {"your-gemini-api-key-here", "REPLACE_WITH_YOUR_ACTUAL_API_KEY"}:
    raise RuntimeError("GEMINI_API_KEY is not set or is placeholder. Set GEMINI_API_KEY in .env and restart.")

genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-2.5-flash"

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EssayAPI")

# ---------- Constants ----------
VALID_EVAL_PARAMS = {
    "language", "analysis", "thought", "structure", "relevance",
    "creativity", "argumentation", "coherence", "grammar", "vocabulary",
    "depth", "originality", "clarity", "logic", "persuasiveness",
    "evidence", "examples", "organization", "flow", "transitions",
    "conclusion", "introduction", "thesis", "tone", "style",
    "mechanics", "syntax", "spelling", "punctuation", "accuracy",
    "insight", "critical thinking", "argument strength", "topic adherence"
}

app = FastAPI(title="Essay Evaluation API", description="API for evaluating essays using AI", version="1.0.0")

# ---------- CORS Configuration ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_envelope(code: str, message: str) -> Dict[str, Dict[str, str]]:
    return {"error": {"code": code, "message": message}}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_envelope(f"HTTP_{exc.status_code}", detail),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_error_envelope("VALIDATION_ERROR", "Invalid request payload"),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_error_envelope("INTERNAL_SERVER_ERROR", "Unexpected server error"),
    )

# ---------- Pydantic Models ----------
class EvaluateRequest(BaseModel):
    essay: str
    parameters: List[str]
    candidate_name: Optional[str] = "Anonymous"
    student_class: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    language: Optional[str] = "English"
    teaching_mode: Optional[str] = None
    learner_context: Optional[str] = None

class GenerateQuestionRequest(BaseModel):
    basis: str
    student_class: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    language: Optional[str] = "English"
    teaching_mode: Optional[str] = None
    learner_context: Optional[str] = None

class GenerateSampleEssayRequest(BaseModel):
    essay_topic: str
    parameters: List[str]
    student_class: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    language: Optional[str] = "English"
    teaching_mode: Optional[str] = None
    learner_context: Optional[str] = None

class GenerateCertificateRequest(BaseModel):
    candidate_name: str
    scores_percent: Dict[str, float]
    essay_topic: Optional[str] = None
    student_class: Optional[str] = None
    curriculum: Optional[str] = None

class StudentProfileRequest(BaseModel):
    student_id: Optional[str] = None
    candidate_name: str
    student_class: str
    curriculum: str
    learning_style_preference: Optional[str] = None
    subject: Optional[str] = None
    language: str = "English"
    teaching_mode: str = "Pictorial / visual"
    skill_level_per_subject: Optional[str] = None
    goal: str = "General learning"
    weak_areas: Optional[str] = None
    explanation_style: str = "Step-by-step"
    avg_study_time_minutes_per_day: Optional[int] = None
    study_time_morning_hours: Optional[float] = None
    study_time_afternoon_hours: Optional[float] = None
    study_time_evening_hours: Optional[float] = None
    study_time_night_hours: Optional[float] = None
    preferred_difficulty_level: str = "Medium"
    attention_span_preference: str = "10-20 min"
    primary_struggle_area: str = "Understanding concepts"
    learning_pace: str = "average"
    question_style_preference: str = "multiple-choice only"
    time_limit_per_question_seconds: Optional[int] = None
    feedback_detail_level: str = "detailed"
    confidence_per_topic: Optional[Dict[str, int]] = None
    preferred_examples_domain: str = "daily life"
    gamification_preference: str = "yes"
    class_roster_csv_text: Optional[str] = None
    assignment_deadlines: Optional[List[Dict[str, Any]]] = None
    custom_rubric_weights: Optional[Dict[str, float]] = None
    minimum_passing_thresholds: Optional[Dict[str, float]] = None
    export_formats: Optional[List[str]] = None
    plagiarism_check_enabled: bool = False

class StudentPerformanceRequest(BaseModel):
    student_id: str
    module: str
    score_percent: float
    duration_minutes: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class StudentProgressReportRequest(BaseModel):
    student_id: str
    limit: int = 100

class SessionHistoryItem(BaseModel):
    candidate_name: str
    essay_question: str
    essay_topic: str
    essay: str
    scores: Dict[str, float]
    feedbacks: Dict[str, str]
    avg_score: float
    overall_feedback: str
    timestamp: float
    student_class: Optional[str] = None
    curriculum: Optional[str] = None

class GenerateReportRequest(BaseModel):
    session_history: List[SessionHistoryItem]
    candidate_name: str
    student_class: Optional[str] = None
    curriculum: Optional[str] = None

# ---------- Helper Functions ----------
def _extract_json_object(text: str) -> dict:
    """Extract JSON from model response."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No valid JSON object found.")
    return json.loads(text[start:end+1])

def _sanitize_param_name(param: str) -> str:
    return param.lower().replace(" ", "_")

def _get_essay_topic(essay_text: str) -> str:
    """Generate a very short title (max 5-7 words) from the essay."""
    prompt = build_essay_topic_prompt(essay_text[:2000])
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        topic = response.text.strip().strip('"').strip("'").strip()
        if len(topic) > 100:
            topic = topic[:100] + "..."
        return topic
    except Exception as e:
        logger.error(f"Failed to generate essay topic: {e}")
        words = essay_text.split()[:5]
        return " ".join(words) + ("..." if len(essay_text.split()) > 5 else "")

# ---------- Certificate Number Management ----------
CERT_COUNTER_FILE = "certificate_counter.json"
STUDENT_PROFILE_DB_FILE = "student_profile.db"
ESSAY_EVAL_CACHE_FILE = "essay_evaluation_cache.json"
ESSAY_EVAL_CACHE_TTL_SECONDS = 24 * 60 * 60

ALLOWED_GOALS = {
    "Exam prep",
    "Improve writing",
    "Competitive exams",
    "General learning",
}
ALLOWED_EXPLANATION_STYLES = {
    "Short & crisp",
    "Detailed",
    "Step-by-step",
    "Example-based",
}
ALLOWED_DIFFICULTY_LEVELS = {"Easy", "Medium", "Hard"}
ALLOWED_SELF_LEVELS = {"Beginner", "Intermediate", "Advanced"}
ALLOWED_ATTENTION_SPAN = {"<10 min", "10-20 min", ">20 min"}
ALLOWED_PRIMARY_STRUGGLE = {
    "Understanding concepts",
    "Remembering",
    "Solving problems",
    "Writing answers",
    "Exam fear",
}
ALLOWED_LEARNING_PACE = {"slow", "average", "fast"}
ALLOWED_QUESTION_STYLE = {"multiple-choice only", "true/false"}
ALLOWED_FEEDBACK_DETAIL = {"brief", "detailed", "very detailed"}
ALLOWED_EXAMPLES_DOMAIN = {"sports", "science", "daily life"}
ALLOWED_GAMIFICATION_PREFERENCE = {"yes", "no"}
ALLOWED_EXPORT_FORMATS = {"CSV", "Excel"}


def _infer_curriculum_from_class(student_class: str) -> str:
    try:
        grade = int(str(student_class or "").strip())
    except ValueError:
        return "CBSE"
    if grade <= 10:
        return "State Board"
    return "CBSE"


def _map_learning_style_to_teaching_mode(style: Optional[str]) -> Optional[str]:
    style_key = (style or "").strip().lower()
    mapping = {
        "examples": "Example-based",
        "stories": "Story-based",
        "short-steps": "Step-by-step",
    }
    return mapping.get(style_key)


def _read_eval_cache() -> Dict[str, Any]:
    cache_path = Path(ESSAY_EVAL_CACHE_FILE)
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_eval_cache(payload: Dict[str, Any]) -> None:
    cache_path = Path(ESSAY_EVAL_CACHE_FILE)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")


def _build_eval_cache_key(request: EvaluateRequest) -> str:
    payload = {
        "essay": request.essay,
        "parameters": sorted(request.parameters),
        "student_class": request.student_class,
        "curriculum": request.curriculum,
        "subject": request.subject,
        "language": request.language,
        "teaching_mode": request.teaching_mode,
        "learner_context": request.learner_context,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest


def _get_cached_evaluation(cache_key: str) -> Optional[Dict[str, Any]]:
    cache = _read_eval_cache()
    item = cache.get(cache_key)
    if not item:
        return None
    created_at = float(item.get("created_at", 0))
    if (time.time() - created_at) > ESSAY_EVAL_CACHE_TTL_SECONDS:
        cache.pop(cache_key, None)
        _write_eval_cache(cache)
        return None
    return item.get("result")


def _store_cached_evaluation(cache_key: str, result: Dict[str, Any]) -> None:
    cache = _read_eval_cache()
    cache[cache_key] = {"created_at": time.time(), "result": result}
    _write_eval_cache(cache)

def _init_student_profile_db() -> None:
    with sqlite3.connect(STUDENT_PROFILE_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='student_profile'"
        ).fetchone()

        if existing:
            existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(student_profile)")}
            if "student_id" not in existing_columns:
                old_rows = conn.execute(
                    "SELECT candidate_name, student_class, curriculum, subject, language, teaching_mode, updated_at FROM student_profile"
                ).fetchall()
                conn.execute("DROP TABLE student_profile")
                conn.execute(
                    """
                    CREATE TABLE student_profile (
                        student_id TEXT PRIMARY KEY,
                        candidate_name TEXT NOT NULL,
                        student_class TEXT NOT NULL,
                        curriculum TEXT NOT NULL,
                        subject TEXT,
                        language TEXT NOT NULL DEFAULT 'English',
                        teaching_mode TEXT NOT NULL DEFAULT 'Pictorial / visual',
                        skill_level_per_subject TEXT,
                        goal TEXT NOT NULL DEFAULT 'General learning',
                        weak_areas TEXT,
                        explanation_style TEXT NOT NULL DEFAULT 'Step-by-step',
                        avg_study_time_minutes_per_day INTEGER,
                        preferred_difficulty_level TEXT NOT NULL DEFAULT 'Medium',
                            study_time_morning_hours REAL,
                            study_time_afternoon_hours REAL,
                            study_time_evening_hours REAL,
                            study_time_night_hours REAL,
                            preferred_difficulty_level TEXT NOT NULL DEFAULT 'Medium',
                            attention_span_preference TEXT NOT NULL DEFAULT '10-20 min',
                        primary_struggle_area TEXT NOT NULL DEFAULT 'Understanding concepts',
                        learning_pace TEXT NOT NULL DEFAULT 'average',
                        question_style_preference TEXT NOT NULL DEFAULT 'multiple-choice only',
                        time_limit_per_question_seconds INTEGER,
                        feedback_detail_level TEXT NOT NULL DEFAULT 'detailed',
                        confidence_per_topic TEXT,
                        preferred_examples_domain TEXT NOT NULL DEFAULT 'daily life',
                        gamification_preference TEXT NOT NULL DEFAULT 'yes',
                        class_roster_csv_text TEXT,
                        assignment_deadlines_json TEXT,
                        custom_rubric_weights_json TEXT,
                        minimum_passing_thresholds_json TEXT,
                        export_formats_json TEXT,
                        plagiarism_check_enabled INTEGER NOT NULL DEFAULT 0,
                        performance_history TEXT NOT NULL DEFAULT '[]',
                        performance_avg_score REAL NOT NULL DEFAULT 0,
                        performance_sessions INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                now_ts = time.time()
                for row in old_rows:
                    student_id = _generate_student_id(conn, now_ts)
                    conn.execute(
                        """
                        INSERT INTO student_profile (
                            student_id, candidate_name, student_class, curriculum, subject, language,
                            teaching_mode, skill_level_per_subject, goal, weak_areas, explanation_style,
                            avg_study_time_minutes_per_day, preferred_difficulty_level, attention_span_preference,
                                study_time_morning_hours, study_time_afternoon_hours, study_time_evening_hours, study_time_night_hours,
                            primary_struggle_area, learning_pace,
                            question_style_preference, time_limit_per_question_seconds, feedback_detail_level,
                            confidence_per_topic, preferred_examples_domain, gamification_preference,
                            class_roster_csv_text, assignment_deadlines_json, custom_rubric_weights_json,
                            study_time_morning_hours, study_time_afternoon_hours, study_time_evening_hours, study_time_night_hours,
                            minimum_passing_thresholds_json, export_formats_json, plagiarism_check_enabled,
                            performance_history, performance_avg_score, performance_sessions, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            student_id,
                            row["candidate_name"],
                            row["student_class"],
                            row["curriculum"],
                            row["subject"],
                            row["language"] or "English",
                            row["teaching_mode"] or "Pictorial / visual",
                            None,
                            "General learning",
                            None,
                            "Step-by-step",
                            None,
                            "Medium",
                            "10-20 min",
                            "Understanding concepts",
                            "average",
                            "multiple-choice only",
                            None,
                            "detailed",
                            None,
                            "daily life",
                            "yes",
                            None,
                            None,
                            None,
                            None,
                            None,
                            0,
                            "[]",
                            0.0,
                            0,
                            now_ts,
                            row["updated_at"] or now_ts,
                        ),
                    )
                conn.commit()

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_profile (
                student_id TEXT PRIMARY KEY,
                candidate_name TEXT NOT NULL,
                student_class TEXT NOT NULL,
                curriculum TEXT NOT NULL,
                subject TEXT,
                language TEXT NOT NULL DEFAULT 'English',
                teaching_mode TEXT NOT NULL DEFAULT 'Pictorial / visual',
                skill_level_per_subject TEXT,
                goal TEXT NOT NULL DEFAULT 'General learning',
                weak_areas TEXT,
                explanation_style TEXT NOT NULL DEFAULT 'Step-by-step',
                avg_study_time_minutes_per_day INTEGER,
                preferred_difficulty_level TEXT NOT NULL DEFAULT 'Medium',
                attention_span_preference TEXT NOT NULL DEFAULT '10-20 min',
                primary_struggle_area TEXT NOT NULL DEFAULT 'Understanding concepts',
                learning_pace TEXT NOT NULL DEFAULT 'average',
                question_style_preference TEXT NOT NULL DEFAULT 'multiple-choice only',
                time_limit_per_question_seconds INTEGER,
                feedback_detail_level TEXT NOT NULL DEFAULT 'detailed',
                confidence_per_topic TEXT,
                preferred_examples_domain TEXT NOT NULL DEFAULT 'daily life',
                gamification_preference TEXT NOT NULL DEFAULT 'yes',
                class_roster_csv_text TEXT,
                assignment_deadlines_json TEXT,
                custom_rubric_weights_json TEXT,
                minimum_passing_thresholds_json TEXT,
                export_formats_json TEXT,
                plagiarism_check_enabled INTEGER NOT NULL DEFAULT 0,
                performance_history TEXT NOT NULL DEFAULT '[]',
                performance_avg_score REAL NOT NULL DEFAULT 0,
                performance_sessions INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                feature TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'attempt',
                score_percent REAL,
                duration_minutes INTEGER,
                metadata_json TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(student_profile)")}
        if "subject" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN subject TEXT")
        if "language" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN language TEXT NOT NULL DEFAULT 'English'")
        if "teaching_mode" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN teaching_mode TEXT NOT NULL DEFAULT 'Pictorial / visual'")
        if "skill_level_per_subject" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN skill_level_per_subject TEXT")
        if "goal" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN goal TEXT NOT NULL DEFAULT 'General learning'")
        if "weak_areas" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN weak_areas TEXT")
        if "explanation_style" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN explanation_style TEXT NOT NULL DEFAULT 'Step-by-step'")
        if "avg_study_time_minutes_per_day" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN avg_study_time_minutes_per_day INTEGER")
        if "preferred_difficulty_level" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN preferred_difficulty_level TEXT NOT NULL DEFAULT 'Medium'")
        if "attention_span_preference" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN attention_span_preference TEXT NOT NULL DEFAULT '10-20 min'")
        if "primary_struggle_area" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN primary_struggle_area TEXT NOT NULL DEFAULT 'Understanding concepts'")
        if "learning_pace" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN learning_pace TEXT NOT NULL DEFAULT 'average'")
        if "question_style_preference" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN question_style_preference TEXT NOT NULL DEFAULT 'multiple-choice only'")
        if "time_limit_per_question_seconds" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN time_limit_per_question_seconds INTEGER")
        if "feedback_detail_level" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN feedback_detail_level TEXT NOT NULL DEFAULT 'detailed'")
        if "confidence_per_topic" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN confidence_per_topic TEXT")
        if "preferred_examples_domain" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN preferred_examples_domain TEXT NOT NULL DEFAULT 'daily life'")
        if "gamification_preference" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN gamification_preference TEXT NOT NULL DEFAULT 'yes'")
        if "class_roster_csv_text" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN class_roster_csv_text TEXT")
        if "assignment_deadlines_json" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN assignment_deadlines_json TEXT")
        if "custom_rubric_weights_json" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN custom_rubric_weights_json TEXT")
        if "minimum_passing_thresholds_json" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN minimum_passing_thresholds_json TEXT")
        if "export_formats_json" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN export_formats_json TEXT")
        if "plagiarism_check_enabled" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN plagiarism_check_enabled INTEGER NOT NULL DEFAULT 0")
        if "performance_history" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN performance_history TEXT NOT NULL DEFAULT '[]'")
        if "performance_avg_score" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN performance_avg_score REAL NOT NULL DEFAULT 0")
        if "performance_sessions" not in existing_columns:
            conn.execute("ALTER TABLE student_profile ADD COLUMN performance_sessions INTEGER NOT NULL DEFAULT 0")
        if "created_at" not in existing_columns:
            now_ts = time.time()
            conn.execute("ALTER TABLE student_profile ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
            conn.execute("UPDATE student_profile SET created_at = COALESCE(updated_at, ?) WHERE created_at = 0", (now_ts,))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_student_activity_log_student_time ON student_activity_log(student_id, created_at DESC)")
        conn.commit()

def _generate_student_id(conn: sqlite3.Connection, now_ts: float) -> str:
    current_time = datetime.fromtimestamp(now_ts)
    month_code = current_time.strftime("%m")
    year_code = current_time.strftime("%y")
    month_start = datetime(current_time.year, current_time.month, 1).timestamp()
    if current_time.month == 12:
        next_month_start = datetime(current_time.year + 1, 1, 1).timestamp()
    else:
        next_month_start = datetime(current_time.year, current_time.month + 1, 1).timestamp()
    month_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM student_profile
        WHERE created_at >= ? AND created_at < ?
        """,
        (month_start, next_month_start),
    ).fetchone()[0]
    sequence = month_count + 1
    return f"AGI-{month_code}-{year_code}-{sequence:03d}"

def _sanitize_profile_choice(value: str, allowed: set[str], fallback: str) -> str:
    candidate = (value or "").strip()
    return candidate if candidate in allowed else fallback

def _row_to_profile_dict(row: Any) -> Dict[str, Any]:
    def _loads_json(raw: Any, default: Any) -> Any:
        try:
            parsed = json.loads(raw or "null")
            return parsed if parsed is not None else default
        except Exception:
            return default

    if not isinstance(row, sqlite3.Row):
        keys = [
            "student_id", "candidate_name", "student_class", "curriculum", "subject", "language",
            "teaching_mode", "skill_level_per_subject", "goal", "weak_areas", "explanation_style",
            "avg_study_time_minutes_per_day", "preferred_difficulty_level", "attention_span_preference",
                "study_time_morning_hours", "study_time_afternoon_hours", "study_time_evening_hours", "study_time_night_hours",
                "primary_struggle_area", "learning_pace",
            "question_style_preference", "time_limit_per_question_seconds", "feedback_detail_level",
            "confidence_per_topic", "preferred_examples_domain", "gamification_preference",
            "class_roster_csv_text", "assignment_deadlines_json", "custom_rubric_weights_json",
            "minimum_passing_thresholds_json", "export_formats_json", "plagiarism_check_enabled",
            "performance_history", "performance_avg_score", "performance_sessions", "created_at", "updated_at",
        ]
        row = dict(zip(keys, row))

    return {
        "student_id": row["student_id"],
        "candidate_name": row["candidate_name"],
        "student_class": row["student_class"],
        "curriculum": row["curriculum"],
        "subject": row["subject"],
        "language": row["language"],
        "teaching_mode": row["teaching_mode"],
        "skill_level_per_subject": row["skill_level_per_subject"],
        "goal": row["goal"],
        "weak_areas": row["weak_areas"],
        "explanation_style": row["explanation_style"],
        "avg_study_time_minutes_per_day": row["avg_study_time_minutes_per_day"],
        "study_time_morning_hours": row.get("study_time_morning_hours"),
        "study_time_afternoon_hours": row.get("study_time_afternoon_hours"),
        "study_time_evening_hours": row.get("study_time_evening_hours"),
        "study_time_night_hours": row.get("study_time_night_hours"),
        "preferred_difficulty_level": row["preferred_difficulty_level"],
        "attention_span_preference": row["attention_span_preference"],
        "primary_struggle_area": row["primary_struggle_area"],
        "learning_pace": row["learning_pace"],
        "question_style_preference": row["question_style_preference"],
        "time_limit_per_question_seconds": row["time_limit_per_question_seconds"],
        "feedback_detail_level": row["feedback_detail_level"],
        "confidence_per_topic": _loads_json(row["confidence_per_topic"], {}),
        "preferred_examples_domain": row["preferred_examples_domain"],
        "gamification_preference": row["gamification_preference"],
        "class_roster_csv_text": row["class_roster_csv_text"],
        "assignment_deadlines": _loads_json(row["assignment_deadlines_json"], []),
        "custom_rubric_weights": _loads_json(row["custom_rubric_weights_json"], {}),
        "minimum_passing_thresholds": _loads_json(row["minimum_passing_thresholds_json"], {}),
        "export_formats": _loads_json(row["export_formats_json"], []),
        "plagiarism_check_enabled": bool(row["plagiarism_check_enabled"]),
        "performance_history": _loads_json(row["performance_history"], []),
        "performance_avg_score": row["performance_avg_score"],
        "performance_sessions": row["performance_sessions"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def _save_student_profile(candidate_name: str, student_class: str, curriculum: str,
                          subject: Optional[str] = None,
                          language: str = "English",
                          teaching_mode: str = "Pictorial / visual",
                          student_id: Optional[str] = None,
                          skill_level_per_subject: Optional[str] = None,
                          goal: str = "General learning",
                          weak_areas: Optional[str] = None,
                          explanation_style: str = "Step-by-step",
                          avg_study_time_minutes_per_day: Optional[int] = None,
                         study_time_morning_hours: Optional[float] = None,
                         study_time_afternoon_hours: Optional[float] = None,
                         study_time_evening_hours: Optional[float] = None,
                         study_time_night_hours: Optional[float] = None,
                          preferred_difficulty_level: str = "Medium",
                          attention_span_preference: str = "10-20 min",
                          primary_struggle_area: str = "Understanding concepts",
                          learning_pace: str = "average",
                          question_style_preference: str = "multiple-choice only",
                          time_limit_per_question_seconds: Optional[int] = None,
                          feedback_detail_level: str = "detailed",
                          confidence_per_topic: Optional[Dict[str, int]] = None,
                          preferred_examples_domain: str = "daily life",
                          gamification_preference: str = "yes",
                          class_roster_csv_text: Optional[str] = None,
                          assignment_deadlines: Optional[List[Dict[str, Any]]] = None,
                          custom_rubric_weights: Optional[Dict[str, float]] = None,
                          minimum_passing_thresholds: Optional[Dict[str, float]] = None,
                          export_formats: Optional[List[str]] = None,
                          plagiarism_check_enabled: bool = False) -> Dict[str, Any]:
    now_ts = time.time()
    normalized_subject = (subject or "").strip() or None
    normalized_language = (language or "English").strip() or "English"
    normalized_teaching_mode = (teaching_mode or "Pictorial / visual").strip() or "Pictorial / visual"
    normalized_skill_level_per_subject = (skill_level_per_subject or "").strip() or None
    normalized_goal = _sanitize_profile_choice(goal or "General learning", ALLOWED_GOALS, "General learning")
    normalized_weak_areas = (weak_areas or "").strip() or None
    normalized_explanation_style = _sanitize_profile_choice(
        explanation_style or "Step-by-step",
        ALLOWED_EXPLANATION_STYLES,
        "Step-by-step",
    )
    normalized_preferred_difficulty_level = _sanitize_profile_choice(
        preferred_difficulty_level or "Medium",
        ALLOWED_DIFFICULTY_LEVELS,
        "Medium",
    )
    normalized_attention_span_preference = _sanitize_profile_choice(
        attention_span_preference or "10-20 min",
        ALLOWED_ATTENTION_SPAN,
        "10-20 min",
    )
    normalized_primary_struggle_area = _sanitize_profile_choice(
        primary_struggle_area or "Understanding concepts",
        ALLOWED_PRIMARY_STRUGGLE,
        "Understanding concepts",
    )
    normalized_learning_pace = _sanitize_profile_choice(
        (learning_pace or "average").lower(),
        ALLOWED_LEARNING_PACE,
        "average",
    )
    normalized_question_style_preference = _sanitize_profile_choice(
        (question_style_preference or "multiple-choice only").lower(),
        ALLOWED_QUESTION_STYLE,
        "multiple-choice only",
    )
    normalized_feedback_detail_level = _sanitize_profile_choice(
        (feedback_detail_level or "detailed").lower(),
        ALLOWED_FEEDBACK_DETAIL,
        "detailed",
    )
    normalized_preferred_examples_domain = _sanitize_profile_choice(
        (preferred_examples_domain or "daily life").lower(),
        ALLOWED_EXAMPLES_DOMAIN,
        "daily life",
    )
    normalized_gamification_preference = _sanitize_profile_choice(
        (gamification_preference or "yes").lower(),
        ALLOWED_GAMIFICATION_PREFERENCE,
        "yes",
    )
    normalized_avg_study_time = int(avg_study_time_minutes_per_day) if avg_study_time_minutes_per_day is not None else None
    if normalized_avg_study_time is not None and normalized_avg_study_time < 0:
        normalized_avg_study_time = None
    normalized_study_time_morning_hours = float(study_time_morning_hours) if study_time_morning_hours is not None else None
    if normalized_study_time_morning_hours is not None and normalized_study_time_morning_hours < 0:
        normalized_study_time_morning_hours = None
    normalized_study_time_afternoon_hours = float(study_time_afternoon_hours) if study_time_afternoon_hours is not None else None
    if normalized_study_time_afternoon_hours is not None and normalized_study_time_afternoon_hours < 0:
        normalized_study_time_afternoon_hours = None
    normalized_study_time_evening_hours = float(study_time_evening_hours) if study_time_evening_hours is not None else None
    if normalized_study_time_evening_hours is not None and normalized_study_time_evening_hours < 0:
        normalized_study_time_evening_hours = None
    normalized_study_time_night_hours = float(study_time_night_hours) if study_time_night_hours is not None else None
    if normalized_study_time_night_hours is not None and normalized_study_time_night_hours < 0:
        normalized_study_time_night_hours = None
    normalized_time_limit = int(time_limit_per_question_seconds) if time_limit_per_question_seconds is not None else None
    if normalized_time_limit is not None and normalized_time_limit <= 0:
        normalized_time_limit = None

    normalized_confidence_per_topic = confidence_per_topic if isinstance(confidence_per_topic, dict) else {}
    normalized_assignment_deadlines = assignment_deadlines if isinstance(assignment_deadlines, list) else []
    normalized_custom_rubric_weights = custom_rubric_weights if isinstance(custom_rubric_weights, dict) else {}
    normalized_minimum_passing_thresholds = minimum_passing_thresholds if isinstance(minimum_passing_thresholds, dict) else {}
    normalized_export_formats = [fmt for fmt in (export_formats or []) if fmt in ALLOWED_EXPORT_FORMATS]

    with sqlite3.connect(STUDENT_PROFILE_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        resolved_student_id = (student_id or "").strip() or None
        existing_row = None
        if resolved_student_id:
            existing_row = conn.execute(
                "SELECT * FROM student_profile WHERE student_id = ?",
                (resolved_student_id,),
            ).fetchone()
        if not resolved_student_id:
            resolved_student_id = _generate_student_id(conn, now_ts)

        if existing_row:
            performance_history_raw = existing_row["performance_history"] or "[]"
            performance_avg_score = float(existing_row["performance_avg_score"] or 0)
            performance_sessions = int(existing_row["performance_sessions"] or 0)
            created_at = float(existing_row["created_at"] or now_ts)
        else:
            performance_history_raw = "[]"
            performance_avg_score = 0.0
            performance_sessions = 0
            created_at = now_ts

        conn.execute(
            """
            INSERT INTO student_profile (
                student_id, candidate_name, student_class, curriculum, subject, language,
                teaching_mode, skill_level_per_subject, goal, weak_areas, explanation_style,
                avg_study_time_minutes_per_day, preferred_difficulty_level, attention_span_preference,
                primary_struggle_area, learning_pace,
                question_style_preference, time_limit_per_question_seconds, feedback_detail_level,
                confidence_per_topic, preferred_examples_domain, gamification_preference,
                class_roster_csv_text, assignment_deadlines_json, custom_rubric_weights_json,
                minimum_passing_thresholds_json, export_formats_json, plagiarism_check_enabled,
                performance_history, performance_avg_score, performance_sessions, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                candidate_name=excluded.candidate_name,
                student_class=excluded.student_class,
                curriculum=excluded.curriculum,
                subject=excluded.subject,
                language=excluded.language,
                teaching_mode=excluded.teaching_mode,
                skill_level_per_subject=excluded.skill_level_per_subject,
                goal=excluded.goal,
                weak_areas=excluded.weak_areas,
                explanation_style=excluded.explanation_style,
                avg_study_time_minutes_per_day=excluded.avg_study_time_minutes_per_day,
                study_time_morning_hours=excluded.study_time_morning_hours,
                study_time_afternoon_hours=excluded.study_time_afternoon_hours,
                study_time_evening_hours=excluded.study_time_evening_hours,
                study_time_night_hours=excluded.study_time_night_hours,
                preferred_difficulty_level=excluded.preferred_difficulty_level,
                attention_span_preference=excluded.attention_span_preference,
                primary_struggle_area=excluded.primary_struggle_area,
                learning_pace=excluded.learning_pace,
                question_style_preference=excluded.question_style_preference,
                time_limit_per_question_seconds=excluded.time_limit_per_question_seconds,
                feedback_detail_level=excluded.feedback_detail_level,
                confidence_per_topic=excluded.confidence_per_topic,
                preferred_examples_domain=excluded.preferred_examples_domain,
                gamification_preference=excluded.gamification_preference,
                class_roster_csv_text=excluded.class_roster_csv_text,
                assignment_deadlines_json=excluded.assignment_deadlines_json,
                custom_rubric_weights_json=excluded.custom_rubric_weights_json,
                minimum_passing_thresholds_json=excluded.minimum_passing_thresholds_json,
                export_formats_json=excluded.export_formats_json,
                plagiarism_check_enabled=excluded.plagiarism_check_enabled,
                performance_history=excluded.performance_history,
                performance_avg_score=excluded.performance_avg_score,
                performance_sessions=excluded.performance_sessions,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at
            """,
            (
                resolved_student_id,
                candidate_name,
                student_class,
                curriculum,
                normalized_subject,
                normalized_language,
                normalized_teaching_mode,
                normalized_skill_level_per_subject,
                normalized_goal,
                normalized_weak_areas,
                normalized_explanation_style,
                normalized_avg_study_time,
                normalized_study_time_morning_hours,
                normalized_study_time_afternoon_hours,
                normalized_study_time_evening_hours,
                normalized_study_time_night_hours,
                normalized_preferred_difficulty_level,
                normalized_attention_span_preference,
                normalized_primary_struggle_area,
                normalized_learning_pace,
                normalized_question_style_preference,
                normalized_time_limit,
                normalized_feedback_detail_level,
                json.dumps(normalized_confidence_per_topic),
                normalized_preferred_examples_domain,
                normalized_gamification_preference,
                (class_roster_csv_text or "").strip() or None,
                json.dumps(normalized_assignment_deadlines),
                json.dumps(normalized_custom_rubric_weights),
                json.dumps(normalized_minimum_passing_thresholds),
                json.dumps(normalized_export_formats),
                1 if plagiarism_check_enabled else 0,
                performance_history_raw,
                performance_avg_score,
                performance_sessions,
                created_at,
                now_ts,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM student_profile WHERE student_id = ?",
            (resolved_student_id,),
        ).fetchone()
    return _row_to_profile_dict(row)

def _get_student_profile(student_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(STUDENT_PROFILE_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        if student_id:
            row = conn.execute(
                "SELECT * FROM student_profile WHERE student_id = ?",
                (student_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM student_profile ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
    if not row:
        return None
    return _row_to_profile_dict(row)


def _list_student_profiles(limit: int = 200) -> List[Dict[str, Any]]:
    normalized_limit = max(1, min(1000, int(limit or 200)))
    with sqlite3.connect(STUDENT_PROFILE_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM student_profile ORDER BY updated_at DESC LIMIT ?",
            (normalized_limit,),
        ).fetchall()
    return [_row_to_profile_dict(row) for row in rows]

def _update_student_performance(student_id: str,
                                module: str,
                                score_percent: float,
                                duration_minutes: Optional[int] = None,
                                metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized_student_id = (student_id or "").strip()
    if not normalized_student_id:
        raise HTTPException(status_code=400, detail="student_id is required")

    normalized_module = (module or "").strip().lower() or "general"
    normalized_score = max(0.0, min(100.0, float(score_percent)))
    normalized_duration = int(duration_minutes) if duration_minutes is not None else None
    if normalized_duration is not None and normalized_duration < 0:
        normalized_duration = None
    payload_metadata = metadata if isinstance(metadata, dict) else {}

    with sqlite3.connect(STUDENT_PROFILE_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM student_profile WHERE student_id = ?",
            (normalized_student_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="student profile not found")

        try:
            history = json.loads(row["performance_history"] or "[]")
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

        entry = {
            "timestamp": time.time(),
            "module": normalized_module,
            "score_percent": round(normalized_score, 2),
            "duration_minutes": normalized_duration,
            "metadata": payload_metadata,
        }
        history.append(entry)
        history = history[-100:]

        sessions = int(row["performance_sessions"] or 0) + 1
        prev_avg = float(row["performance_avg_score"] or 0)
        new_avg = ((prev_avg * (sessions - 1)) + normalized_score) / sessions
        now_ts = time.time()

        conn.execute(
            """
            UPDATE student_profile
            SET performance_history = ?, performance_avg_score = ?, performance_sessions = ?, updated_at = ?
            WHERE student_id = ?
            """,
            (json.dumps(history), new_avg, sessions, now_ts, normalized_student_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM student_profile WHERE student_id = ?",
            (normalized_student_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO student_activity_log (
                student_id, feature, event_type, score_percent, duration_minutes, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_student_id,
                normalized_module,
                str(payload_metadata.get("event_type") or "attempt").strip().lower() or "attempt",
                normalized_score,
                normalized_duration,
                json.dumps(payload_metadata),
                now_ts,
            ),
        )
        conn.commit()
    return _row_to_profile_dict(updated)


def _get_student_activity_log(student_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    normalized_student_id = (student_id or "").strip()
    if not normalized_student_id:
        return []
    safe_limit = max(1, min(int(limit or 100), 500))
    with sqlite3.connect(STUDENT_PROFILE_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT student_id, feature, event_type, score_percent, duration_minutes, metadata_json, created_at
            FROM student_activity_log
            WHERE student_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_student_id, safe_limit),
        ).fetchall()
    activities: List[Dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except Exception:
            metadata = {}
        activities.append(
            {
                "student_id": row["student_id"],
                "feature": row["feature"],
                "event_type": row["event_type"],
                "score_percent": row["score_percent"],
                "duration_minutes": row["duration_minutes"],
                "metadata": metadata,
                "created_at": row["created_at"],
            }
        )
    return activities


def _build_student_progress_summary(profile: Dict[str, Any], activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    feature_counts: Dict[str, int] = {}
    feature_scores: Dict[str, List[float]] = {}
    for item in activities:
        feature = str(item.get("feature") or "general").strip().lower() or "general"
        feature_counts[feature] = feature_counts.get(feature, 0) + 1
        score = item.get("score_percent")
        if score is not None:
            feature_scores.setdefault(feature, []).append(float(score))

    feature_summary = []
    for feature, count in sorted(feature_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        scores = feature_scores.get(feature, [])
        avg_score = round(sum(scores) / len(scores), 2) if scores else None
        feature_summary.append({"feature": feature, "attempts": count, "average_score": avg_score})

    first_seen = min((float(item.get("created_at") or 0) for item in activities), default=None) if activities else None
    last_seen = max((float(item.get("created_at") or 0) for item in activities), default=None) if activities else None

    return {
        "total_entries": len(activities),
        "unique_features": len(feature_counts),
        "feature_summary": feature_summary,
        "first_seen_at": first_seen,
        "last_seen_at": last_seen,
        "latest_activity": activities[0] if activities else None,
        "profile_score_summary": {
            "performance_sessions": profile.get("performance_sessions", 0),
            "performance_avg_score": profile.get("performance_avg_score", 0),
        },
    }


def _generate_student_progress_report_pdf(profile: Dict[str, Any], activities: List[Dict[str, Any]], filename: str) -> str:
    from reportlab.lib.enums import TA_LEFT

    doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ProgressTitle", parent=styles["Title"], fontSize=20, leading=24, textColor=colors.HexColor("#1f7a8c"), alignment=TA_LEFT)
    section_style = ParagraphStyle("ProgressSection", parent=styles["Heading2"], fontSize=12, leading=14, textColor=colors.HexColor("#0f172a"))
    body_style = ParagraphStyle("ProgressBody", parent=styles["BodyText"], fontSize=9.5, leading=12)

    def _fmt_time(ts: Any) -> str:
        try:
            return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "Unknown"

    story = [
        Paragraph("Student Progress Report", title_style),
        Spacer(1, 10),
        Paragraph(
            f"<b>Name:</b> {profile.get('candidate_name') or 'N/A'}<br/>"
            f"<b>Student ID:</b> {profile.get('student_id') or 'N/A'}<br/>"
            f"<b>Class:</b> {profile.get('student_class') or 'N/A'}<br/>"
            f"<b>Curriculum:</b> {profile.get('curriculum') or 'N/A'}<br/>"
            f"<b>Language:</b> {profile.get('language') or 'English'}",
            body_style,
        ),
        Spacer(1, 10),
    ]

    summary = _build_student_progress_summary(profile, activities)
    summary_table = Table(
        [
            ["Total Entries", "Unique Features", "Profile Attempts", "Profile Avg Score"],
            [
                str(summary["total_entries"]),
                str(summary["unique_features"]),
                str(summary["profile_score_summary"]["performance_sessions"]),
                f"{float(summary['profile_score_summary']['performance_avg_score'] or 0):.1f}%",
            ],
        ],
        colWidths=[110, 110, 110, 110],
    )
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Feature Usage Summary", section_style))
    if summary["feature_summary"]:
        feature_rows = [["Feature", "Attempts", "Avg Score"]]
        for row in summary["feature_summary"]:
            feature_rows.append([
                row["feature"],
                str(row["attempts"]),
                f"{row['average_score']:.1f}%" if row["average_score"] is not None else "N/A",
            ])
        feature_table = Table(feature_rows, colWidths=[180, 90, 100])
        feature_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14b8a6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ffffff")),
        ]))
        story.append(feature_table)
    else:
        story.append(Paragraph("No feature activity has been recorded yet.", body_style))

    story.append(Spacer(1, 12))
    story.append(Paragraph("Recent Activity", section_style))
    if activities:
        activity_rows = [["Time", "Feature", "Event", "Score", "Duration", "Details"]]
        for item in activities[:50]:
            metadata = item.get("metadata") or {}
            detail_bits = []
            for key in ("difficulty", "question_count", "essay_topic", "topic", "parameters"):
                value = metadata.get(key)
                if value not in (None, "", [], {}):
                    detail_bits.append(f"{key}: {value}")
            activity_rows.append([
                _fmt_time(item.get("created_at")),
                str(item.get("feature") or "general"),
                str(item.get("event_type") or "attempt"),
                f"{float(item.get('score_percent') or 0):.1f}%" if item.get("score_percent") is not None else "N/A",
                f"{int(item.get('duration_minutes'))} min" if item.get("duration_minutes") is not None else "N/A",
                "; ".join(detail_bits) or "-",
            ])
        activity_table = Table(activity_rows, colWidths=[95, 70, 55, 45, 50, 145])
        activity_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ffffff")),
        ]))
        story.append(activity_table)
    else:
        story.append(Paragraph("No activity entries found for this student.", body_style))

    doc.build(story)
    return filename


def _get_student_progress_bundle(student_id: str, limit: int = 100) -> Dict[str, Any]:
    profile = _get_student_profile(student_id)
    if not profile:
        return {"found": False, "profile": None, "activities": [], "summary": None}
    activities = _get_student_activity_log(student_id, limit=limit)
    summary = _build_student_progress_summary(profile, activities)
    return {"found": True, "profile": profile, "activities": activities, "summary": summary}

_init_student_profile_db()

def get_next_certificate_number() -> str:
    """Return the next certificate number (e.g., AGAI001, AGAI002)."""
    try:
        with open(CERT_COUNTER_FILE, 'r') as f:
            data = json.load(f)
            last_num = data.get('last_number', 0)
    except (FileNotFoundError, json.JSONDecodeError):
        last_num = 0

    next_num = last_num + 1
    cert_number = f"AGAI{next_num:03d}"

    with open(CERT_COUNTER_FILE, 'w') as f:
        json.dump({'last_number': next_num}, f)

    return cert_number

# ---------- Core Evaluation Function ----------
def evaluate_essay(essay: str,
                   parameters: List[str],
                   candidate_name: Optional[str] = None,
                   student_class: Optional[str] = None,
                   curriculum: Optional[str] = None,
                   subject: Optional[str] = None,
                   language: Optional[str] = "English",
                   teaching_mode: Optional[str] = None,
                   learner_context: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate essay on given parameters using Gemini AI."""
    if not essay.strip():
        raise HTTPException(status_code=400, detail="Essay text cannot be empty")

    # Validate parameters
    invalid_params = [p for p in parameters if p.lower() not in VALID_EVAL_PARAMS]
    if invalid_params:
        raise HTTPException(status_code=400, detail=f"Invalid parameters: {invalid_params}")

    scores = {}
    feedbacks = {}
    normalized_language = (language or "English").strip() or "English"
    normalized_teaching_mode = (teaching_mode or "").strip()
    normalized_subject = (subject or "").strip()
    profile_note = learner_context or ""
    if not profile_note and (student_class or curriculum or normalized_subject or normalized_language or normalized_teaching_mode):
        profile_note = (
            f"Learner context:\n"
            f"- Preferred subject: {normalized_subject or 'Not specified'}\n"
            f"- Class/Grade: {student_class or 'Not specified'}\n"
            f"- Curriculum/Board: {curriculum or 'Not specified'}\n"
            f"- Language: {normalized_language}\n"
            f"- Teaching mode: {normalized_teaching_mode or 'Not specified'}\n"
        )

    for param in parameters:
        prompt = build_essay_parameter_prompt(
            profile_note=profile_note,
            parameter=param,
            language=normalized_language,
            teaching_mode=normalized_teaching_mode,
            essay=essay,
        )
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            result = _extract_json_object(response.text)
            scores[param] = result.get("score", 0)
            feedbacks[param] = result.get("feedback", "No feedback provided")
        except Exception as e:
            logger.error(f"Failed to evaluate {param}: {e}")
            scores[param] = 0
            feedbacks[param] = f"Evaluation failed: {str(e)}"

    # Calculate average
    avg_score = sum(scores.values()) / len(scores) if scores else 0

    # Overall feedback
    overall_feedback = f"Average score: {avg_score:.2f}/10. {'Excellent work!' if avg_score >= 8 else 'Good effort, room for improvement.'}"

    return {
        "scores": scores,
        "feedbacks": feedbacks,
        "avg_score": avg_score,
        "overall_feedback": overall_feedback,
        "essay_topic": _get_essay_topic(essay)
    }

# ---------- Generate Question Function ----------
def generate_question(basis: str,
                      student_class: Optional[str] = None,
                      curriculum: Optional[str] = None,
                      subject: Optional[str] = None,
                      language: Optional[str] = "English",
                      teaching_mode: Optional[str] = None,
                      learner_context: Optional[str] = None) -> str:
    """Generate an essay question based on the given basis."""
    if not basis.strip():
        raise HTTPException(status_code=400, detail="Basis cannot be empty")

    normalized_language = (language or "English").strip() or "English"
    normalized_teaching_mode = (teaching_mode or "").strip()
    normalized_subject = (subject or "").strip()
    profile_note = learner_context or ""
    if not profile_note and (student_class or curriculum or normalized_subject or normalized_language or normalized_teaching_mode):
        profile_note = (
            f"\nLearner profile:\n"
            f"- Preferred subject: {normalized_subject or 'Not specified'}\n"
            f"- Class/Grade: {student_class or 'Not specified'}\n"
            f"- Curriculum/Board: {curriculum or 'Not specified'}\n"
            f"- Language: {normalized_language}\n"
            f"- Teaching mode: {normalized_teaching_mode or 'Not specified'}\n"
            f"Adjust vocabulary depth, complexity, and context to suit this learner profile.\n"
        )

    prompt = build_essay_question_prompt(
        basis=basis,
        profile_note=profile_note,
        language=normalized_language,
    )
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        question = response.text.strip()
        return question
    except Exception as e:
        logger.error(f"Failed to generate question: {e}")
        return f"Write an essay about {basis}."

def generate_sample_essay(essay_topic: str,
                          parameters: List[str],
                          student_class: Optional[str] = None,
                          curriculum: Optional[str] = None,
                          subject: Optional[str] = None,
                          language: Optional[str] = "English",
                          teaching_mode: Optional[str] = None,
                          learner_context: Optional[str] = None) -> str:
    if not essay_topic.strip():
        raise HTTPException(status_code=400, detail="Essay topic cannot be empty")

    invalid_params = [p for p in parameters if p.lower() not in VALID_EVAL_PARAMS]
    if invalid_params:
        raise HTTPException(status_code=400, detail=f"Invalid parameters: {invalid_params}")

    normalized_language = (language or "English").strip() or "English"
    normalized_teaching_mode = (teaching_mode or "").strip()
    normalized_subject = (subject or "").strip()
    profile_note = learner_context or ""
    if not profile_note and (student_class or curriculum or normalized_subject or normalized_language or normalized_teaching_mode):
        profile_note = (
            f"Learner context:\n"
            f"- Preferred subject: {normalized_subject or 'Not specified'}\n"
            f"- Class/Grade: {student_class or 'Not specified'}\n"
            f"- Curriculum/Board: {curriculum or 'Not specified'}\n"
            f"- Language: {normalized_language}\n"
            f"- Teaching mode: {normalized_teaching_mode or 'Not specified'}\n"
        )

    selected_params = ", ".join(parameters)
    prompt = build_sample_essay_prompt(
        essay_topic=essay_topic,
        selected_parameters=selected_params,
        profile_note=profile_note,
        language=normalized_language,
        teaching_mode=normalized_teaching_mode,
    )
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        sample_essay = response.text.strip()
        return sample_essay
    except Exception as e:
        logger.error(f"Failed to generate sample essay: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate sample essay: {e}")

# ---------- Certificate Generation ----------
def generate_pass_certificate(candidate_name: str,
                              scores_percent: Dict[str, float],
                              essay_topic: str | None,
                              certificate_number: str,
                              student_class: Optional[str] = None,
                              curriculum: Optional[str] = None,
                              filename: str = "certificate.pdf",
                              logo_path: str = "logo.png"):
    """
    Generate a one-page certificate.
    If essay_topic is None or multiple essays, use generic text.
    """
    doc = SimpleDocTemplate(filename, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'],
                                 fontSize=30, alignment=1, textColor=colors.HexColor("#0A4FA3"), spaceAfter=15)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Heading2'],
                                    alignment=1, textColor=colors.HexColor("#1F77D0"), fontSize=18, spaceAfter=8)
    name_style = ParagraphStyle('NameStyle', alignment=1, fontSize=26,
                                textColor=colors.HexColor("#0A4FA3"), spaceAfter=20)
    body_style = ParagraphStyle('BodyStyle', parent=styles['BodyText'],
                                alignment=1, fontSize=12, textColor=colors.HexColor("#333333"), spaceAfter=10)

    # Outer border
    border = Table([[""]], colWidths=[450], rowHeights=[650])
    border.setStyle(TableStyle([('GRID', (0,0), (-1,-1), 5, colors.HexColor("#1F77D0")),
                                ('BACKGROUND', (0,0), (-1,-1), colors.white)]))
    story.append(border)
    story.append(Spacer(1, -620))

    # Logo
    try:
        logo = Image(logo_path, width=1.2*inch, height=1.2*inch)
        logo.hAlign = 'CENTER'
        story.append(logo)
    except Exception:
        story.append(Paragraph("<i>Logo missing</i>", body_style))

    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("<b>AgenixAi</b>", subtitle_style))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph("Certificate of Achievement", title_style))
    story.append(Paragraph("Awarded To", subtitle_style))
    story.append(Paragraph(f"<b>{candidate_name}</b>", name_style))
    story.append(Paragraph(
        f"Class/Grade: {student_class or 'Not specified'} | Curriculum/Board: {curriculum or 'Not specified'}",
        body_style,
    ))

    # Decide the award text based on whether we have a specific essay topic
    if essay_topic:
        award_text = (f"This certificate is proudly presented for achieving outstanding performance "
                      f"scoring <b>70% or above</b> in all evaluation categories for essay: <i>{essay_topic}</i>.")
    else:
        award_text = (f"This certificate is proudly presented for achieving outstanding performance "
                      f"scoring <b>70% or above</b> in all evaluation categories across the session.")

    story.append(Paragraph(award_text, body_style))
    story.append(Spacer(1, 0.2*inch))

    # Certificate number
    story.append(Paragraph(f"Certificate No: {certificate_number}", body_style))
    story.append(Spacer(1, 0.3*inch))

    # Table of scores
    table_data = [["Category", "Score (%)"]]
    for cat, score in scores_percent.items():
        table_data.append([cat.capitalize(), f"{score:.1f}%"])
    score_table = Table(table_data, colWidths=[250, 130])
    score_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 14),
        ('FONTSIZE', (0,1), (-1,-1), 12),
        ('GRID', (0,0), (-1,-1), 1, colors.grey),
        ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 0.4*inch))

    # Pie charts
    story.append(Paragraph("<b>Performance Overview</b>", subtitle_style))
    story.append(Spacer(1, 0.2*inch))

    pie_tables = []
    for cat, score in scores_percent.items():
        d = Drawing(1.2*inch, 1.2*inch)
        pie = Pie()
        pie.x = 0.1*inch
        pie.y = 0.1*inch
        pie.width = 1.0*inch
        pie.height = 1.0*inch
        pie.data = [score, 100 - score]
        pie.slices[0].fillColor = colors.HexColor("#1F77D0")
        pie.slices[1].fillColor = colors.lightgrey
        pie.slices[1].strokeColor = None
        pie.simpleLabels = 0
        d.add(pie)
        label = Paragraph(cat.capitalize(), body_style)
        small_table = Table([[d], [label]], colWidths=[1.2*inch])
        small_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))
        pie_tables.append(small_table)

    if pie_tables:
        main_table = Table([pie_tables],
                           colWidths=[1.2*inch] * len(pie_tables))
        main_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(main_table)
        story.append(Spacer(1, 0.4*inch))

    # Signature
    story.append(Paragraph("<br/><br/>__________________________", body_style))
    story.append(Paragraph("<b>AgenixAi Evaluation System</b>", body_style))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("Congratulations on your exceptional performance!", body_style))

    # Watermark
    def add_watermark(canvas_obj, doc_obj):
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.08)
            canvas_obj.drawImage(logo_path, x=150, y=250, width=300, height=300,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except:
            pass

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    return filename

# ---------- Detailed Session Report PDF ----------
def generate_detailed_report_pdf(session_history: List[Dict[str, Any]],
                                 candidate_name: str,
                                 filename: str = "detailed_report.pdf"):
    """
    Create a multi-page PDF with summary, trends, and detailed feedback.
    Each essay's short topic (if generated) is displayed.
    """
    doc = SimpleDocTemplate(filename, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('ReportTitle', parent=styles['Title'],
                                  fontSize=24, alignment=1, textColor=colors.HexColor("#0A4FA3"), spaceAfter=20)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'],
                                   fontSize=16, textColor=colors.HexColor("#1F77D0"), spaceAfter=12)
    normal_style = styles['Normal']

    story.append(Paragraph(f"Detailed Essay Evaluation Report for {candidate_name}", title_style))
    story.append(Spacer(1, 0.2*inch))

    if not session_history:
        story.append(Paragraph("No essays were evaluated.", normal_style))
        doc.build(story)
        return filename

    profile_class = str(session_history[0].get('student_class') or 'Not specified')
    profile_curriculum = str(session_history[0].get('curriculum') or 'Not specified')
    story.append(Paragraph(
        f"<b>Student Profile:</b> Class/Grade: {profile_class} | Curriculum/Board: {profile_curriculum}",
        normal_style,
    ))
    story.append(Spacer(1, 0.15*inch))

    all_params = set()
    for run in session_history:
        all_params.update(run['scores'].keys())
    all_params = sorted(all_params)

    # 1. Summary table
    story.append(Paragraph("Session Summary", heading_style))
    data = [["Essay #", "Date/Time", "Avg Score"] + [p.capitalize() for p in all_params]]
    for idx, run in enumerate(session_history, 1):
        timestamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(run.get('timestamp', 0)))
        row = [str(idx), timestamp, f"{run['avg_score']:.2f}"]
        for p in all_params:
            sc = run['scores'].get(p)
            row.append(str(sc) if sc is not None else "N/A")
        data.append(row)

    col_widths = [0.5*inch, 1.2*inch, 0.8*inch] + [0.7*inch] * len(all_params)
    summary_table = Table(data, colWidths=col_widths)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 1, colors.grey),
        ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
    ]))
    story.append(summary_table)
    story.append(PageBreak())

    # 2. Detailed feedback for each essay
    for idx, run in enumerate(session_history, 1):
        story.append(Paragraph(f"Essay {idx}: {run.get('essay_topic', 'Untitled')}", heading_style))
        if run.get('essay_question'):
            story.append(Paragraph(f"<b>Question:</b> {run['essay_question']}", normal_style))
        story.append(Spacer(1, 0.1*inch))
        story.append(Paragraph(f"<b>Average Score:</b> {run['avg_score']:.2f}/10", normal_style))
        story.append(Spacer(1, 0.1*inch))

        # Scores table
        score_data = [["Parameter", "Score", "Feedback"]]
        for param in all_params:
            score = run['scores'].get(param)
            feedback = run['feedbacks'].get(param, "")
            score_data.append([param.capitalize(), str(score) if score is not None else "N/A", feedback])
        score_table = Table(score_data, colWidths=[1.5*inch, 0.5*inch, 4*inch])
        score_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('GRID', (0,0), (-1,-1), 1, colors.grey),
            ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))
        story.append(score_table)
        story.append(Spacer(1, 0.2*inch))

        # Overall feedback
        story.append(Paragraph(f"<b>Overall Feedback:</b> {run.get('overall_feedback', '')}", normal_style))
        story.append(PageBreak())

    doc.build(story)
    return filename

@app.get("/student-profile")
async def get_student_profile_endpoint(student_id: Optional[str] = None):
    profile = _get_student_profile(student_id)
    if not profile:
        return {"found": False, "profile": None}
    return {"found": True, "profile": profile}


@app.get("/student-profiles")
async def list_student_profiles_endpoint(limit: int = 200):
    profiles = _list_student_profiles(limit=limit)
    return {"count": len(profiles), "profiles": profiles}

@app.get("/student-profile/{student_id}")
async def get_student_profile_by_id_endpoint(student_id: str):
    profile = _get_student_profile(student_id)
    if not profile:
        return {"found": False, "profile": None}
    return {"found": True, "profile": profile}

@app.post("/student-profile")
async def save_student_profile_endpoint(request: StudentProfileRequest):
    student_id = (request.student_id or "").strip() or None
    candidate_name = (request.candidate_name or "").strip()
    student_class = (request.student_class or "").strip()
    curriculum = (request.curriculum or "").strip()
    subject = (request.subject or "").strip() or None
    language = (request.language or "English").strip() or "English"
    inferred_teaching_mode = _map_learning_style_to_teaching_mode(request.learning_style_preference)
    teaching_mode = (request.teaching_mode or "").strip() or inferred_teaching_mode or "Step-by-step"
    skill_level_per_subject = (request.skill_level_per_subject or "").strip() or None
    goal = (request.goal or "General learning").strip() or "General learning"
    weak_areas = (request.weak_areas or "").strip() or None
    explanation_style = (request.explanation_style or "").strip() or (
        "Example-based" if teaching_mode == "Example-based" else "Step-by-step"
    )
    avg_study_time_minutes_per_day = request.avg_study_time_minutes_per_day
    study_time_morning_hours = request.study_time_morning_hours
    study_time_afternoon_hours = request.study_time_afternoon_hours
    study_time_evening_hours = request.study_time_evening_hours
    study_time_night_hours = request.study_time_night_hours
    preferred_difficulty_level = (request.preferred_difficulty_level or "Medium").strip() or "Medium"
    attention_span_preference = (request.attention_span_preference or "10-20 min").strip() or "10-20 min"
    primary_struggle_area = (request.primary_struggle_area or "Understanding concepts").strip() or "Understanding concepts"
    learning_pace = (request.learning_pace or "average").strip().lower() or "average"
    question_style_preference = (request.question_style_preference or "multiple-choice only").strip().lower() or "multiple-choice only"
    time_limit_per_question_seconds = request.time_limit_per_question_seconds
    feedback_detail_level = (request.feedback_detail_level or "detailed").strip().lower() or "detailed"
    preferred_examples_domain = (request.preferred_examples_domain or "daily life").strip().lower() or "daily life"
    gamification_preference = (request.gamification_preference or "yes").strip().lower() or "yes"

    if not candidate_name:
        raise HTTPException(status_code=400, detail="candidate_name is required")
    if not student_class:
        raise HTTPException(status_code=400, detail="student_class is required")
    if not curriculum:
        raise HTTPException(status_code=400, detail="curriculum is required")
    if goal not in ALLOWED_GOALS:
        raise HTTPException(status_code=400, detail=f"goal must be one of {sorted(ALLOWED_GOALS)}")
    if explanation_style not in ALLOWED_EXPLANATION_STYLES:
        raise HTTPException(status_code=400, detail=f"explanation_style must be one of {sorted(ALLOWED_EXPLANATION_STYLES)}")
    if preferred_difficulty_level not in ALLOWED_DIFFICULTY_LEVELS:
        raise HTTPException(status_code=400, detail=f"preferred_difficulty_level must be one of {sorted(ALLOWED_DIFFICULTY_LEVELS)}")
    if skill_level_per_subject and skill_level_per_subject not in ALLOWED_SELF_LEVELS:
        raise HTTPException(status_code=400, detail=f"skill_level_per_subject must be one of {sorted(ALLOWED_SELF_LEVELS)}")
    if attention_span_preference not in ALLOWED_ATTENTION_SPAN:
        raise HTTPException(status_code=400, detail=f"attention_span_preference must be one of {sorted(ALLOWED_ATTENTION_SPAN)}")
    if primary_struggle_area not in ALLOWED_PRIMARY_STRUGGLE:
        raise HTTPException(status_code=400, detail=f"primary_struggle_area must be one of {sorted(ALLOWED_PRIMARY_STRUGGLE)}")
    if learning_pace not in ALLOWED_LEARNING_PACE:
        raise HTTPException(status_code=400, detail=f"learning_pace must be one of {sorted(ALLOWED_LEARNING_PACE)}")
    if question_style_preference not in ALLOWED_QUESTION_STYLE:
        raise HTTPException(status_code=400, detail=f"question_style_preference must be one of {sorted(ALLOWED_QUESTION_STYLE)}")
    if feedback_detail_level not in ALLOWED_FEEDBACK_DETAIL:
        raise HTTPException(status_code=400, detail=f"feedback_detail_level must be one of {sorted(ALLOWED_FEEDBACK_DETAIL)}")
    if preferred_examples_domain not in ALLOWED_EXAMPLES_DOMAIN:
        raise HTTPException(status_code=400, detail=f"preferred_examples_domain must be one of {sorted(ALLOWED_EXAMPLES_DOMAIN)}")
    if gamification_preference not in ALLOWED_GAMIFICATION_PREFERENCE:
        raise HTTPException(status_code=400, detail=f"gamification_preference must be one of {sorted(ALLOWED_GAMIFICATION_PREFERENCE)}")
    if avg_study_time_minutes_per_day is not None and avg_study_time_minutes_per_day < 0:
        raise HTTPException(status_code=400, detail="avg_study_time_minutes_per_day must be >= 0")
    if study_time_morning_hours is not None and study_time_morning_hours < 0:
        raise HTTPException(status_code=400, detail="study_time_morning_hours must be >= 0")
    if study_time_afternoon_hours is not None and study_time_afternoon_hours < 0:
        raise HTTPException(status_code=400, detail="study_time_afternoon_hours must be >= 0")
    if study_time_evening_hours is not None and study_time_evening_hours < 0:
        raise HTTPException(status_code=400, detail="study_time_evening_hours must be >= 0")
    if study_time_night_hours is not None and study_time_night_hours < 0:
        raise HTTPException(status_code=400, detail="study_time_night_hours must be >= 0")
    if time_limit_per_question_seconds is not None and time_limit_per_question_seconds <= 0:
        raise HTTPException(status_code=400, detail="time_limit_per_question_seconds must be > 0")
    if request.export_formats:
        invalid_formats = [fmt for fmt in request.export_formats if fmt not in ALLOWED_EXPORT_FORMATS]
        if invalid_formats:
            raise HTTPException(status_code=400, detail=f"export_formats must be subset of {sorted(ALLOWED_EXPORT_FORMATS)}")

    profile = _save_student_profile(
        candidate_name,
        student_class,
        curriculum,
        subject,
        language,
        teaching_mode,
        student_id,
        skill_level_per_subject,
        goal,
        weak_areas,
        explanation_style,
        avg_study_time_minutes_per_day,
        study_time_morning_hours,
        study_time_afternoon_hours,
        study_time_evening_hours,
        study_time_night_hours,
        preferred_difficulty_level,
        attention_span_preference,
        primary_struggle_area,
        learning_pace,
        question_style_preference,
        time_limit_per_question_seconds,
        feedback_detail_level,
        request.confidence_per_topic,
        preferred_examples_domain,
        gamification_preference,
        request.class_roster_csv_text,
        request.assignment_deadlines,
        request.custom_rubric_weights,
        request.minimum_passing_thresholds,
        request.export_formats,
        request.plagiarism_check_enabled,
    )
    return {"status": "saved", "profile": profile}

@app.post("/student-profile/performance")
async def update_student_performance_endpoint(request: StudentPerformanceRequest):
    module = (request.module or "").strip().lower()
    if not module:
        raise HTTPException(status_code=400, detail="module is required")
    if request.score_percent < 0 or request.score_percent > 100:
        raise HTTPException(status_code=400, detail="score_percent must be between 0 and 100")

    profile = _update_student_performance(
        request.student_id,
        module,
        request.score_percent,
        request.duration_minutes,
        request.metadata,
    )
    return {"status": "updated", "profile": profile}


@app.get("/student-progress/{student_id}")
async def get_student_progress_endpoint(student_id: str, limit: int = 100):
    bundle = _get_student_progress_bundle(student_id, limit=limit)
    if not bundle["found"]:
        return {"found": False, "profile": None, "activities": [], "summary": None}
    return bundle


@app.post("/student-progress/report")
async def generate_student_progress_report_endpoint(request: StudentProgressReportRequest):
    student_id = (request.student_id or "").strip()
    if not student_id:
        raise HTTPException(status_code=400, detail="student_id is required")

    bundle = _get_student_progress_bundle(student_id, limit=request.limit)
    if not bundle["found"]:
        raise HTTPException(status_code=404, detail="student profile not found")

    profile = bundle["profile"] or {}
    activities = bundle["activities"] or []
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", student_id)
    filename = f"{safe_id}_progress_report_{int(time.time())}.pdf"
    _generate_student_progress_report_pdf(profile, activities, filename)
    return FileResponse(filename, media_type="application/pdf", filename=f"{safe_id}_progress_report.pdf")

# ---------- API Endpoints ----------

@app.post("/evaluate")
async def evaluate_essay_endpoint(request: EvaluateRequest):
    """Evaluate an essay on specified parameters."""
    cache_key = _build_eval_cache_key(request)
    cached = _get_cached_evaluation(cache_key)
    if cached:
        cached_payload = dict(cached)
        cached_payload["candidate_name"] = request.candidate_name
        cached_payload["cached"] = True
        return JSONResponse(content=cached_payload)

    result = evaluate_essay(
        request.essay,
        request.parameters,
        request.candidate_name,
        request.student_class,
        request.curriculum,
        request.subject,
        request.language,
        request.teaching_mode,
        request.learner_context,
    )
    result["candidate_name"] = request.candidate_name
    _store_cached_evaluation(cache_key, result)
    return JSONResponse(content=result)

@app.post("/generate-question")
async def generate_question_endpoint(request: GenerateQuestionRequest):
    """Generate an essay question based on a basis."""
    try:
        question = generate_question(
            request.basis,
            request.student_class,
            request.curriculum,
            request.subject,
            request.language,
            request.teaching_mode,
            request.learner_context,
        )
        return {"question": question}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-sample-essay")
async def generate_sample_essay_endpoint(request: GenerateSampleEssayRequest):
    """Generate a high-scoring sample essay for the selected topic and parameters."""
    try:
        sample_essay = generate_sample_essay(
            request.essay_topic,
            request.parameters,
            request.student_class,
            request.curriculum,
            request.subject,
            request.language,
            request.teaching_mode,
            request.learner_context,
        )
        return {"sample_essay": sample_essay}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-certificate")
async def generate_certificate_endpoint(request: GenerateCertificateRequest):
    """Generate a certificate PDF."""
    try:
        cert_number = get_next_certificate_number()
        filename = f"{request.candidate_name.replace(' ', '_')}_certificate.pdf"
        generate_pass_certificate(
            request.candidate_name,
            request.scores_percent,
            request.essay_topic,
            cert_number,
            request.student_class,
            request.curriculum,
            filename
        )
        return FileResponse(filename, media_type='application/pdf', filename=filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-report")
async def generate_report_endpoint(request: GenerateReportRequest):
    """Generate a detailed report PDF."""
    try:
        filename = f"{request.candidate_name.replace(' ', '_')}_detailed_report.pdf"
        session_history = [item.dict() for item in request.session_history]
        if session_history:
            first = session_history[0]
            if not first.get("student_class") and request.student_class:
                for item in session_history:
                    item["student_class"] = request.student_class
            if not first.get("curriculum") and request.curriculum:
                for item in session_history:
                    item["curriculum"] = request.curriculum
        generate_detailed_report_pdf(session_history, request.candidate_name, filename)
        return FileResponse(filename, media_type='application/pdf', filename=filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/valid-parameters")
async def get_valid_parameters():
    """Get list of valid evaluation parameters."""
    return {"parameters": sorted(list(VALID_EVAL_PARAMS))}

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Essay Evaluation API", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)