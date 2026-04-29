import os
import time
import logging
import json
import difflib  # for fuzzy matching
from typing import Dict, Any, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import google.generativeai as genai

# ---------- ReportLab for Certificate ----------
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.linecharts import LineChart

# ---------- Gemini Configuration ----------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY or GEMINI_API_KEY.strip() in {"your-gemini-api-key-here", "REPLACE_WITH_YOUR_ACTUAL_API_KEY"}:
    raise RuntimeError("GEMINI_API_KEY is not set or is placeholder. Set GEMINI_API_KEY in .env and restart.")

genai.configure(api_key=GEMINI_API_KEY)

MODEL_NAME = "gemini-2.5-flash"

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EssayPipeline")

# ---------- Constants ----------
def _get_threshold() -> int:
    return 5

def _get_certificate_threshold() -> int:
    return 7

MAX_PARAMS = 5  # Maximum number of evaluation parameters

# ---------- Valid Essay Evaluation Parameters ----------
# Only these parameters are allowed. Custom terms not in this set are rejected.
VALID_EVAL_PARAMS = {
    "language", "analysis", "thought", "structure", "relevance",
    "creativity", "argumentation", "coherence", "grammar", "vocabulary",
    "depth", "originality", "clarity", "logic", "persuasiveness",
    "evidence", "examples", "organization", "flow", "transitions",
    "conclusion", "introduction", "thesis", "tone", "style",
    "mechanics", "syntax", "spelling", "punctuation", "accuracy",
    "insight", "critical thinking", "argument strength", "topic adherence"
}

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
    """Generate a very short title (max 5‑7 words) from the essay."""
    prompt = f"""
Read the following essay and generate a very short title (maximum 5-7 words) that captures its main subject.
Return only the title, no extra text.

Essay:
{essay_text[:2000]}
"""
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        topic = response.text.strip().strip('"').strip("'").strip()
        if len(topic) > 100:
            topic = topic[:100] + "..."
        return topic
    except Exception as e:
        logger.error(f"Failed to generate essay topic: {e}")
        # Fallback: first 5 words
        words = essay_text.split()[:5]
        return " ".join(words) + ("..." if len(essay_text.split()) > 5 else "")

# ---------- Certificate Number Management ----------
CERT_COUNTER_FILE = "certificate_counter.json"

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

# ---------- Certificate Generation ----------
def generate_pass_certificate(candidate_name: str,
                              scores_percent: Dict[str, float],
                              essay_topic: str | None,
                              certificate_number: str,
                              filename: str = "certificate.pdf",
                              logo_path: str = "logo.png"):
    """
    Generate a one‑page certificate with improved printing support.
    If essay_topic is None or multiple essays, use generic text.
    """
    # Use custom margins for better printing
    doc = SimpleDocTemplate(filename, pagesize=A4, 
                           topMargin=0.5*inch, bottomMargin=0.5*inch,
                           leftMargin=0.5*inch, rightMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'],
                                 fontSize=28, alignment=1, textColor=colors.HexColor("#0A4FA3"), 
                                 spaceAfter=12, spaceBefore=12)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Heading2'],
                                    alignment=1, textColor=colors.HexColor("#1F77D0"), 
                                    fontSize=16, spaceAfter=6)
    name_style = ParagraphStyle('NameStyle', alignment=1, fontSize=24,
                                textColor=colors.HexColor("#0A4FA3"), spaceAfter=16,
                                spaceBefore=10)
    body_style = ParagraphStyle('BodyStyle', parent=styles['BodyText'],
                                alignment=1, fontSize=11, textColor=colors.HexColor("#333333"), 
                                spaceAfter=8, leading=14)

    # Decorative top border using a thin table (print-friendly)
    border_top = Table([["" ]], colWidths=[7.5*inch], rowHeights=[2])
    border_top.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#1F77D0")),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(border_top)
    story.append(Spacer(1, 0.15*inch))

    # Logo
    try:
        logo = Image(logo_path, width=0.9*inch, height=0.9*inch)
        logo.hAlign = 'CENTER'
        story.append(logo)
    except Exception:
        story.append(Paragraph("<i>Logo</i>", body_style))
    
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("<b>AgenixAi</b>", subtitle_style))
    story.append(Spacer(1, 0.1*inch))

    story.append(Paragraph("Certificate of Achievement", title_style))
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("Awarded To", subtitle_style))
    story.append(Spacer(1, 0.08*inch))
    story.append(Paragraph(f"<b>{candidate_name}</b>", name_style))

    # Award text
    if essay_topic:
        award_text = (f"This certificate is proudly presented for achieving outstanding performance "
                      f"scoring <b>70% or above</b> in all evaluation categories for essay: <i>{essay_topic}</i>.")
    else:
        award_text = (f"This certificate is proudly presented for achieving outstanding performance "
                      f"scoring <b>70% or above</b> in all evaluation categories across the session.")

    story.append(Paragraph(award_text, body_style))
    story.append(Spacer(1, 0.15*inch))

    # Certificate number
    story.append(Paragraph(f"<b>Certificate No: {certificate_number}</b>", body_style))
    story.append(Spacer(1, 0.2*inch))

    # Table of scores with better formatting for printing
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
        ('GRID', (0,0), (-1,-1), 1, colors.grey),
        ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F5F5F5")]),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 0.25*inch))

    # Signature area
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("_" * 40, body_style))
    story.append(Paragraph("<b>AgenixAi Evaluation System</b>", body_style))
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph("<i>Congratulations on your exceptional performance!</i>", body_style))

    # Decorative bottom border
    story.append(Spacer(1, 0.2*inch))
    border_bottom = Table([["" ]], colWidths=[7.5*inch], rowHeights=[2])
    border_bottom.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#1F77D0")),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(border_bottom)

    # Build PDF with watermark
    def add_watermark(canvas_obj, doc_obj):
        """Add subtle watermark if logo exists"""
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.08)
            canvas_obj.drawImage(logo_path, x=200, y=350, width=250, height=250,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except:
            pass

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    return filename

# ---------- Detailed Session Report PDF ----------
def generate_detailed_report_pdf(session_history: List[Dict[str, Any]],
                                 candidate_name: str,
                                 filename: str = "detailed_report.pdf",
                                 logo_path: str = "logo.png"):
    """
    Create a multi‑page PDF with summary, trends, and detailed feedback.
    Optimized for printing with proper page breaks and formatting.
    """
    # Use print-friendly margins
    doc = SimpleDocTemplate(filename, pagesize=A4,
                           topMargin=0.5*inch, bottomMargin=0.5*inch,
                           leftMargin=0.5*inch, rightMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('ReportTitle', parent=styles['Title'],
                                  fontSize=22, alignment=1, textColor=colors.HexColor("#0A4FA3"), 
                                  spaceAfter=12, spaceBefore=0)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'],
                                   fontSize=14, textColor=colors.HexColor("#1F77D0"), 
                                   spaceAfter=10, spaceBefore=6)
    subheading_style = ParagraphStyle('SubHeading', parent=styles['Heading3'],
                                      fontSize=11, textColor=colors.HexColor("#0A4FA3"), 
                                      spaceAfter=8)
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'],
                                  fontSize=10, leading=13, textColor=colors.HexColor("#2E3440"))
    feedback_style = ParagraphStyle('Feedback', parent=styles['Normal'],
                                    fontSize=9, leading=11, leftIndent=0.2*inch, textColor=colors.HexColor("#52606D"))

    def add_watermark(canvas_obj, doc_obj):
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.06)
            canvas_obj.drawImage(logo_path, x=170, y=250, width=250, height=250,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except Exception:
            pass

    # Title page
    story.append(Paragraph(f"Detailed Essay Evaluation Report", title_style))
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(f"<b>Candidate:</b> {candidate_name}", normal_style))
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph(f"<b>Generated:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}", normal_style))
    story.append(Spacer(1, 0.3*inch))

    if not session_history:
        story.append(Paragraph("No essays were evaluated.", normal_style))
        doc.build(story)
        return filename

    all_params = set()
    for run in session_history:
        if isinstance(run.get('scores'), dict):
            all_params.update(run['scores'].keys())
    all_params = sorted(all_params)

    # 1. Summary table (adaptive column widths)
    story.append(Paragraph("Session Summary", heading_style))
    data = [["Essay #", "Date/Time", "Avg Score"] + [p.capitalize()[:8] for p in all_params]]
    
    for idx, run in enumerate(session_history, 1):
        timestamp = "N/A"
        if run.get('timestamp'):
            try:
                timestamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(run.get('timestamp', 0)))
            except:
                pass
        
        avg_score = run.get('avg_score', 'N/A')
        if isinstance(avg_score, (int, float)):
            avg_score = f"{avg_score:.1f}"
        
        row = [str(idx), timestamp, str(avg_score)]
        for p in all_params:
            sc = run.get('scores', {}).get(p)
            row.append(str(sc) if sc is not None else "N/A")
        data.append(row)

    # Calculate dynamic column widths (limit to avoid overflow)
    max_cols = len(all_params) + 3
    if max_cols > 10:
        # Reduce columns if too many
        col_widths = [0.6*inch, 1.0*inch, 0.7*inch] + [0.5*inch] * (max_cols - 3)
    else:
        col_widths = [0.6*inch, 1.0*inch, 0.7*inch] + [0.55*inch] * len(all_params)
    
    summary_table = Table(data, colWidths=col_widths, repeatRows=1)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFF")]),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.25*inch))

    # 2. Trends section (if more than one essay)
    if len(session_history) > 1:
        story.append(Paragraph("Performance Trends", heading_style))
        trend_data = [["Category"] + [f"Essay {i+1}" for i in range(len(session_history))]]
        
        for p in all_params:
            row = [p.capitalize()]
            for run in session_history:
                sc = run.get('scores', {}).get(p)
                row.append(f"{sc:.1f}" if sc is not None else "N/A")
            trend_data.append(row)
        
        trend_table = Table(trend_data, colWidths=[1.2*inch] + [0.8*inch]*len(session_history))
        trend_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1F77D0")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#D6DCE5")),
            ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(trend_table)
        story.append(Spacer(1, 0.25*inch))

    # 3. Detailed feedback per essay
    for idx, run in enumerate(session_history, 1):
        story.append(PageBreak())
        story.append(Paragraph(f"Essay #{idx} — Detailed Feedback", heading_style))
        story.append(Spacer(1, 0.1*inch))

        # Topic and question
        if run.get('essay_topic'):
            story.append(Paragraph(f"<b>Topic:</b> {run['essay_topic']}", subheading_style))
        if run.get('essay_question'):
            question_text = run['essay_question']
            if len(question_text) > 100:
                question_text = question_text[:100] + "..."
            story.append(Paragraph(f"<b>Question:</b> {question_text}", normal_style))
        
        story.append(Spacer(1, 0.1*inch))

        # Essay snippet
        essay_preview = run.get('essay', '')
        if len(essay_preview) > 400:
            essay_preview = essay_preview[:400] + "…"
        story.append(Paragraph(f"<b>Essay Preview:</b>", subheading_style))
        story.append(Paragraph(essay_preview, feedback_style))
        story.append(Spacer(1, 0.12*inch))

        # Scores and feedback per parameter
        story.append(Paragraph(f"<b>Score Breakdown:</b>", subheading_style))
        for p in all_params:
            score = run.get('scores', {}).get(p)
            feedback = run.get('feedbacks', {}).get(p, "No additional feedback.")
            if score is not None:
                story.append(Paragraph(f"<b>{p.capitalize()}:</b> {score}/10", normal_style))
                # Truncate long feedback
                if len(feedback) > 150:
                    feedback = feedback[:150] + "…"
                story.append(Paragraph(feedback, feedback_style))
                story.append(Spacer(1, 0.08*inch))

        # Overall feedback
        story.append(Spacer(1, 0.08*inch))
        overall_feedback = run.get('overall_feedback', 'No overall feedback provided.')
        if len(overall_feedback) > 300:
            overall_feedback = overall_feedback[:300] + "…"
        story.append(Paragraph(f"<b>Overall Assessment:</b>", subheading_style))
        story.append(Paragraph(overall_feedback, normal_style))

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
    return filename

# ---------- Node Functions ----------
def Essay_node_start(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_start"
    start_time = time.perf_counter()
    print("\n=== Essay Evaluation Session Starting ===")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    result = {
        "flag": "success",
        "message": "Session started.",
        "session_meta": {"session_started_at": time.time()},
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_start_result": result}

def Essay_node_choose_mode(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_choose_mode"
    start_time = time.perf_counter()
    print("\n--- Choose Mode ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    print("1. Evaluate an existing essay (just paste it)")
    print("2. Get a topic and generate a question, then write an essay")
    while True:
        choice = input("Enter 1 or 2: ").strip()
        if choice in {"1", "2"}:
            break
        print("Invalid choice. Please enter 1 or 2.")
    mode = "evaluate" if choice == "1" else "generate"
    result = {
        "flag": "success",
        "message": f"Mode selected: {mode}",
        "mode": mode,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_choose_mode_result": result}

def Essay_node_get_basis(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_get_basis"
    start_time = time.perf_counter()
    print("\n--- Essay Basis ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    basis = input("On what basis would you like to write an essay? (e.g., topic, theme, keyword): ").strip()
    if not basis:
        basis = "technology in education"
        print(f"Using default basis: {basis}")
    result = {
        "flag": "success",
        "message": "Basis collected.",
        "basis": basis,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_get_basis_result": result}

def Essay_node_generate_question(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_generate_question"
    start_time = time.perf_counter()
    print("\n--- Generating Essay Question ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    basis = state.get("Essay_node_get_basis_result", {}).get("basis", "technology")
    prompt = f"""
Generate one thoughtful essay question based on the following basis: "{basis}".
The question should be suitable for a general essay.
Return only the question text, no extra commentary.
"""
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        question = response.text.strip()
    except Exception as e:
        logger.error(f"Failed to generate question: {e}")
        question = f"Write an essay about {basis}."
    print(f"\nEssay Question:\n{question}\n")
    result = {
        "flag": "success",
        "message": "Question generated.",
        "essay_question": question,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_generate_question_result": result}

def Essay_node_collect_essay(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_collect_essay"
    start_time = time.perf_counter()
    print("\n--- Provide Your Essay ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    question = state.get("Essay_node_generate_question_result", {}).get("essay_question")
    if question:
        print(f"Question: {question}\n")
    else:
        print("Paste your essay below.\n")
    essay = input("Essay text:\n").strip()
    if not essay:
        print("Empty essay. Using default placeholder.")
        essay = "Technology is transforming education in India by improving accessibility, enabling digital learning platforms, and promoting personalized learning experiences."
    result = {
        "flag": "success",
        "message": "Essay collected.",
        "essay": essay,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_collect_essay_result": result}

def _evaluate_aspect(state: Dict[str, Any], aspect: str) -> Dict[str, Any]:
    NODE_NAME = f"Essay_node_evaluate_{_sanitize_param_name(aspect)}"
    start_time = time.perf_counter()
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}' for aspect: {aspect}")
    essay = state.get("essay", "")
    if not essay:
        essay = state.get("Essay_node_collect_essay_result", {}).get("essay", "")
    prompt = f"""
You are an expert essay evaluator.
Evaluate the **{aspect}** of the following essay.
Provide concise feedback and assign a score out of 10.

**IMPORTANT**: Your entire response must be a single JSON object with exactly two keys:
- "feedback": a string containing your feedback
- "score": an integer between 0 and 10

Do not include any other text, explanations, or markdown formatting.

Essay:
{essay}
"""
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        logger.debug(f"Raw model response for {aspect}: {raw_text}")
        data = _extract_json_object(raw_text)
        feedback = data.get("feedback", "No feedback provided.")
        score = data.get("score", 0)
        if not isinstance(score, int) or score < 0 or score > 10:
            score = 0
    except Exception as e:
        logger.error(f"LLM call or parsing failed for {aspect}: {e}")
        feedback = f"Error evaluating {aspect}."
        score = 0
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {
        f"{aspect}_feedback": feedback,
        f"{aspect}_score": score,
    }

def Essay_node_final_evaluation(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_final_evaluation"
    start_time = time.perf_counter()
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    params = state.get("evaluation_parameters", [])
    if not params:
        params = ["language", "analysis", "thought", "structure", "relevance"]
    scores = []
    feedback_parts = []
    for param in params:
        score = state.get(f"{param}_score")
        feedback = state.get(f"{param}_feedback", "No feedback.")
        if score is not None:
            scores.append(score)
            feedback_parts.append(f"{param.capitalize()}: {feedback}")
    if not scores:
        avg_score = 0.0
        overall_feedback = "No scores available."
    else:
        avg_score = sum(scores) / len(scores)
        overall_prompt = f"""
Based on the following individual feedbacks, write a concise overall feedback (2-3 sentences) for the essay.

{chr(10).join(feedback_parts)}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            overall_feedback = model.generate_content(overall_prompt).text.strip()
        except Exception as e:
            logger.error(f"Failed to generate overall feedback: {e}")
            overall_feedback = "Overall evaluation could not be generated."
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"overall_feedback": overall_feedback, "avg_score": avg_score}

def Essay_node_show_results(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_show_results"
    start_time = time.perf_counter()
    print("\n--- Evaluation Results ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    params = state.get("evaluation_parameters", [])
    if not params:
        params = ["language", "analysis", "thought", "structure", "relevance"]
    print("\nIndividual Scores (out of 10):")
    for param in params:
        score = state.get(f"{param}_score", "N/A")
        print(f"  {param.capitalize():12} : {score}")
    print(f"\nAverage Score: {state.get('avg_score', 0.0):.2f}/10")
    print("\nDetailed Feedback:")
    for param in params:
        fb = state.get(f"{param}_feedback", "No feedback.")
        print(f"\n--- {param.capitalize()} ---\n{fb}")
    print(f"\nOverall Feedback:\n{state.get('overall_feedback', 'N/A')}")
    result = {"flag": "success", "message": "Results displayed."}
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_show_results_result": result}

def Essay_node_progress_check(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_progress_check"
    start_time = time.perf_counter()
    print("\n--- Progress Check ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    params = state.get("evaluation_parameters", [])
    if not params:
        params = ["language", "analysis", "thought", "structure", "relevance"]
    scores = [state.get(f"{param}_score", 0) for param in params]
    threshold = _get_threshold()
    passed = all(s >= threshold for s in scores)
    if passed:
        print(f"✅ All scores are at least {threshold}/10. Great job!")
    else:
        low_cats = [param for param, s in zip(params, scores) if s < threshold]
        print(f"⚠️  Some scores are below {threshold}/10: {', '.join(low_cats)}. Keep practicing!")
    result = {
        "flag": "success",
        "message": "Progress checked.",
        "passed": passed,
        "threshold": threshold,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_progress_check_result": result}

def Essay_node_redirect(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_redirect"
    start_time = time.perf_counter()
    print("\n--- Another Essay? ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    while True:
        choice = input("Do you want to evaluate another essay? (y/n): ").strip().lower()
        if choice in {"y", "yes"}:
            redirect = True
            print("OK, let's do another one.\n")
            break
        elif choice in {"n", "no"}:
            redirect = False
            print("Ending session.\n")
            break
        else:
            print("Please enter 'y' or 'n'.")
    result = {
        "flag": "success",
        "message": "Redirect decision collected.",
        "redirect": redirect,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_redirect_result": result}

def Essay_node_offer_sample(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Essay_node_offer_sample"
    start_time = time.perf_counter()
    print("\n--- Sample Essay Offer ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    question = state.get("Essay_node_generate_question_result", {}).get("essay_question")
    if not question:
        basis = state.get("Essay_node_get_basis_result", {}).get("basis")
        if basis:
            question = f"Write an essay about {basis}."
        else:
            essay = state.get("essay", "")
            if essay:
                prompt = f"Based on the following essay, what is the main topic or question it addresses? Return only the topic in a sentence.\n\nEssay: {essay}"
                try:
                    model = genai.GenerativeModel(MODEL_NAME)
                    response = model.generate_content(prompt)
                    question = response.text.strip()
                except Exception as e:
                    logger.error(f"Topic extraction failed: {e}")
                    question = "the given topic"
            else:
                question = "the given topic"
    while True:
        choice = input("\nWould you like to see a sample essay that demonstrates high scores in all categories? (y/n): ").strip().lower()
        if choice in {'y', 'yes'}:
            generate_sample = True
            break
        elif choice in {'n', 'no'}:
            generate_sample = False
            break
        else:
            print("Please enter 'y' or 'n'.")
    sample_essay = None
    if generate_sample:
        print("\nGenerating sample essay...")
        prompt = f"""
You are an expert essay writer. Write a high-quality sample essay on the following topic:
"{question}"

The essay should demonstrate excellent:
- Language: fluent, precise, varied vocabulary, correct grammar.
- Analysis: deep insights, logical reasoning, supporting evidence.
- Thought: original ideas, critical thinking.
- Structure: clear introduction, body paragraphs with transitions, conclusion.
- Relevance: stays on topic, addresses the question fully.
- Creativity: engaging, unique perspective or examples.

Write the essay in a clear, well-organized manner. Aim for about 300-500 words.
Return only the essay text, no additional commentary.
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            sample_essay = response.text.strip()
            print("\n--- Sample Essay ---\n")
            print(sample_essay)
            print("\n--- End of Sample ---")
        except Exception as e:
            logger.error(f"Failed to generate sample essay: {e}")
            print("Sorry, could not generate sample essay at this time.")
    result = {
        "flag": "success",
        "message": "Sample essay offer processed.",
        "sample_generated": generate_sample,
        "sample_essay": sample_essay,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Essay_node_offer_sample_result": result}

def print_session_aggregate_report(session_history: List[Dict[str, Any]]) -> None:
    if not session_history:
        print("\nNo essays were evaluated this session.")
        return
    print("\n=== SESSION AGGREGATE REPORT ===")
    all_params = set()
    for run in session_history:
        all_params.update(run['scores'].keys())
    all_params = sorted(all_params)
    for idx, run in enumerate(session_history, 1):
        print(f"\nEssay {idx}:")
        if run.get('essay_topic'):
            print(f"  Topic: {run['essay_topic']}")
        if run.get('essay_question'):
            print(f"  Question: {run['essay_question']}")
        print(f"  Average Score: {run['avg_score']:.2f}/10")
        print("  Scores:")
        for param in all_params:
            sc = run['scores'].get(param)
            if sc is not None:
                print(f"    {param.capitalize()}: {sc}")
    cat_totals = {}
    cat_counts = {}
    for run in session_history:
        for param, sc in run['scores'].items():
            if sc is not None:
                cat_totals[param] = cat_totals.get(param, 0) + sc
                cat_counts[param] = cat_counts.get(param, 0) + 1
    if cat_totals:
        print("\nAverage per category across all essays:")
        for param in sorted(cat_totals.keys()):
            avg = cat_totals[param] / cat_counts[param]
            print(f"  {param.capitalize()}: {avg:.2f}/10")

# ---------- Parameter Selection with Strict Validation ----------
def select_evaluation_parameters():
    """Let user choose up to MAX_PARAMS evaluation parameters.
       Custom parameters are validated against VALID_EVAL_PARAMS.
       Invalid terms are rejected; only valid suggestions can be accepted."""
    # Predefined list shown to the user (subset of VALID_EVAL_PARAMS)
    predefined = [
        "language", "analysis", "thought", "structure", "relevance",
        "creativity", "argumentation", "coherence", "grammar", "vocabulary", "depth"
    ]
    selected = []
    max_params = MAX_PARAMS

    print("\n=== Evaluation Parameter Selection ===")
    print(f"You can select up to {max_params} parameters.")
    print("You may either:")
    print("  - Enter numbers (e.g., 1,3,5 or 2-4) to pick from the list below, or")
    print("  - Type a custom parameter name (must be a standard evaluation term).")
    print("After each addition, you will be asked if you want to add more.\n")

    while len(selected) < max_params:
        if selected:
            print(f"\nCurrently selected ({len(selected)}/{max_params}): {', '.join(s.capitalize() for s in selected)}")
        else:
            print("\nNo parameters selected yet.")

        print("\nSuggested parameters:")
        for i, p in enumerate(predefined, 1):
            print(f"  {i}. {p.capitalize()}")

        inp = input("\nEnter parameter(s): ").strip()
        if not inp:
            print("Input cannot be empty. Please try again.")
            continue

        # Split by commas (allow spaces)
        parts = [p.strip() for p in inp.split(',') if p.strip()]

        # --- Number‑based selection (from the predefined list) ---
        if all(p.isdigit() or '-' in p for p in parts):
            new_indices = set()
            valid_range = True
            for part in parts:
                if '-' in part:
                    try:
                        start, end = map(int, part.split('-'))
                        if start < 1 or end > len(predefined) or start > end:
                            print(f"Invalid range: {part}. Must be between 1 and {len(predefined)}.")
                            valid_range = False
                            break
                        new_indices.update(range(start, end + 1))
                    except ValueError:
                        print(f"Invalid range format: {part}. Use e.g., 2-5.")
                        valid_range = False
                        break
                else:
                    num = int(part)
                    if num < 1 or num > len(predefined):
                        print(f"Number {num} is out of range (1-{len(predefined)}).")
                        valid_range = False
                        break
                    new_indices.add(num)
            if not valid_range:
                continue

            new_params = [predefined[i-1] for i in sorted(new_indices)]
            actually_new = [p for p in new_params if p not in selected]
            if not actually_new:
                print("All selected parameters are already in your list. Nothing added.")
            else:
                selected.extend(actually_new)
                print(f"Added: {', '.join(p.capitalize() for p in actually_new)}")

        # --- Custom parameter entry (text) ---
        else:
            for custom in parts:
                if len(custom) > 50:
                    print(f"Parameter name '{custom}' is too long (max 50 characters). Skipping.")
                    continue

                # Check for duplicate
                if any(c.lower() == custom.lower() for c in selected):
                    print(f"'{custom.capitalize()}' is already selected. Skipping.")
                    continue

                # Validate against the global set (case‑insensitive)
                lower_custom = custom.lower()
                if lower_custom in VALID_EVAL_PARAMS:
                    selected.append(custom)
                    print(f"Added '{custom.capitalize()}' (valid).")
                else:
                    # Find close matches
                    matches = difflib.get_close_matches(lower_custom, VALID_EVAL_PARAMS, n=3, cutoff=0.6)
                    if matches:
                        print(f"'{custom.capitalize()}' is not a standard evaluation parameter.")
                        print("Did you mean one of these?")
                        for idx, m in enumerate(matches, 1):
                            print(f"  {idx}. {m.capitalize()}")
                        # User must choose a valid suggestion; cannot keep their own term.
                        while True:
                            choice = input("Enter the number of a suggestion to use, or 'n' to skip: ").strip()
                            if choice.isdigit() and 1 <= int(choice) <= len(matches):
                                chosen = matches[int(choice)-1]
                                if chosen not in selected:
                                    selected.append(chosen)
                                    print(f"Added '{chosen.capitalize()}'.")
                                else:
                                    print(f"'{chosen.capitalize()}' is already selected. Nothing added.")
                                break
                            elif choice.lower() in ('n', 'no'):
                                print(f"Skipped '{custom.capitalize()}'.")
                                break
                            else:
                                print("Please enter a number or 'n'.")
                    else:
                        # No close matches – reject outright
                        print(f"'{custom.capitalize()}' is not a recognized evaluation parameter and no close matches were found.")
                        print("It will not be added. Please choose from the suggested list or a valid term.")
                        # No addition

        # Check if we reached the maximum
        if len(selected) >= max_params:
            print(f"\nMaximum of {max_params} parameters reached.")
            break

        # Ask if user wants to add more parameters
        while True:
            more = input("\nAdd another parameter? (y/n): ").strip().lower()
            if more in ('y', 'yes'):
                break
            elif more in ('n', 'no'):
                return selected
            else:
                print("Please enter 'y' or 'n'.")

    return selected

# ---------- Main Pipeline ----------
if __name__ == "__main__":
    candidate_name = input("Enter Candidate Name: ").strip() or "Unknown Candidate"
    session_history = []

    state = {}
    state.update(Essay_node_start(state))

    # Parameter selection
    state["evaluation_parameters"] = select_evaluation_parameters()
    print(f"\nFinal selected parameters: {', '.join(p.capitalize() for p in state['evaluation_parameters'])}")

    while True:
        state.update(Essay_node_choose_mode(state))
        mode = state["Essay_node_choose_mode_result"]["mode"]

        if mode == "generate":
            state.update(Essay_node_get_basis(state))
            state.update(Essay_node_generate_question(state))

        state.update(Essay_node_collect_essay(state))
        essay = state["Essay_node_collect_essay_result"]["essay"]
        state["essay"] = essay

        for param in state["evaluation_parameters"]:
            result_dict = _evaluate_aspect(state, param)
            state.update(result_dict)

        state.update(Essay_node_final_evaluation(state))
        state.update(Essay_node_show_results(state))
        state.update(Essay_node_offer_sample(state))
        state.update(Essay_node_progress_check(state))

        scores_dict = {}
        feedbacks_dict = {}
        for param in state["evaluation_parameters"]:
            scores_dict[param] = state.get(f"{param}_score")
            feedbacks_dict[param] = state.get(f"{param}_feedback")

        # Generate short essay topic
        essay_topic = _get_essay_topic(essay)

        run_entry = {
            "candidate_name": candidate_name,
            "essay_question": state.get("Essay_node_generate_question_result", {}).get("essay_question", ""),
            "essay_topic": essay_topic,
            "essay": essay,
            "scores": scores_dict,
            "feedbacks": feedbacks_dict,
            "avg_score": state.get("avg_score", 0.0),
            "overall_feedback": state.get("overall_feedback", ""),
            "timestamp": time.time(),
        }
        session_history.append(run_entry)

        state.update(Essay_node_redirect(state))
        redirect_info = state.get("Essay_node_redirect_result", {})
        if not redirect_info.get("redirect", False):
            break

    # End of session: print aggregate report and generate PDFs
    print_session_aggregate_report(session_history)

    if session_history:
        detailed_filename = f"{candidate_name.replace(' ', '_')}_detailed_report.pdf"
        generate_detailed_report_pdf(session_history, candidate_name, detailed_filename)
        print(f"\n📄 Detailed report generated: {detailed_filename}")

    # Certificate generation – only if all categories average >= threshold
    cat_totals = {}
    cat_counts = {}
    for run in session_history:
        for param, sc in run['scores'].items():
            if sc is not None:
                cat_totals[param] = cat_totals.get(param, 0) + sc
                cat_counts[param] = cat_counts.get(param, 0) + 1

    if cat_totals:
        cat_averages = {param: cat_totals[param] / cat_counts[param] for param in cat_totals}
        cert_threshold = _get_certificate_threshold()
        all_passed = all(avg >= cert_threshold for avg in cat_averages.values())

        if all_passed:
            # Determine whether to show a specific essay topic or generic text
            if len(session_history) == 1:
                essay_topic = session_history[0]['essay_topic']
            else:
                essay_topic = None   # generic text

            cert_number = get_next_certificate_number()
            scores_percent = {p: avg*10 for p, avg in cat_averages.items()}
            pdf_file = generate_pass_certificate(
                candidate_name,
                scores_percent,
                essay_topic,
                cert_number,
                filename=f"{candidate_name.replace(' ', '_')}_certificate.pdf"
            )
            print(f"\n🎉 Certificate generated: {pdf_file}")
        else:
            print(f"\n  Certificate not generated – some categories below {cert_threshold}.")
            print("Category averages:", {k: f"{v:.2f}" for k, v in cat_averages.items()})
    else:
        print("\nNo scores recorded, certificate not generated.")