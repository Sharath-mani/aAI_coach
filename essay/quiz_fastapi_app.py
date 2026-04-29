from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import os
import time
import logging
import json
import re
import random
import urllib.parse
import urllib.request
from dotenv import load_dotenv
from adaptive_quiz import build_adaptive_quiz_prompt, derive_student_performance
from prompts import build_quiz_generation_prompt

# Load environment variables from .env file
load_dotenv()

import google.generativeai as genai
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak

# ---------- Gemini Configuration ----------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY or GEMINI_API_KEY.strip() in {"your-gemini-api-key-here", "REPLACE_WITH_YOUR_ACTUAL_API_KEY"}:
    raise RuntimeError("GEMINI_API_KEY is not set or is placeholder. Set GEMINI_API_KEY in .env and restart.")

genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-2.5-flash"
PROFILE_SERVICE_BASE = os.getenv("PROFILE_SERVICE_BASE", "http://localhost:8000")

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QuizAPI")

app = FastAPI(title="Quiz Generation API", description="API for generating quizzes using AI", version="1.0.0")

# ---------- CORS Configuration ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Pydantic Models ----------
class QuizGenerationRequest(BaseModel):
    student_id: Optional[str] = None
    topic: str
    language: str = "English"
    difficulty: str = "medium"
    quiz_count: int = 5
    time_limit_per_question_sec: int = 0
    focus_categories: Optional[List[str]] = None
    student_class: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    teaching_mode: Optional[str] = None
    learner_context: Optional[str] = None

class QuestionBase(BaseModel):
    question: str
    options: List[str]
    answer: str
    explanation: str
    category: str

class UserAnswerSubmission(BaseModel):
    quiz: List[QuestionBase]
    user_answers: List[Optional[str]]
    candidate_name: Optional[str] = "Anonymous"

class SessionHistoryItem(BaseModel):
    config: Dict[str, Any]
    category_stats: Dict[str, Dict[str, int]]
    score_percent: Optional[float] = None
    correct_count: Optional[int] = None
    total_questions: Optional[int] = None
    quiz: Optional[List[Dict[str, Any]]] = None
    details: Optional[List[Dict[str, Any]]] = None
    timestamp: float

class GenerateSessionReportRequest(BaseModel):
    session_history: List[SessionHistoryItem]
    candidate_name: str
    student_class: Optional[str] = None
    curriculum: Optional[str] = None

class GenerateQuizCertificateRequest(BaseModel):
    candidate_name: str
    scores_percent: Dict[str, float]
    topic: Optional[str] = None
    student_class: Optional[str] = None
    curriculum: Optional[str] = None


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

# ---------- Helper Functions ----------
def _extract_json_array(text: str) -> str:
    """Extract JSON array from model response."""
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Could not find a JSON array in model output.")
    return text[start: end + 1]

def _infer_category_from_question_text(question_text: str) -> str:
    """Infer category from question text with [Category: ...] marker."""
    marker = "[Category:"
    if marker in question_text:
        try:
            start_idx = question_text.index(marker) + len(marker)
            end_idx = question_text.index("]", start_idx)
            return question_text[start_idx:end_idx].strip()
        except Exception:
            return "uncategorized"
    return "uncategorized"

def _replace_category_tag_in_text(q_text: str, new_cat: str) -> str:
    """Replace category tag in question text."""
    marker = "[Category:"
    if marker in q_text:
        try:
            start_idx = q_text.index(marker) + len(marker)
            end_idx = q_text.index("]", start_idx)
            before = q_text[:start_idx]
            after = q_text[end_idx:]
            return f"{before} {new_cat}{after}"
        except Exception:
            return f"[Category: {new_cat}] {q_text}"
    else:
        return f"[Category: {new_cat}] {q_text}"

def _enforce_focus_category_rules(quiz_data: List[Dict[str, Any]], focus_categories: List[str]) -> List[Dict[str, Any]]:
    """Enforce focus category rules for category distribution."""
    if not focus_categories:
        return quiz_data

    allowed = [c.strip() for c in focus_categories if c.strip()]
    if not allowed:
        return quiz_data

    total = len(quiz_data)

    for q in quiz_data:
        if "category" not in q or not q["category"]:
            q["category"] = _infer_category_from_question_text(q.get("question", ""))

    # Force exact allowed categories
    for q in quiz_data:
        cat = q.get("category", "")
        if cat not in allowed:
            new_cat = allowed[0]
            q["category"] = new_cat
            q["question"] = _replace_category_tag_in_text(q.get("question", ""), new_cat)

    return quiz_data


def _generate_local_quiz(
    topic: str,
    language: str,
    difficulty: str,
    quiz_count: int,
    focus_categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    topic_text = (topic or "topic").strip() or "topic"
    categories = [c.strip() for c in (focus_categories or []) if str(c).strip()]
    if not categories:
        categories = ["core concepts", "facts", "applications"]

    templates = [
        "What is the best description of {topic}?",
        "Which statement about {topic} is most accurate?",
        "Which example best fits {topic}?",
        "What is a key idea related to {topic}?",
    ]

    quiz: List[Dict[str, Any]] = []
    for index in range(max(1, quiz_count)):
        category = categories[index % len(categories)]
        correct = f"Correct idea about {topic_text}"
        options = [
            correct,
            f"Unrelated detail about {topic_text}",
            f"Misleading statement about {topic_text}",
            f"General background on {topic_text}",
        ]
        random.shuffle(options)
        quiz.append({
            "question": f"[Category: {category}] {random.choice(templates).format(topic=topic_text)}",
            "options": options,
            "answer": ["A", "B", "C", "D"][options.index(correct)],
            "explanation": f"This option best matches the idea of {topic_text}.",
            "category": category,
        })

    return quiz


def _fetch_student_profile(student_id: Optional[str]) -> Optional[Dict[str, Any]]:
    normalized_student_id = (student_id or "").strip()
    if not normalized_student_id:
        return None

    encoded_id = urllib.parse.quote(normalized_student_id)
    url = f"{PROFILE_SERVICE_BASE.rstrip('/')}/student-profile/{encoded_id}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if payload.get("found") and isinstance(payload.get("profile"), dict):
                return payload["profile"]
    except Exception as exc:
        logger.warning("Unable to fetch student profile for adaptive quiz: %s", exc)
    return None

# ---------- Core Quiz Generation Function ----------
def generate_quiz(
    topic: str,
    language: str,
    difficulty: str,
    quiz_count: int,
    focus_categories: Optional[List[str]] = None,
    student_class: Optional[str] = None,
    curriculum: Optional[str] = None,
    subject: Optional[str] = None,
    teaching_mode: Optional[str] = None,
    learner_context: Optional[str] = None,
    time_limit_per_question_sec: int = 0,
    prompt_override: Optional[str] = None,
    effective_focus_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate a quiz using Gemini AI."""
    if not topic.strip():
        raise HTTPException(status_code=400, detail="Topic cannot be empty")

    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"

    if quiz_count <= 0:
        raise HTTPException(status_code=400, detail="Quiz count must be positive")

    normalized_language = (language or "English").strip() or "English"
    normalized_subject = (subject or "").strip()
    normalized_teaching_mode = (teaching_mode or "").strip()

    profile_instruct = learner_context or ""
    if not profile_instruct and (student_class or curriculum or normalized_subject or normalized_language or normalized_teaching_mode):
        profile_instruct = f"""
LEARNER PROFILE (STRICTLY APPLY):
- Preferred subject: {normalized_subject or 'Not specified'}
- Class/Grade: {student_class or 'Not specified'}
- Curriculum/Board: {curriculum or 'Not specified'}
- Language: {normalized_language}
- Teaching mode: {normalized_teaching_mode or 'Not specified'}
- Match cognitive level, terminology, examples, and question style to this learner profile.
"""

    time_limit_per_question_sec = max(0, int(time_limit_per_question_sec or 0))
    final_focus_categories = effective_focus_categories if effective_focus_categories is not None else focus_categories
    prompt = prompt_override or build_quiz_generation_prompt(
        topic=topic,
        language=normalized_language,
        difficulty=difficulty,
        quiz_count=quiz_count,
        focus_categories=final_focus_categories,
        student_class=student_class,
        curriculum=curriculum,
        subject=subject,
        teaching_mode=teaching_mode,
        learner_context=profile_instruct,
    )

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)

        raw_text = (response.text or "").strip()
        json_text = _extract_json_array(raw_text)
        quiz_data: List[Dict[str, Any]] = json.loads(json_text)

        if not isinstance(quiz_data, list):
            raise ValueError("Parsed quiz data is not a list.")

        # Ensure category field consistency
        for q in quiz_data:
            if "category" not in q or not q["category"]:
                q_text = q.get("question", "")
                q["category"] = _infer_category_from_question_text(q_text)

        # Post-processing enforcement
        if final_focus_categories:
            quiz_data = _enforce_focus_category_rules(quiz_data, final_focus_categories)

        return {
            "quiz": quiz_data,
            "config": {
                "topic": topic,
                "language": language,
                "difficulty": difficulty,
                "quiz_count": quiz_count,
                "focus_categories": final_focus_categories or [],
                "student_class": student_class,
                "curriculum": curriculum,
                "subject": subject,
                "teaching_mode": teaching_mode,
                "time_limit_per_question_sec": time_limit_per_question_sec,
            }
        }
    except Exception as e:
        logger.warning("Gemini quiz generation failed, falling back to local generator: %s", e)
        return {
            "quiz": _generate_local_quiz(topic, language, difficulty, quiz_count, final_focus_categories),
            "config": {
                "topic": topic,
                "language": language,
                "difficulty": difficulty,
                "quiz_count": quiz_count,
                "focus_categories": final_focus_categories or [],
                "student_class": student_class,
                "curriculum": curriculum,
                "subject": subject,
                "teaching_mode": teaching_mode,
                "time_limit_per_question_sec": time_limit_per_question_sec,
            }
        }

# ---------- Quiz Evaluation Function ----------
def evaluate_quiz_answers(quiz: List[Dict[str, Any]], user_answers: List[Optional[str]]) -> Dict[str, Any]:
    """Evaluate user answers and compute scores."""
    normalized_answers = list(user_answers or [])
    if len(normalized_answers) < len(quiz):
        normalized_answers.extend([None] * (len(quiz) - len(normalized_answers)))
    elif len(normalized_answers) > len(quiz):
        normalized_answers = normalized_answers[:len(quiz)]

    total = len(quiz)
    correct_count = 0
    details: List[Dict[str, Any]] = []
    category_stats: Dict[str, Dict[str, int]] = {}

    for i in range(total):
        q = quiz[i]
        # Support both raw dicts and pydantic QuestionBase models
        if hasattr(q, "dict") and callable(q.dict):
            q = q.dict()

        correct = q.get("answer")
        user_ans = normalized_answers[i]
        is_correct = user_ans == correct
        
        if is_correct:
            correct_count += 1

        category = q.get("category", "uncategorized")
        if category not in category_stats:
            category_stats[category] = {"total": 0, "correct": 0}
        category_stats[category]["total"] += 1
        if is_correct:
            category_stats[category]["correct"] += 1

        details.append({
            "question": q.get("question"),
            "correct_answer": correct,
            "user_answer": user_ans,
            "is_correct": is_correct,
            "explanation": q.get("explanation"),
            "category": category,
        })

    score_pct = (correct_count / total * 100) if total > 0 else 0.0

    # Calculate category percentages
    category_percentages: Dict[str, float] = {}
    for cat, stats in category_stats.items():
        total_c = stats["total"]
        correct_c = stats["correct"]
        pct_c = (correct_c / total_c * 100) if total_c > 0 else 0.0
        category_percentages[cat] = pct_c

    return {
        "total_questions": total,
        "correct_count": correct_count,
        "score_percent": score_pct,
        "details": details,
        "category_stats": category_stats,
        "category_percentages": category_percentages,
    }

# ---------- PDF Report Generation ----------
def generate_quiz_report_pdf(session_history: List[Dict[str, Any]], candidate_name: str, filename: str = "quiz_report.pdf", logo_path: str = "logo.png") -> str:
    """Generate a detailed PDF report for quiz session."""
    doc = SimpleDocTemplate(filename, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('ReportTitle', parent=styles['Title'],
                                  fontSize=22, alignment=1, textColor=colors.HexColor("#0A4FA3"), spaceAfter=12)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'],
                                   fontSize=14, textColor=colors.HexColor("#1F77D0"), spaceAfter=10)
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=10, leading=13, textColor=colors.HexColor("#2E3440"))

    def add_watermark(canvas_obj, doc_obj):
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.06)
            canvas_obj.drawImage(logo_path, x=170, y=250, width=250, height=250,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except Exception:
            pass

    story.append(Paragraph(f"Quiz Session Report for {candidate_name}", title_style))
    story.append(Spacer(1, 0.2*inch))

    profile_cfg = (session_history[0].get("config", {}) if session_history else {}) or {}
    profile_class = str(profile_cfg.get("student_class") or "Not specified")
    profile_curriculum = str(profile_cfg.get("curriculum") or "Not specified")
    story.append(Paragraph(
        f"<b>Student Profile:</b> Class/Grade: {profile_class} | Curriculum/Board: {profile_curriculum}",
        normal_style,
    ))
    story.append(Spacer(1, 0.12*inch))

    if len(session_history) > 1:
        first_score = session_history[0].get("score_percent", 0.0)
        last_score = session_history[-1].get("score_percent", 0.0)
        delta = last_score - first_score
        if delta > 0:
            trend_text = f"Great work! Your latest quiz score improved by {delta:.2f}% compared to the first quiz in this session."
        elif delta < 0:
            trend_text = f"Your latest quiz score is {abs(delta):.2f}% lower than the first quiz in this session. Keep practicing."
        else:
            trend_text = "Your latest quiz score is unchanged compared to the first quiz in this session."

        story.append(Paragraph("Session Improvement Summary", heading_style))
        story.append(Paragraph(trend_text, normal_style))
        story.append(Spacer(1, 0.2*inch))

    if not session_history:
        story.append(Paragraph("No quizzes were taken in this session.", normal_style))
        doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
        return filename

    # Aggregate stats across all quizzes
    agg: Dict[str, Dict[str, int]] = {}
    quiz_details = []
    
    for idx, run in enumerate(session_history, 1):
        cfg = run.get("config", {})
        cat_stats = run.get("category_stats", {})
        
        quiz_details.append({
            "number": idx,
            "topic": cfg.get("topic", "Unknown"),
            "difficulty": cfg.get("difficulty", "medium"),
            "language": cfg.get("language", "English"),
            "category_stats": cat_stats
        })
        
        for cat, stats in cat_stats.items():
            if cat not in agg:
                agg[cat] = {"total": 0, "correct": 0}
            agg[cat]["total"] += stats.get("total", 0)
            agg[cat]["correct"] += stats.get("correct", 0)

    # Session Summary
    story.append(Paragraph("Session Summary", heading_style))
    summary_data = [["Quiz #", "Topic", "Difficulty", "Language"]]
    for qd in quiz_details:
        summary_data.append([
            str(qd["number"]),
            qd["topic"][:20],
            qd["difficulty"],
            qd["language"]
        ])
    
    summary_table = Table(summary_data, colWidths=[0.7*inch, 2*inch, 1*inch, 1*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#D6DCE5")),
        ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFF")]),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.3*inch))

    # Detailed question review for each quiz run
    for idx, run in enumerate(session_history, 1):
        quiz_items = run.get("quiz") or []
        details = run.get("details") or []
        if quiz_items and details:
            story.append(PageBreak())
            story.append(Paragraph(f"Quiz {idx}: Detailed Question Review", heading_style))
            story.append(Spacer(1, 0.1*inch))
            for qidx, question in enumerate(quiz_items, 1):
                question_text = re.sub(r"\[Category:[^\]]+\]", "", question.get("question", "")).strip()
                user_answer = details[qidx-1].get("user_answer", "")
                correct_answer = details[qidx-1].get("correct_answer", "")
                explanation = details[qidx-1].get("explanation", "")
                is_correct = details[qidx-1].get("is_correct", False)
                category = question.get("category", "uncategorized")

                story.append(Paragraph(f"{qidx}. {question_text}", ParagraphStyle('Question', parent=styles['Normal'], fontSize=10.5, spaceAfter=4, leading=13, textColor=colors.HexColor("#1F2937"))))
                story.append(Paragraph(f"<b>Category:</b> {category}", normal_style))
                story.append(Paragraph(f"<b>Answer Submitted:</b> {user_answer}", normal_style))
                story.append(Paragraph(f"<b>Correct Answer:</b> {correct_answer}", normal_style))
                story.append(Paragraph(f"<b>Result:</b> {'Correct' if is_correct else 'Incorrect'}", normal_style))
                if explanation:
                    story.append(Paragraph(f"<b>Explanation:</b> {explanation}", ParagraphStyle('Explanation', parent=normal_style, fontSize=9, textColor=colors.HexColor("#52606D"))))
                story.append(Spacer(1, 0.15*inch))
            story.append(Spacer(1, 0.2*inch))

    # Aggregated Performance
    story.append(Paragraph("Aggregated Category Performance", heading_style))
    perf_data = [["Category", "Correct", "Total", "Percentage"]]
    for cat, stats in agg.items():
        total_c = stats["total"]
        correct_c = stats["correct"]
        pct = (correct_c / total_c * 100) if total_c > 0 else 0.0
        perf_data.append([cat, str(correct_c), str(total_c), f"{pct:.2f}%"])
    
    perf_table = Table(perf_data, colWidths=[2*inch, 1*inch, 1*inch, 1.2*inch])
    perf_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#D6DCE5")),
        ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFF")]),
    ]))
    story.append(perf_table)
    story.append(Spacer(1, 0.3*inch))

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    return filename

# ---------- Quiz Certificate Generation ----------
QUIZ_CERT_COUNTER_FILE = "quiz_certificate_counter.json"

def get_next_quiz_certificate_number() -> str:
    """Return the next quiz certificate number (e.g., QAIQ001, QAIQ002)."""
    try:
        with open(QUIZ_CERT_COUNTER_FILE, 'r') as f:
            data = json.load(f)
            last_num = data.get('last_number', 0)
    except (FileNotFoundError, json.JSONDecodeError):
        last_num = 0

    next_num = last_num + 1
    cert_number = f"QAIQ{next_num:03d}"

    with open(QUIZ_CERT_COUNTER_FILE, 'w') as f:
        json.dump({'last_number': next_num}, f)

    return cert_number

def generate_quiz_certificate(candidate_name: str,
                              scores_percent: Dict[str, float],
                              topic: Optional[str] = None,
                              student_class: Optional[str] = None,
                              curriculum: Optional[str] = None,
                              certificate_number: Optional[str] = None,
                              filename: str = "quiz_certificate.pdf",
                              logo_path: str = "logo.png") -> str:
    """
    Generate a quiz certificate PDF.
    """
    if not certificate_number:
        certificate_number = get_next_quiz_certificate_number()
        
    # Use print-friendly margins
    doc = SimpleDocTemplate(filename, pagesize=A4,
                           topMargin=0.5*inch, bottomMargin=0.5*inch,
                           leftMargin=0.5*inch, rightMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'],
                                 fontSize=26, alignment=1, textColor=colors.HexColor("#0A4FA3"),
                                 spaceAfter=10, spaceBefore=10)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Heading2'],
                                    alignment=1, textColor=colors.HexColor("#1F77D0"),
                                    fontSize=16, spaceAfter=6)
    name_style = ParagraphStyle('NameStyle', alignment=1, fontSize=24,
                                textColor=colors.HexColor("#0A4FA3"), spaceAfter=16,
                                spaceBefore=10)
    body_style = ParagraphStyle('BodyStyle', parent=styles['BodyText'],
                                alignment=1, fontSize=10.5, textColor=colors.HexColor("#2E3440"),
                                spaceAfter=7, leading=13)

    def add_watermark(canvas_obj, doc_obj):
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.06)
            canvas_obj.drawImage(logo_path, x=170, y=250, width=250, height=250,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except Exception:
            pass

    # Decorative top border
    border_top = Table([["" ]], colWidths=[7.5*inch], rowHeights=[2])
    border_top.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#1F77D0")),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(border_top)
    story.append(Spacer(1, 0.15*inch))

    # Logo/Icon
    try:
        logo = Image(logo_path, width=1.0*inch, height=1.0*inch)
        logo.hAlign = 'CENTER'
        story.append(logo)
    except Exception:
        story.append(Paragraph("<b>AgenixAi</b>", subtitle_style))
    story.append(Spacer(1, 0.08*inch))
    story.append(Paragraph("<b>AgenixAi</b>", subtitle_style))

    story.append(Paragraph("Quiz Certificate of Achievement", title_style))
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("Awarded To", subtitle_style))
    story.append(Spacer(1, 0.08*inch))
    story.append(Paragraph(f"<b>{candidate_name}</b>", name_style))
    story.append(Paragraph(
        f"Class/Grade: {student_class or 'Not specified'} | Curriculum/Board: {curriculum or 'Not specified'}",
        body_style,
    ))
    story.append(Spacer(1, 0.06*inch))

    # Award text
    if topic:
        award_text = (f"This certificate is proudly presented for successfully completing "
                      f"the quiz on <b>{topic}</b> with an overall score of <b>50% or above</b>.")
    else:
        award_text = (f"This certificate is proudly presented for successfully completing "
                      f"a quiz with an overall score of <b>50% or above</b>.")

    story.append(Paragraph(award_text, body_style))
    story.append(Spacer(1, 0.15*inch))

    # Certificate number
    story.append(Paragraph(f"<b>Certificate No: {certificate_number}</b>", body_style))
    story.append(Spacer(1, 0.2*inch))

    # Score breakdown table
    table_data = [["Category", "Score"]]
    for cat, score in scores_percent.items():
        table_data.append([cat.capitalize(), f"{score:.1f}%"])

    score_table = Table(table_data, colWidths=[4.0*inch, 1.2*inch])
    score_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 12),
        ('FONTSIZE', (0,1), (-1,-1), 11),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#D6DCE5")),
        ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFF")]),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 0.25*inch))

    # Signature area
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("_" * 40, body_style))
    story.append(Paragraph("<b>AgenixAi Quiz Evaluation System</b>", body_style))
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph("<i>Congratulations on your excellent performance!</i>", body_style))

    # Decorative bottom border
    story.append(Spacer(1, 0.2*inch))
    border_bottom = Table([["" ]], colWidths=[7.5*inch], rowHeights=[2])
    border_bottom.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#1F77D0")),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(border_bottom)

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    return filename

# ---------- API Endpoints ----------

@app.post("/generate-quiz")
async def generate_quiz_endpoint(request: QuizGenerationRequest):
    """Generate a quiz based on topic, language, difficulty, and count."""
    student_profile = _fetch_student_profile(request.student_id)
    adaptive_context = build_adaptive_quiz_prompt(
        request,
        derive_student_performance(student_profile),
    )

    result = generate_quiz(
        request.topic,
        request.language,
        adaptive_context["effective_difficulty"],
        request.quiz_count,
        request.focus_categories,
        request.student_class,
        request.curriculum,
        request.subject,
        request.teaching_mode,
        request.learner_context,
        request.time_limit_per_question_sec,
        prompt_override=adaptive_context["prompt"],
        effective_focus_categories=adaptive_context["effective_focus_categories"],
    )
    result["adaptive"] = {
        "effective_difficulty": adaptive_context["effective_difficulty"],
        "effective_focus_categories": adaptive_context["effective_focus_categories"],
        "last_score": adaptive_context["last_score"],
    }
    result["time_limit_per_question_sec"] = int(request.time_limit_per_question_sec or 0)
    return JSONResponse(content=result)

@app.post("/evaluate-answers")
async def evaluate_answers_endpoint(request: UserAnswerSubmission):
    """Evaluate user answers and compute scores."""
    try:
        quiz_as_dicts = [q.dict() if hasattr(q, 'dict') and callable(q.dict) else q for q in request.quiz]
        result = evaluate_quiz_answers(quiz_as_dicts, request.user_answers)
        result["candidate_name"] = request.candidate_name
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-session-report")
async def generate_session_report_endpoint(request: GenerateSessionReportRequest):
    """Generate a PDF report for the quiz session."""
    try:
        filename = f"{request.candidate_name.replace(' ', '_')}_quiz_report.pdf"
        session_history = [item.dict() for item in request.session_history]
        if session_history:
            profile_cfg = session_history[0].get("config", {}) or {}
            if request.student_class and not profile_cfg.get("student_class"):
                for item in session_history:
                    cfg = item.setdefault("config", {})
                    cfg["student_class"] = request.student_class
            if request.curriculum and not profile_cfg.get("curriculum"):
                for item in session_history:
                    cfg = item.setdefault("config", {})
                    cfg["curriculum"] = request.curriculum
        generate_quiz_report_pdf(session_history, request.candidate_name, filename)
        return FileResponse(filename, media_type='application/pdf', filename=filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-quiz-certificate")
async def generate_quiz_certificate_endpoint(request: GenerateQuizCertificateRequest):
    """Generate a quiz certificate PDF."""
    try:
        # Validate input
        if not request.candidate_name or not request.candidate_name.strip():
            raise HTTPException(status_code=400, detail="candidate_name is required")
        if not request.scores_percent:
            raise HTTPException(status_code=400, detail="scores_percent must be non-empty")

        # Get next certificate number
        certificate_number = get_next_quiz_certificate_number()
        
        # Generate certificate
        temp_filename = f"temp_quiz_cert_{certificate_number}.pdf"
        generate_quiz_certificate(
            candidate_name=request.candidate_name.strip(),
            scores_percent=request.scores_percent,
            topic=request.topic,
            student_class=request.student_class,
            curriculum=request.curriculum,
            certificate_number=certificate_number,
            filename=temp_filename
        )

        # Read and return PDF
        try:
            with open(temp_filename, 'rb') as f:
                pdf_data = f.read()

            # Clean up temporary file
            if os.path.exists(temp_filename):
                os.remove(temp_filename)

            filename = f"{request.candidate_name}_quiz_certificate.pdf"
            return Response(
                content=pdf_data,
                media_type='application/pdf',
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"'
                }
            )
        except Exception as e:
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            raise HTTPException(status_code=500, detail=str(e))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating quiz certificate: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Quiz Generation API", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "Quiz Generation API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)