from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import os
import time
import logging
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from dotenv import load_dotenv
import google.generativeai as genai
import para_read as prq

load_dotenv()

logger = logging.getLogger("PRQAPI")
logging.basicConfig(level=logging.INFO)

MODEL_NAME = getattr(prq, "MODEL_NAME", "gemini-2.5-flash")

app = FastAPI(
    title="PRQ API",
    description="Paragraph Reading Question generation and evaluation API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PRQGenerateRequest(BaseModel):
    candidate_name: Optional[str] = "Anonymous"
    passage: str
    difficulty: str = "medium"
    question_count: int = Field(default=5, ge=1, le=10)
    paragraph_owner: Optional[str] = None
    question_owner: Optional[str] = None
    answer_owner: Optional[str] = None
    force_new: bool = True
    student_class: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    language: str = "English"
    teaching_mode: Optional[str] = None
    learner_context: Optional[str] = None


class PRQGenerateTopicRequest(BaseModel):
    candidate_name: Optional[str] = "Anonymous"
    topic: str
    difficulty: str = "medium"
    question_count: int = Field(default=5, ge=1, le=10)
    paragraph_owner: Optional[str] = None
    question_owner: Optional[str] = None
    answer_owner: Optional[str] = None
    paragraph_min_words: int = Field(default=150, ge=150, le=600)
    paragraph_max_words: int = Field(default=220, ge=150, le=900)
    force_new: bool = True
    student_class: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    language: str = "English"
    teaching_mode: Optional[str] = None
    learner_context: Optional[str] = None


class PRQQuestion(BaseModel):
    question: str
    options: List[str]
    answer: str
    explanation: Optional[str] = ""
    type: Optional[str] = "literal"


class PRQEvaluateRequest(BaseModel):
    candidate_name: Optional[str] = "Anonymous"
    passage: Optional[str] = ""
    questions: List[PRQQuestion]
    user_answers: List[str]
    config: Optional[Dict[str, Any]] = None


class PRQReviewPasteRequest(BaseModel):
    candidate_name: Optional[str] = "Anonymous"
    passage: str
    questions_text: str
    answers_text: Optional[str] = None
    paragraph_owner: Optional[str] = None
    question_owner: Optional[str] = None
    answer_owner: Optional[str] = None
    student_class: Optional[str] = None
    curriculum: Optional[str] = None
    subject: Optional[str] = None
    language: str = "English"
    teaching_mode: Optional[str] = None
    learner_context: Optional[str] = None


class PRQSessionHistoryItem(BaseModel):
    config: Dict[str, Any]
    type_stats: Dict[str, Dict[str, int]]
    type_percentages: Dict[str, float]
    score_percent: float
    correct_count: int
    total_questions: int
    timestamp: float


class PRQGenerateSessionReportRequest(BaseModel):
    session_history: List[PRQSessionHistoryItem]
    candidate_name: str
    student_class: Optional[str] = None
    curriculum: Optional[str] = None


class PRQGenerateCertificateRequest(BaseModel):
    candidate_name: str
    scores_percent: Dict[str, float]
    threshold: float = 60.0
    student_class: Optional[str] = None
    curriculum: Optional[str] = None


def _build_config(
    passage: str,
    difficulty: str,
    question_count: int,
    paragraph_owner: Optional[str] = None,
    question_owner: Optional[str] = None,
    answer_owner: Optional[str] = None,
    student_class: Optional[str] = None,
    curriculum: Optional[str] = None,
    subject: Optional[str] = None,
    language: str = "English",
    teaching_mode: Optional[str] = None,
    learner_context: Optional[str] = None,
) -> Dict[str, Any]:
    difficulty = (difficulty or "medium").strip().lower()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"

    passage_type = prq._classify_passage_type_locally(passage)
    return {
        "difficulty": difficulty,
        "question_count": int(question_count),
        "language": (language or "English").strip() or "English",
        "passage_type": passage_type,
        "student_class": student_class,
        "curriculum": curriculum,
        "subject": subject,
        "paragraph_owner": (paragraph_owner or "").strip().lower() or None,
        "question_owner": (question_owner or "").strip().lower() or None,
        "answer_owner": (answer_owner or "").strip().lower() or None,
        "teaching_mode": teaching_mode,
        "learner_context": learner_context,
    }


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Could not find a JSON object in model output.")
    return json.loads(text[start:end + 1])


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _split_review_question_blocks(text: str) -> List[str]:
    lines = [line.strip() for line in re.split(r"[\r\n]+", text or "") if line.strip()]
    if not lines:
        return []

    blocks: List[List[str]] = []
    current: List[str] = []
    for line in lines:
        is_new_question = bool(re.match(r"^(?:\d+[\.)]|Q\d+[:\-]?|[-*•])\s+", line, re.IGNORECASE))
        if is_new_question and current:
            blocks.append(current)
            current = []
        cleaned = re.sub(r"^(?:\d+[\.)]|Q\d+[:\-]?|[-*•])\s+", "", line, flags=re.IGNORECASE)
        current.append(cleaned.strip())

    if current:
        blocks.append(current)

    merged = [" ".join(block).strip() for block in blocks if " ".join(block).strip()]
    return merged or lines


def _extract_review_answer_from_passage(passage: str, question: str) -> str:
    passage = str(passage or "").strip()
    question = str(question or "").strip()
    if not passage:
        return "Answer depends on the pasted paragraph."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", passage) if s.strip()]
    if not sentences:
        return passage[:160].strip()

    stop_words = {
        "what", "which", "when", "where", "who", "whom", "whose", "why", "how", "is", "are", "was",
        "were", "the", "a", "an", "of", "to", "in", "for", "and", "or", "does", "do", "did", "from",
        "this", "that", "these", "those", "with", "about", "into", "be", "by", "as", "at", "it",
    }
    question_words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", question) if w.lower() not in stop_words]

    best_sentence = sentences[0]
    best_score = -1
    for sentence in sentences:
        sentence_words = set(w.lower() for w in re.findall(r"[A-Za-z]{3,}", sentence))
        score = sum(1 for word in question_words if word in sentence_words)
        if score > best_score:
            best_score = score
            best_sentence = sentence

    best_sentence = best_sentence.strip()
    if len(best_sentence) > 220:
        best_sentence = best_sentence[:217].rstrip() + "..."
    return best_sentence


def _score_review_answer(expected_answer: str, student_answer: str) -> Optional[bool]:
    expected = _normalize_text(expected_answer)
    student = _normalize_text(student_answer)
    if not student:
        return None
    if expected == student:
        return True

    expected_words = {w for w in re.findall(r"[A-Za-z]{3,}", expected)}
    student_words = {w for w in re.findall(r"[A-Za-z]{3,}", student)}
    if not expected_words or not student_words:
        return None

    overlap = len(expected_words & student_words) / max(len(expected_words), 1)
    return overlap >= 0.45


def _enforce_word_range(text: str, min_words: int, max_words: int, topic: str) -> str:
    text = (text or "").strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        sentences = [f"This passage explains the topic of {topic} in a practical way."]

    def _word_count(sents: List[str]) -> int:
        return len([w for w in re.split(r"\s+", " ".join(sents).strip()) if w])

    extra_sentences = [
        f"In daily life, {topic} connects to choices people make at home, in school, and in the community.",
        f"When learners study {topic}, they can compare real examples and understand why the ideas matter.",
        f"Teachers can use {topic} to discuss causes, effects, and practical actions in familiar situations.",
        f"Looking at {topic} from different viewpoints helps readers build clearer thinking and better judgment.",
    ]

    i = 0
    while _word_count(sentences) < min_words:
        sentences.append(extra_sentences[i % len(extra_sentences)])
        i += 1
        if i > 12:
            break

    words = [w for w in re.split(r"\s+", " ".join(sentences).strip()) if w]
    if len(words) > max_words:
        words = words[:max_words]
        if words and words[-1][-1] not in ".!?":
            words[-1] = words[-1] + "."

    return " ".join(words)


def _guess_topic_kind(topic: str) -> str:
    t = (topic or "").strip().lower()
    if not t:
        return "general"
    place_hints = {
        "mysuru", "mysore", "bengaluru", "bangalore", "mumbai", "delhi", "chennai", "hyderabad", "kolkata"
    }
    if t in place_hints or any(k in t for k in ["city", "town", "village", "district", "state", "country", "river", "lake", "mountain", "place"]):
        return "place"
    if any(k in t for k in ["person", "leader", "scientist", "teacher", "poet", "author", "doctor"]):
        return "person"
    return "general"


def _build_local_topic_paragraph(topic: str, difficulty: str, config: Dict[str, Any]) -> str:
    topic_text = (topic or "Topic").strip()
    topic_name = topic_text[0].upper() + topic_text[1:] if topic_text else "Topic"
    subject = str(config.get("subject") or "").strip()
    teaching_mode = str(config.get("teaching_mode") or "").strip().lower()
    kind = _guess_topic_kind(topic_text)

    if kind == "place":
        sentences = [
            f"{topic_name} is a place with its own culture, people, and everyday rhythm.",
            f"In {topic_name}, students can observe how transport, schools, markets, and public spaces work together.",
            f"The local history and traditions of {topic_name} also shape how people celebrate, communicate, and solve problems.",
            f"Learning about {topic_name} helps readers connect geography, society, and civic life to real examples.",
        ]
    elif kind == "person":
        sentences = [
            f"{topic_name} is often discussed because of important actions, ideas, or contributions.",
            f"When readers study {topic_name}, they can identify the values, challenges, and decisions involved.",
            f"Examples connected to {topic_name} show how effort, choices, and responsibility affect outcomes.",
            f"This helps learners understand both facts and deeper lessons from real situations.",
        ]
    else:
        sentences = [
            f"{topic_name} is an important topic in daily life and learning.",
            f"People discuss {topic_name} at home, in classrooms, and in communities because it affects real decisions.",
            f"By studying {topic_name}, learners can connect ideas, causes, and effects in practical situations.",
            f"This makes the topic easier to understand and apply in meaningful ways.",
        ]

    if subject:
        sentences.append(f"In {subject}, {topic_name} can be explored through examples, comparison, and simple analysis.")

    if "visual" in teaching_mode or "pictorial" in teaching_mode:
        sentences.append(f"Readers can imagine scenes from {topic_name} and use clear mental pictures to remember key points.")
    elif "step" in teaching_mode:
        sentences.append(f"A step-by-step approach to {topic_name} helps learners move from basic facts to deeper understanding.")
    elif "story" in teaching_mode:
        sentences.append(f"A short story-like example can make {topic_name} easier to follow and discuss.")
    elif "example" in teaching_mode:
        sentences.append(f"Concrete examples related to {topic_name} make the ideas clearer and easier to apply.")

    if difficulty == "hard":
        sentences.append(f"Advanced learners may also examine trade-offs and long-term effects related to {topic_name}.")
    elif difficulty == "easy":
        sentences.append(f"For beginners, simple observations about {topic_name} are enough to build confidence.")

    return " ".join(sentences)


def _fetch_wikipedia_text(topic: str) -> Optional[Dict[str, str]]:
    topic = (topic or "").strip()
    if not topic:
        return None

    headers = {"User-Agent": "AgenixAI-PRQ/1.0"}

    def _get_json(url: str) -> Optional[Dict[str, Any]]:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            return json.loads(raw)
        except Exception:
            return None

    def _summary_for(title: str) -> Optional[Dict[str, str]]:
        t = urllib.parse.quote(title.replace(" ", "_"), safe="")
        summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}"
        payload = _get_json(summary_url)
        if not payload:
            return None
        extract = str(payload.get("extract") or "").strip()
        if not extract:
            return None
        page = str(payload.get("content_urls", {}).get("desktop", {}).get("page") or "").strip()
        return {"text": extract, "url": page or f"https://en.wikipedia.org/wiki/{t}"}

    # Try direct topic title first.
    direct = _summary_for(topic)
    if direct:
        return direct

    # Fallback: search nearest Wikipedia page title.
    search_q = urllib.parse.quote(topic, safe="")
    search_url = (
        "https://en.wikipedia.org/w/api.php?action=opensearch"
        f"&search={search_q}&limit=1&namespace=0&format=json"
    )
    search_payload = _get_json(search_url)
    if isinstance(search_payload, list) and len(search_payload) >= 2 and search_payload[1]:
        title = str(search_payload[1][0]).strip()
        if title:
            return _summary_for(title)
    return None


def _build_source_topic_paragraph(topic: str, source_text: str, difficulty: str, config: Dict[str, Any]) -> str:
    topic_name = (topic or "Topic").strip() or "Topic"
    teaching_mode = str(config.get("teaching_mode") or "").strip().lower()
    subject = str(config.get("subject") or "").strip()

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source_text or "") if s.strip()]
    if not sentences:
        return _build_local_topic_paragraph(topic_name, difficulty, config)

    # Keep factual, topic-linked lines from source text.
    core = sentences[:4]
    paragraph = " ".join(core)

    additions = []
    if subject:
        additions.append(f"In {subject}, this helps learners connect {topic_name} with practical classroom understanding.")
    if "visual" in teaching_mode or "pictorial" in teaching_mode:
        additions.append(f"Visualizing places, people, and events related to {topic_name} makes the ideas easier to remember.")
    elif "step" in teaching_mode:
        additions.append(f"A step-by-step approach to {topic_name} helps learners move from basic facts to deeper meaning.")
    elif "example" in teaching_mode:
        additions.append(f"Real examples linked to {topic_name} improve understanding and application.")
    elif "story" in teaching_mode:
        additions.append(f"A short story-style explanation can make {topic_name} more engaging for learners.")

    if difficulty == "hard":
        additions.append(f"Advanced learners can also compare viewpoints and evaluate long-term effects connected to {topic_name}.")
    elif difficulty == "easy":
        additions.append(f"For beginners, focusing on key facts about {topic_name} is enough to build confidence.")

    if additions:
        paragraph = paragraph + " " + " ".join(additions)
    return paragraph


def _build_topic_prq_prompt(
    topic: str,
    question_count: int,
    difficulty: str,
    paragraph_min_words: int,
    paragraph_max_words: int,
    config: Dict[str, Any],
) -> str:
    language = str(config.get("language") or "English").strip() or "English"
    teaching_mode = str(config.get("teaching_mode") or "").strip()
    learner_context = str(config.get("learner_context") or "").strip()
    subject = str(config.get("subject") or "").strip()
    student_class = str(config.get("student_class") or "").strip()
    curriculum = str(config.get("curriculum") or "").strip()

    context_note = learner_context or f"""
Learner profile:
- Preferred subject: {subject or 'Not specified'}
- Class/Grade: {student_class or 'Not specified'}
- Curriculum/Board: {curriculum or 'Not specified'}
- Language: {language}
- Teaching mode: {teaching_mode or 'Not specified'}
"""

    style_rules = f"""
Style rules:
- Write the passage and questions in {language}.
- If {language} is Kannada and Kannada generation is unreliable, write in English instead.
- If teaching mode is Pictorial / visual, keep the passage concrete and image-friendly.
- If teaching mode is Step-by-step, use sequential reasoning language.
- If teaching mode is Example-based, include examples in the passage.
- If teaching mode is Story-based, present the passage as a short scenario.
- If teaching mode is Simple language, use short sentences and simple words.
- If teaching mode is Activity-based, make the paragraph feel action-oriented.
- If teaching mode is Practice-focused, emphasize application and repetition.
- If teaching mode is Bilingual hints, keep the main content in the selected language and include a light English hint if needed.
"""

    return f"""
You are a paragraph reading generator.

Topic: {topic}
Question count: {question_count}
Difficulty: {difficulty}

{context_note}

Generate one short paragraph and {question_count} comprehension questions based on the topic.
The paragraph length must be between {paragraph_min_words} and {paragraph_max_words} words.
Questions should include literal, inferential, and vocabulary questions.
{style_rules}

Return ONLY valid JSON with this structure:
{{
  "passage": "...",
  "questions": [
    {{
      "question": "...",
      "options": ["A", "B", "C", "D"],
      "answer": "A",
      "explanation": "...",
      "type": "literal"
    }}
  ]
}}
"""


def _generate_topic_based_prq(topic: str, config: Dict[str, Any]) -> Dict[str, Any]:
    question_count = int(config.get("question_count", 5))
    difficulty = str(config.get("difficulty", "medium")).strip() or "medium"
    paragraph_min_words = int(config.get("paragraph_min_words", 150) or 150)
    paragraph_max_words = int(config.get("paragraph_max_words", 220) or 220)
    prompt = _build_topic_prq_prompt(topic, question_count, difficulty, paragraph_min_words, paragraph_max_words, config)

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        payload = _extract_json_object(response.text or "")
        passage = str(payload.get("passage") or "").strip()
        questions = payload.get("questions") or []
        if not passage or not isinstance(questions, list):
            raise ValueError("Invalid topic PRQ payload.")
        passage = _enforce_word_range(passage, paragraph_min_words, paragraph_max_words, topic)
        return {
            "passage": passage,
            "questions": questions[:question_count],
            "config": dict(config),
        }
    except Exception as e:
        logger.warning("Topic PRQ generation failed, using local fallback: %s", e)
        source_meta = _fetch_wikipedia_text(topic)
        if source_meta and source_meta.get("text"):
            fallback_passage = _build_source_topic_paragraph(topic, source_meta["text"], difficulty, config)
            config["source"] = {
                "provider": "wikipedia",
                "url": source_meta.get("url"),
            }
        else:
            fallback_passage = _build_local_topic_paragraph(topic, difficulty, config)
            config["source"] = {"provider": "local-fallback"}
        fallback_passage = _enforce_word_range(fallback_passage, paragraph_min_words, paragraph_max_words, topic)
        questions = prq._generate_local_questions(
            fallback_passage,
            question_count,
            difficulty=difficulty,
            seed=int(time.time() * 1_000_000),
        )
        return {
            "passage": fallback_passage,
            "questions": questions,
            "config": dict(config),
        }


def _review_pasted_prq(passage: str, questions_text: str, answers_text: Optional[str], config: Dict[str, Any]) -> Dict[str, Any]:
    language = str(config.get("language") or "English").strip() or "English"
    teaching_mode = str(config.get("teaching_mode") or "").strip()
    learner_context = str(config.get("learner_context") or "").strip()
    subject = str(config.get("subject") or "").strip()
    student_class = str(config.get("student_class") or "").strip()
    curriculum = str(config.get("curriculum") or "").strip()

    context_note = learner_context or f"""
Learner profile:
- Preferred subject: {subject or 'Not specified'}
- Class/Grade: {student_class or 'Not specified'}
- Curriculum/Board: {curriculum or 'Not specified'}
- Language: {language}
- Teaching mode: {teaching_mode or 'Not specified'}
"""

    prompt = (
        "You are reviewing a paragraph reading exercise.\n\n"
        f"{context_note}\n\n"
        f"Passage:\n\"\"\"\n{passage}\n\"\"\"\n\n"
        f"Questions pasted by the user:\n\"\"\"\n{questions_text}\n\"\"\"\n\n"
        f"Student answers pasted by the user, if any:\n\"\"\"\n{answers_text or ''}\n\"\"\"\n\n"
        "Tasks:\n"
        "1. Provide a correct-answer key for each question.\n"
        "2. If student answers are present, mark each item correct or incorrect and compute an overall score.\n"
        f"3. Write the response in {language}.\n\n"
        f"If {language} is Kannada and Kannada generation is unreliable, write in English instead.\n\n"
        "Return ONLY valid JSON with this structure:\n"
        "{\n"
        "  \"summary\": \"...\",\n"
        "  \"score_percent\": 75.0,\n"
        "  \"note\": \"...\",\n"
        "  \"items\": [\n"
        "    {\n"
        "      \"question\": \"...\",\n"
        "      \"correct_answer\": \"...\",\n"
        "      \"student_answer\": \"...\",\n"
        "      \"is_correct\": true,\n"
        "      \"explanation\": \"...\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        payload = _extract_json_object(response.text or "")
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise ValueError("Invalid review payload.")
        return {
            "summary": str(payload.get("summary") or "Review completed.").strip(),
            "score_percent": payload.get("score_percent"),
            "note": str(payload.get("note") or "").strip(),
            "items": items,
        }
    except Exception as e:
        logger.warning("Paste review failed, using fallback: %s", e)
        questions = _split_review_question_blocks(questions_text)
        answers = [line.strip() for line in re.split(r"[\n\r]+", answers_text or "") if line.strip()]
        items = []
        for idx, question in enumerate(questions, 1):
            correct_answer = _extract_review_answer_from_passage(passage, question)
            student_answer = answers[idx - 1] if idx - 1 < len(answers) else ""
            items.append({
                "question": question,
                "correct_answer": correct_answer,
                "student_answer": student_answer,
                "is_correct": _score_review_answer(correct_answer, student_answer),
                "explanation": "This answer was derived from the pasted paragraph in local review mode.",
            })
        return {
            "summary": "Answer key generated from the pasted paragraph and questions.",
            "score_percent": None,
            "note": "Fallback review mode was used because automated analysis was unavailable.",
            "items": items,
        }


def _generate_questions(passage: str, config: Dict[str, Any], force_new: bool = False) -> List[Dict[str, Any]]:
    question_count = int(config.get("question_count", 5))
    difficulty = config.get("difficulty", "medium")

    use_gemini = bool(
        prq.GEMINI_AVAILABLE
        and os.getenv("GEMINI_API_KEY")
        and not getattr(prq, "_GEMINI_FAILED", False)
    )
    generator = "gemini" if use_gemini else "local"

    cache_key = prq._question_cache_key(passage, config, generator)
    if not force_new and cache_key in prq.QUESTION_CACHE:
        return json.loads(json.dumps(prq.QUESTION_CACHE[cache_key]))

    run_config = dict(config)
    if force_new:
        # Include a nonce so repeated requests with same passage/config still vary.
        run_config["nonce"] = int(time.time() * 1_000_000)

    try:
        if use_gemini:
            questions = prq._generate_questions_with_gemini(passage, run_config)
        else:
            questions = prq._generate_local_questions(
                passage,
                question_count,
                difficulty=difficulty,
                seed=int(time.time() * 1_000_000),
            )
    except Exception as gemini_error:
        # Fallback to local generator if Gemini fails at runtime.
        setattr(prq, "_GEMINI_FAILED", True)
        logger.warning("Gemini generation failed, using local fallback: %s", gemini_error)
        questions = prq._generate_local_questions(
            passage,
            question_count,
            difficulty=difficulty,
            seed=int(time.time() * 1_000_000),
        )
        generator = "local"

    for q in questions:
        q["answer"] = prq._normalize_answer_letter(str(q.get("answer", "")), q.get("options", []))

    if not force_new:
        cache_key = prq._question_cache_key(passage, config, generator)
        prq.QUESTION_CACHE[cache_key] = json.loads(json.dumps(questions))
        prq._save_cache(prq.QUESTION_CACHE)
    return questions


def _evaluate_questions(questions: List[Dict[str, Any]], user_answers: List[str]) -> Dict[str, Any]:
    total = min(len(questions), len(user_answers))
    correct_count = 0
    details: List[Dict[str, Any]] = []
    type_stats: Dict[str, Dict[str, int]] = {}

    for i in range(total):
        q = questions[i]
        correct_answer = prq._normalize_answer_letter(
            str(q.get("answer", "A")), q.get("options", [])
        )
        user_answer = prq._normalize_answer_letter(
            str(user_answers[i] if i < len(user_answers) else ""), q.get("options", [])
        )
        is_correct = user_answer == correct_answer
        q_type = q.get("type", "literal")

        if is_correct:
            correct_count += 1

        if q_type not in type_stats:
            type_stats[q_type] = {"total": 0, "correct": 0}
        type_stats[q_type]["total"] += 1
        if is_correct:
            type_stats[q_type]["correct"] += 1

        details.append(
            {
                "question": q.get("question", ""),
                "correct_answer": correct_answer,
                "user_answer": user_answer,
                "is_correct": is_correct,
                "explanation": q.get("explanation", ""),
                "type": q_type,
            }
        )

    score_percent = (correct_count / total * 100) if total else 0.0
    type_percentages = {
        t: ((s["correct"] / s["total"] * 100) if s["total"] else 0.0)
        for t, s in type_stats.items()
    }

    return {
        "total_questions": total,
        "correct_count": correct_count,
        "score_percent": score_percent,
        "details": details,
        "type_stats": type_stats,
        "type_percentages": type_percentages,
    }


PRQ_CERT_COUNTER_FILE = "prq_certificate_counter.json"


def _safe_text(value: str, fallback: str = "Candidate") -> str:
    clean = (value or "").strip()
    return clean if clean else fallback


def _next_prq_certificate_number() -> str:
    try:
        with open(PRQ_CERT_COUNTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            last_number = int(data.get("last_number", 0))
    except Exception:
        last_number = 0

    next_number = last_number + 1
    cert_number = f"PRQ{next_number:03d}"

    with open(PRQ_CERT_COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_number": next_number}, f)

    return cert_number


def _build_prq_session_summary(session_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_questions = 0
    total_correct = 0
    merged_type_stats: Dict[str, Dict[str, int]] = {}

    for run in session_history:
        run_total = int(run.get("total_questions", 0) or 0)
        run_correct = int(run.get("correct_count", 0) or 0)
        total_questions += run_total
        total_correct += run_correct

        run_type_stats = run.get("type_stats", {}) or {}
        for q_type, stats in run_type_stats.items():
            bucket = merged_type_stats.setdefault(q_type, {"total": 0, "correct": 0})
            bucket["total"] += int(stats.get("total", 0) or 0)
            bucket["correct"] += int(stats.get("correct", 0) or 0)

    overall_percent = (total_correct / total_questions * 100) if total_questions else 0.0
    type_percentages = {
        q_type: ((vals["correct"] / vals["total"] * 100) if vals["total"] else 0.0)
        for q_type, vals in merged_type_stats.items()
    }

    return {
        "total_questions": total_questions,
        "total_correct": total_correct,
        "overall_percent": overall_percent,
        "type_stats": merged_type_stats,
        "type_percentages": type_percentages,
        "attempts": len(session_history),
    }


def _generate_prq_report_pdf(candidate_name: str, session_history: List[Dict[str, Any]], output_file: str, logo_path: str = "logo.png") -> None:
    summary = _build_prq_session_summary(session_history)

    doc = SimpleDocTemplate(output_file, pagesize=A4)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleStyle",
        parent=styles["Title"],
        fontSize=22,
        textColor=colors.HexColor("#0A4FA3"),
        alignment=1,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["BodyText"],
        fontSize=10,
        textColor=colors.HexColor("#2E3440"),
        leading=14,
    )

    def add_watermark(canvas_obj, doc_obj):
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.06)
            canvas_obj.drawImage(logo_path, x=170, y=250, width=250, height=250,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except Exception:
            pass

    story = [
        Paragraph("Paragraph Reading Session Report", title_style),
        Paragraph(f"Candidate: <b>{_safe_text(candidate_name)}</b>", body_style),
        Spacer(1, 8),
        Paragraph(
            f"Attempts: <b>{summary['attempts']}</b> | "
            f"Overall: <b>{summary['overall_percent']:.1f}%</b> | "
            f"Correct: <b>{summary['total_correct']}/{summary['total_questions']}</b>",
            body_style,
        ),
        Spacer(1, 12),
    ]

    profile_cfg = (session_history[0].get("config", {}) if session_history else {}) or {}
    profile_class = str(profile_cfg.get("student_class") or "Not specified")
    profile_curriculum = str(profile_cfg.get("curriculum") or "Not specified")
    story.extend([
        Paragraph(
            f"<b>Student Profile:</b> Class/Grade: {profile_class} | Curriculum/Board: {profile_curriculum}",
            body_style,
        ),
        Spacer(1, 10),
    ])

    type_table = [["Question Type", "Correct", "Total", "Percent"]]
    for q_type, stats in sorted(summary["type_stats"].items()):
        pct = summary["type_percentages"].get(q_type, 0.0)
        type_table.append([q_type.title(), str(stats["correct"]), str(stats["total"]), f"{pct:.1f}%"])

    if len(type_table) > 1:
        t = Table(type_table, colWidths=[170, 80, 80, 100])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F77D0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6DCE5")),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFF")]),
        ]))
        story.extend([Paragraph("Type-wise Performance", styles["Heading3"]), Spacer(1, 6), t, Spacer(1, 14)])

    run_table = [["Run #", "Difficulty", "Questions", "Correct", "Score"]]
    for idx, run in enumerate(session_history, start=1):
        cfg = run.get("config", {}) or {}
        run_table.append([
            str(idx),
            str(cfg.get("difficulty", "medium")).title(),
            str(run.get("total_questions", 0)),
            str(run.get("correct_count", 0)),
            f"{float(run.get('score_percent', 0.0)):.1f}%",
        ])

    t2 = Table(run_table, colWidths=[60, 110, 90, 90, 90])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0A4FA3")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6DCE5")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F7FAFF")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFF")]),
    ]))
    story.extend([Paragraph("Attempt Details", styles["Heading3"]), Spacer(1, 6), t2])

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)


def _generate_prq_certificate_pdf(
    candidate_name: str,
    scores_percent: Dict[str, float],
    output_file: str,
    threshold: float = 60.0,
    student_class: Optional[str] = None,
    curriculum: Optional[str] = None,
    logo_path: str = "logo.png",
) -> None:
    doc = SimpleDocTemplate(output_file, pagesize=A4)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "CertTitle",
        parent=styles["Title"],
        fontSize=24,
        alignment=1,
        textColor=colors.HexColor("#0A4FA3"),
        spaceAfter=8,
    )
    name_style = ParagraphStyle(
        "CertName",
        parent=styles["Heading2"],
        fontSize=20,
        alignment=1,
        textColor=colors.HexColor("#1F77D0"),
        spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "CertBody",
        parent=styles["BodyText"],
        fontSize=10.5,
        alignment=1,
        leading=14,
    )

    def add_watermark(canvas_obj, doc_obj):
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.06)
            canvas_obj.drawImage(logo_path, x=170, y=250, width=250, height=250,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except Exception:
            pass

    cert_number = _next_prq_certificate_number()
    avg_score = (sum(scores_percent.values()) / len(scores_percent)) if scores_percent else 0.0

    table_data = [["Category", "Score (%)"]]
    for key, value in scores_percent.items():
        table_data.append([str(key).title(), f"{float(value):.1f}%"])

    score_table = Table(table_data, colWidths=[220, 140])
    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F77D0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6DCE5")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFF")]),
    ]))

    story = [
        Spacer(1, 30),
        Paragraph("Certificate of Achievement", title_style),
        Paragraph("This certificate is awarded to", body_style),
        Paragraph(f"<b>{_safe_text(candidate_name)}</b>", name_style),
        Paragraph(
            f"Class/Grade: {student_class or 'Not specified'} | Curriculum/Board: {curriculum or 'Not specified'}",
            body_style,
        ),
        Paragraph(
            f"for achieving <b>{avg_score:.1f}%</b> in Paragraph Reading "
            f"(minimum required threshold: {threshold:.0f}%).",
            body_style,
        ),
        Spacer(1, 16),
        score_table,
        Spacer(1, 20),
        Paragraph(f"Certificate No: {cert_number}", body_style),
    ]

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)


@app.post("/prq/generate")
async def generate_prq(request: PRQGenerateRequest):
    passage = (request.passage or "").strip()
    if not passage:
        raise HTTPException(status_code=400, detail="Passage cannot be empty")

    config = _build_config(
        passage,
        request.difficulty,
        request.question_count,
        request.paragraph_owner,
        request.question_owner,
        request.answer_owner,
        request.student_class,
        request.curriculum,
        request.subject,
        request.language,
        request.teaching_mode,
        request.learner_context,
    )

    try:
        questions = _generate_questions(passage, config, force_new=request.force_new)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "candidate_name": request.candidate_name,
        "passage": passage,
        "config": config,
        "questions": questions,
    }


@app.post("/prq/evaluate")
async def evaluate_prq(request: PRQEvaluateRequest):
    questions = [q.dict() for q in request.questions]
    if not questions:
        raise HTTPException(status_code=400, detail="Questions are required")

    if len(request.user_answers) != len(questions):
        raise HTTPException(status_code=400, detail="Number of answers must match number of questions")

    try:
        result = _evaluate_questions(questions, request.user_answers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    result["candidate_name"] = request.candidate_name
    result["config"] = request.config or {}
    return result


@app.post("/prq/generate-session-report")
async def generate_prq_session_report(request: PRQGenerateSessionReportRequest):
    session_history = [item.dict() for item in request.session_history]
    if not session_history:
        raise HTTPException(status_code=400, detail="Session history cannot be empty")

    profile_cfg = (session_history[0].get("config", {}) if session_history else {}) or {}
    if request.student_class and not profile_cfg.get("student_class"):
        for item in session_history:
            cfg = item.setdefault("config", {})
            cfg["student_class"] = request.student_class
    if request.curriculum and not profile_cfg.get("curriculum"):
        for item in session_history:
            cfg = item.setdefault("config", {})
            cfg["curriculum"] = request.curriculum

    candidate_name = _safe_text(request.candidate_name)
    filename = f"prq_session_report_{int(time.time())}.pdf"
    file_path = str(Path(filename))

    try:
        _generate_prq_report_pdf(candidate_name, session_history, file_path)
    except Exception as e:
        logger.error("Failed to generate PRQ session report: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    return FileResponse(
        path=file_path,
        filename=f"{candidate_name.replace(' ', '_')}_prq_report.pdf",
        media_type="application/pdf",
    )


@app.post("/prq/generate-topic")
async def generate_prq_from_topic(request: PRQGenerateTopicRequest):
    topic = (request.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic cannot be empty")
    if request.paragraph_max_words < request.paragraph_min_words:
        raise HTTPException(status_code=400, detail="Maximum words must be greater than or equal to minimum words")

    config = _build_config(
        topic,
        request.difficulty,
        request.question_count,
        request.paragraph_owner,
        request.question_owner,
        request.answer_owner,
        request.student_class,
        request.curriculum,
        request.subject,
        request.language,
        request.teaching_mode,
        request.learner_context,
    )
    config["topic"] = topic
    config["passage_type"] = "topic-generated"
    config["paragraph_min_words"] = int(request.paragraph_min_words)
    config["paragraph_max_words"] = int(request.paragraph_max_words)

    try:
        result = _generate_topic_based_prq(topic, config)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/prq/review-paste")
async def review_prq_paste(request: PRQReviewPasteRequest):
    passage = (request.passage or "").strip()
    questions_text = (request.questions_text or "").strip()
    if not passage:
        raise HTTPException(status_code=400, detail="Passage cannot be empty")
    if not questions_text:
        raise HTTPException(status_code=400, detail="Questions text cannot be empty")

    config = _build_config(
        passage,
        "medium",
        5,
        request.paragraph_owner,
        request.question_owner,
        request.answer_owner,
        request.student_class,
        request.curriculum,
        request.subject,
        request.language,
        request.teaching_mode,
        request.learner_context,
    )

    try:
        result = _review_pasted_prq(passage, questions_text, request.answers_text, config)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/prq/generate-certificate")
async def generate_prq_certificate(request: PRQGenerateCertificateRequest):
    if not request.scores_percent:
        raise HTTPException(status_code=400, detail="scores_percent is required")

    average = sum(float(v) for v in request.scores_percent.values()) / len(request.scores_percent)
    if average < float(request.threshold):
        raise HTTPException(
            status_code=400,
            detail=f"Certificate requires at least {request.threshold:.0f}% overall score",
        )

    candidate_name = _safe_text(request.candidate_name)
    filename = f"prq_certificate_{int(time.time())}.pdf"
    file_path = str(Path(filename))

    try:
        _generate_prq_certificate_pdf(
            candidate_name,
            request.scores_percent,
            file_path,
            threshold=float(request.threshold),
            student_class=request.student_class,
            curriculum=request.curriculum,
        )
    except Exception as e:
        logger.error("Failed to generate PRQ certificate: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    return FileResponse(
        path=file_path,
        filename=f"{candidate_name.replace(' ', '_')}_prq_certificate.pdf",
        media_type="application/pdf",
    )


@app.get("/")
async def root():
    return {"message": "PRQ API", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
