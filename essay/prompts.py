from __future__ import annotations

from typing import List, Optional

PROMPT_VERSION = "v2.1.0"


def build_essay_topic_prompt(essay_excerpt: str) -> str:
    return f"""
Read the following essay and generate a very short title (maximum 5-7 words) that captures its main subject.
Return only the title, no extra text.

Essay:
{essay_excerpt}
"""


def build_quiz_generation_prompt(
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
    adaptive_notes: Optional[str] = None,
) -> str:
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

    focus_instruct = ""
    allowed_list_note = ""
    if focus_categories:
        allowed = [c.strip() for c in focus_categories if c and c.strip()]
        allowed_list_str = ", ".join(f'"{c}"' for c in allowed)
        per_cat = quiz_count // len(allowed)
        remainder = quiz_count % len(allowed)

        focus_instruct = f"""
IMPORTANT FOCUS RULE (STRICT):
- The ONLY allowed category names are: {allowed_list_str}.
- EVERY question MUST use exactly one of those names in BOTH places:
    1) Question text prefix: "[Category: <exact_name>]"
    2) JSON field "category"
- DO NOT rename, paraphrase, or substitute category names.

EQUAL DISTRIBUTION RULE:
- There are {len(allowed)} selected subcategories.
- You MUST generate exactly {per_cat} questions per category.
- If total questions ({quiz_count}) is not perfectly divisible,
  distribute the remaining {remainder} question(s) by adding
  ONE extra question to the first {remainder} categories.
- Every selected category MUST appear at least once.
"""
        allowed_list_note = f"\nAllowed categories (STRICT): {allowed_list_str}\n"

    adaptive_block = f"\nADAPTIVE INSTRUCTIONS:\n{adaptive_notes}\n" if adaptive_notes else ""

    return f"""
You are a quiz generator.
Prompt version: {PROMPT_VERSION}

Generate {quiz_count} multiple-choice questions in {normalized_language} language
on the topic: "{topic}".
Difficulty level: {difficulty}.
{profile_instruct}
{adaptive_block}
Language and style rules:
- Write the quiz in {normalized_language}.
- If {normalized_language} is Kannada and Kannada generation is unreliable, write in English instead.
- If teaching mode is Pictorial / visual, favor concrete examples and image-friendly wording.
- If teaching mode is Step-by-step, emphasize sequence and process.
- If teaching mode is Example-based, use examples in the question stem.
- If teaching mode is Story-based, frame the question with a short scenario.
- If teaching mode is Simple language, keep wording short and direct.
- If teaching mode is Activity-based, make the question action-oriented.
- If teaching mode is Practice-focused, emphasize repetition and application.
- If teaching mode is Bilingual hints, keep the main question in the chosen language and allow light English hints if needed.

{focus_instruct}

CATEGORY RULE (very important):
- Use as FEW distinct categories as possible.
- Each category should have AT LEAST 2 questions.
- EXCEPTION: If total questions is odd, then EXACTLY ONE category may have 1 question.

For EACH question:
1. Assign a conceptual category.
2. The category MUST be INCLUDED at the beginning of the question text in this exact format:
   "[Category: <category_name>] Question text here"

Return STRICTLY a JSON array with this structure, and no extra text:

[
  {{
    "question": "[Category: basics] What is a Python list?",
    "options": ["a collection of items", "a language feature", "a data structure", "all of the above"],
    "answer": "all of the above",
    "explanation": "Python lists are all of the above",
    "category": "basics"
  }}
]

{allowed_list_note}
"""


def build_essay_parameter_prompt(
    profile_note: str,
    parameter: str,
    language: str,
    teaching_mode: str,
    essay: str,
) -> str:
    return f"""
{profile_note}
Evaluate the following essay on the parameter "{parameter}". Provide a score from 1-10 (where 10 is excellent) and brief feedback (2-3 sentences).

Write the feedback in {language}.
If {language} is Kannada and Kannada generation is unreliable, respond in English instead.
If teaching mode is specified, shape the feedback to match it: {teaching_mode or 'general'}.

Return your response as a JSON object with keys "score" and "feedback".

Essay:
{essay}
"""


def build_essay_question_prompt(
    basis: str,
    profile_note: str,
    language: str,
) -> str:
    return f"""
Generate one thoughtful essay question based on the following basis: "{basis}".
The question should be suitable for a general essay.
{profile_note}
Language rule:
- Write the question in {language}.
- If {language} is Kannada and you cannot produce Kannada reliably, write the question in English instead.
Teaching mode guidance:
- Pictorial / visual: make the question concrete and image-friendly.
- Step-by-step: encourage sequential reasoning.
- Example-based: ask for examples.
- Story-based: frame the question inside a short scenario.
- Simple language: use short, clear words.
- Activity-based: make it action-oriented.
- Practice-focused: emphasize application and repetition.
- Bilingual hints: keep the main question in the selected language and allow a light English hint if needed.
Return only the question text, no extra commentary.
"""


def build_sample_essay_prompt(
    essay_topic: str,
    selected_parameters: str,
    profile_note: str,
    language: str,
    teaching_mode: str,
) -> str:
    return f"""
You are an expert essay writer. Write a high-quality sample essay on the following topic:
"{essay_topic}"

The sample should be written to demonstrate excellent performance in these evaluation categories:
{selected_parameters}

It should be polished, coherent, well-structured, and likely to score very highly across all selected categories.
Aim for about 300-500 words.

{profile_note}
Write the essay in {language}.
If {language} is Kannada and Kannada generation is unreliable, respond in English instead.
If teaching mode is specified, shape the style to match it: {teaching_mode or 'general'}.

Return only the essay text, no extra commentary.
"""
