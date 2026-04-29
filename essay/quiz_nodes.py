import time
import logging
import json
from typing import Dict, Any, List, Optional
from collections import Counter
from certificate import generate_pass_certificate
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import google.generativeai as genai


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY or GEMINI_API_KEY.strip() in {"your-gemini-api-key-here", "REPLACE_WITH_YOUR_ACTUAL_API_KEY"}:
    raise RuntimeError("GEMINI_API_KEY is not set or is placeholder. Set GEMINI_API_KEY in .env and restart.")

genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-2.5-flash"

logger = logging.getLogger("QuizPipeline")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _extract_json_array(text: str) -> str:
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Could not find a JSON array in model output.")
    return text[start: end + 1]


def _infer_category_from_question_text(question_text: str) -> str:
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


def _fix_category_distribution_general(quiz_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = len(quiz_data)
    if total <= 1:
        return quiz_data

    counts = Counter(q.get("category", "uncategorized") for q in quiz_data)
    main_category = max(counts, key=counts.get)
    single_categories = [cat for cat, c in counts.items() if c == 1]
    allow_single = (total % 2 == 1)

    if allow_single and single_categories:
        single_categories_to_fix = single_categories[1:]
    else:
        single_categories_to_fix = single_categories

    if not single_categories_to_fix:
        return quiz_data

    for q in quiz_data:
        cat = q.get("category", "uncategorized")
        if cat in single_categories_to_fix:
            q["category"] = main_category
            q_text = q.get("question", "")
            q["question"] = _replace_category_tag_in_text(q_text, main_category)

    return quiz_data


def _enforce_focus_category_rules(quiz_data: List[Dict[str, Any]], focus_categories: List[str]) -> List[Dict[str, Any]]:
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

    # Distribution attempt
    counts = Counter(q.get("category") for q in quiz_data)
    allow_single = (total % 2 == 1)
    max_categories_with_two = total // 2
    desired_min_per_cat = 2

    if len(allowed) <= max_categories_with_two:
        deficit = {}
        for cat in allowed:
            have = counts.get(cat, 0)
            if have < desired_min_per_cat:
                deficit[cat] = desired_min_per_cat - have

        surplus_indices = []
        for idx, q in enumerate(quiz_data):
            cat = q.get("category")
            if counts.get(cat, 0) > desired_min_per_cat:
                surplus_indices.append(idx)

        for target_cat, need in deficit.items():
            while need > 0 and surplus_indices:
                src_idx = surplus_indices.pop(0)
                src_q = quiz_data[src_idx]
                old_cat = src_q.get("category")
                counts[old_cat] -= 1
                src_q["category"] = target_cat
                src_q["question"] = _replace_category_tag_in_text(src_q.get("question", ""), target_cat)
                counts[target_cat] = counts.get(target_cat, 0) + 1
                need -= 1
    else:
        for idx, cat in enumerate(allowed):
            if idx >= total:
                break
            quiz_data[idx]["category"] = cat
            quiz_data[idx]["question"] = _replace_category_tag_in_text(quiz_data[idx].get("question", ""), cat)

    return quiz_data


# ---------------------------------------------------------
# Nodes
# ---------------------------------------------------------

def Quiz_node_start(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_start"
    start_time = time.perf_counter()
    print("\n=== Quiz Session Starting ===")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    result = {
        "flag": "success",
        "message": "Quiz pipeline started successfully.",
        "session_meta": {"session_started_at": time.time()},
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Quiz_node_start_result": result}


def Quiz_node_language_topic_subcategory(state: Dict[str, Any]) -> Dict[str, Any]:
    print("\n--- Node: Language / Topic / Subcategory ---")

    languages = {"1": "English", "2": "Kannada", "3": "Hindi"}
    for k, v in languages.items():
        print(f"{k}. {v}")

    while True:
        ch = input("Choose language (1/2/3): ").strip()
        if ch in languages:
            language = languages[ch]
            break
        print("Invalid choice")

    # Topic must be English
    while True:
        topic = input("Enter topic (English only): ").strip()
        if topic and any(c.isalpha() for c in topic):
            break
        print("Invalid topic")

    # Fetch subcategories
    print("\nFetching subcategories...")
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        prompt = f"""
        List at most 5 important subcategories for topic "{topic}".
        Return ONLY a JSON array of strings.
        """
        response = model.generate_content(prompt)
        subcats = json.loads(_extract_json_array(response.text))[:5]
    except Exception:
        subcats = []

    focus_categories = None  # default = system decides

    if subcats:
        print("\nAvailable subcategories:")
        for i, sc in enumerate(subcats, 1):
            print(f"{i}. {sc}")

        print(f"{len(subcats) + 1}. Enter custom subcategory")
        print(f"{len(subcats) + 2}. Any category (system chooses automatically)")

        print("\nYou can choose multiple subcategories.")
        print("Enter numbers separated by comma (example: 1,3)")
        print("Or choose single option number.")

        sel = input("Choose option(s): ").strip()

        if not sel:
            focus_categories = None

        else:
            parts = [s.strip() for s in sel.split(",")]
            selected_categories = []

            for p in parts:
                if p.isdigit():
                    idx = int(p)

                    # Selected from list
                    if 1 <= idx <= len(subcats):
                        selected_categories.append(subcats[idx - 1])

                    # Custom subcategory
                    elif idx == len(subcats) + 1:
                        custom = input("Enter custom subcategory: ").strip()
                        if custom:
                            selected_categories.append(custom)

                    # Any category
                    elif idx == len(subcats) + 2:
                        selected_categories = []
                        break

            if selected_categories:
                focus_categories = selected_categories
            else:
                focus_categories = None

    else:
        print("No subcategories found.")
        custom = input("Enter custom subcategory or press Enter for system choice: ").strip()
        focus_categories = [custom] if custom else None

    return {
        "Quiz_node_language_topic_subcategory_result": {
            "flag": "success",
            "config": {
                "language": language,
                "topic": topic,
                "focus_categories": focus_categories
            }
        }
    }




def Quiz_node_topic_difficulty_quizcount(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_topic_difficulty_quizcount"
    start_time = time.perf_counter()
    print("\n--- Node: Difficulty / Quiz Count ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    try:
        # 🔹 Get base config from Language/Topic/Subcategory node
        base_cfg = state.get(
            "Quiz_node_language_topic_subcategory_result", {}
        ).get("config", {})

        # Topic is already collected and enforced as English
        topic = base_cfg.get("topic")
        if not topic:
            raise ValueError("Topic not found from language node")

        # Difficulty
        difficulty = input("Enter difficulty (easy / medium / hard): ").strip().lower()
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"
        difficulty_level = difficulty

        # Quiz count
        while True:
            quiz_count_raw = input("Enter number of questions you want in the quiz: ").strip()
            if quiz_count_raw.isdigit():
                quiz_count = int(quiz_count_raw)
                if quiz_count > 0:
                    break
            print("Invalid number. Please enter a positive integer (e.g., 5, 10).")

        result = {
            "flag": "success",
            "message": "User configuration collected successfully.",
            "config": {
                **base_cfg,               # language, topic, focus_categories
                "difficulty": difficulty_level,
                "quiz_count": quiz_count,
            },
        }

    except Exception as e:
        error_msg = f"Failed to collect difficulty/quiz count: {e}"
        print(f"ERROR: {error_msg}")
        result = {"flag": "error", "message": error_msg, "config": {}}

    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Quiz_node_topic_difficulty_quizcount_result": result}


def Quiz_node_generate_quiz(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_generate_quiz"
    start_time = time.perf_counter()
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    prev_result = state.get("Quiz_node_topic_difficulty_quizcount_result", {})
    config = prev_result.get("config", {})
    prefill = state.get("prefill_config")

    if prefill:
        config = prefill

    if not config:
        error_msg = "No config available to generate quiz."
        print(f"ERROR: {error_msg}")
        result = {"flag": "error", "message": error_msg, "quiz": []}
        end_time = time.perf_counter()
        logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
        return {"Quiz_node_generate_quiz_result": result}

    topic = config.get("topic", "Unknown Topic")
    difficulty = config.get("difficulty", "medium")
    quiz_count = int(config.get("quiz_count", 5))
    focus_categories = config.get("focus_categories", None)
    language = config.get("language", "English")

    print(f"\n--- Node: Generating Quiz ({topic} | {difficulty} | {quiz_count} Qs | {language}) ---")

    focus_instruct = ""
    allowed_list_note = ""

    # ======================================================
    # MULTI-SUBCATEGORY EQUAL DISTRIBUTION LOGIC
    # ======================================================
    if focus_categories:
        allowed = [c.strip() for c in focus_categories if c.strip()]
        allowed_list_str = ", ".join(f'"{c}"' for c in allowed)

        equal_distribution_note = ""
        if len(allowed) > 0:
            per_cat = quiz_count // len(allowed)
            remainder = quiz_count % len(allowed)

            equal_distribution_note = f"""
EQUAL DISTRIBUTION RULE:
- There are {len(allowed)} selected subcategories.
- You MUST generate exactly {per_cat} questions per category.
- If total questions ({quiz_count}) is not perfectly divisible,
  distribute the remaining {remainder} question(s) by adding
  ONE extra question to the first {remainder} categories.
- Every selected category MUST appear at least once.
"""

        focus_instruct = f"""
IMPORTANT FOCUS RULE (STRICT):
- The ONLY allowed category names are: {allowed_list_str}.
- EVERY question MUST use exactly one of those names in BOTH places:
    1) Question text prefix: "[Category: <exact_name>]"
    2) JSON field "category"
- DO NOT rename, paraphrase, or substitute category names.

{equal_distribution_note}
"""

        allowed_list_note = f"\nAllowed categories (STRICT): {allowed_list_str}\n"

    # ======================================================
    # PROMPT
    # ======================================================
    prompt = f"""
You are a quiz generator.

Generate {quiz_count} multiple-choice questions in {language} language
on the topic: "{topic}".
Difficulty level: {difficulty}.

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
    "question": "[Category: lists] Which method is used to add an element to a Python list?",
    "options": ["append()", "add()", "insert()", "extend()"],
    "answer": "append()",
    "explanation": "append() adds one element at the end of the list",
    "category": "lists"
  }}
]

{allowed_list_note}
"""

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

        # Post-processing enforcement (your original logic untouched)
        if focus_categories:
            quiz_data = _enforce_focus_category_rules(quiz_data, focus_categories)
        else:
            quiz_data = _fix_category_distribution_general(quiz_data)

        result = {
            "flag": "success",
            "message": "Quiz generated successfully.",
            "quiz": quiz_data,
            "config": config,
        }

    except json.JSONDecodeError as e:
        error_msg = f"Failed to parse model output as JSON. Error: {e}"
        print(f"ERROR: {error_msg}")
        result = {"flag": "error", "message": error_msg, "quiz": [], "config": config}

    except Exception as e:
        error_msg = f"Unexpected error while generating quiz: {e}"
        print(f"ERROR: {error_msg}")
        result = {"flag": "error", "message": error_msg, "quiz": [], "config": config}

    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")

    return {"Quiz_node_generate_quiz_result": result}

def Quiz_node_user_answers_questions(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_user_answers_questions"
    start_time = time.perf_counter()
    print("\n--- Node: Present Quiz / User Answers Questions ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    prev_result = state.get("Quiz_node_generate_quiz_result", {})
    quiz: List[Dict[str, Any]] = prev_result.get("quiz", [])

    if prev_result.get("flag") != "success" or not quiz:
        error_msg = "No quiz available to answer."
        print(f"ERROR: {error_msg}")
        result = {"flag": "error", "message": error_msg, "user_answers": []}
        end_time = time.perf_counter()
        logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
        return {"Quiz_node_user_answers_questions_result": result}

    user_answers: List[str] = []

    for idx, q in enumerate(quiz, start=1):
        print(f"\nQ{idx}: {q.get('question')}")
        options = q.get("options", [])

        for i, opt in enumerate(options, start=1):
            print(f"  {i}. {opt}")

        while True:
            ans = input(f"Enter your answer for Q{idx} (1-{len(options)}): ").strip()
            if ans.isdigit():
                choice_idx = int(ans)
                if 1 <= choice_idx <= len(options):
                    user_answers.append(options[choice_idx - 1])
                    break
            print("Invalid choice, please try again.")

    result = {
        "flag": "success",
        "message": "User answers collected.",
        "user_answers": user_answers,
    }

    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")

    return {"Quiz_node_user_answers_questions_result": result}


def Quiz_node_record_answers_track_progress(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_record_answers_track_progress"
    start_time = time.perf_counter()
    print("\n--- Node: Record Answers / Track Progress ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    quiz_result = state.get("Quiz_node_generate_quiz_result", {})
    quiz: List[Dict[str, Any]] = quiz_result.get("quiz", [])
    ans_result = state.get("Quiz_node_user_answers_questions_result", {})
    user_answers: List[str] = ans_result.get("user_answers", [])

    if quiz_result.get("flag") != "success" or ans_result.get("flag") != "success":
        error_msg = "Quiz or answers not available for progress tracking."
        print(f"ERROR: {error_msg}")
        result = {"flag": "error", "message": error_msg}
        end_time = time.perf_counter()
        logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
        return {"Quiz_node_record_answers_track_progress_result": result}

    total = min(len(quiz), len(user_answers))
    correct_count = 0
    details: List[Dict[str, Any]] = []
    category_stats: Dict[str, Dict[str, Any]] = {}

    for i in range(total):
        q = quiz[i]
        correct = q.get("answer")
        user_ans = user_answers[i]
        is_correct = user_ans == correct
        if is_correct:
            correct_count += 1
        category = q.get("category", "uncategorized")
        if category not in category_stats:
            category_stats[category] = {"total": 0, "correct": 0}
        category_stats[category]["total"] += 1
        if is_correct:
            category_stats[category]["correct"] += 1

        details.append(
            {
                "question": q.get("question"),
                "correct_answer": correct,
                "user_answer": user_ans,
                "is_correct": is_correct,
                "explanation": q.get("explanation"),
                "category": category,
            }
        )

    score_pct = (correct_count / total * 100) if total > 0 else 0.0
    print(f"\nYou answered {correct_count}/{total} correctly. Score: {score_pct:.2f}%")

    for idx, d in enumerate(details, start=1):
        print(f"\nQ{idx}: {d['question']}")
        print(f"Category      : {d['category']}")
        print(f"Your answer   : {d['user_answer']}")
        print(f"Correct answer: {d['correct_answer']}")
        print(f"Result        : {'Correct ✅' if d['is_correct'] else 'Wrong ❌'}")
        if d["explanation"]:
            print(f"Explanation   : {d['explanation']}")

    print("\n=== Category-wise Performance ===")
    category_percentages: Dict[str, float] = {}
    for cat, stats in category_stats.items():
        total_c = stats["total"]
        correct_c = stats["correct"]
        pct_c = (correct_c / total_c * 100) if total_c > 0 else 0.0
        category_percentages[cat] = pct_c
        print(f"- {cat}: {correct_c}/{total_c} correct ({pct_c:.2f}%)")

    equal_performance = False
    equal_pct = 0.0
    weak_categories: List[str] = []

    if category_percentages:
        max_pct = max(category_percentages.values())
        min_pct = min(category_percentages.values())
        if abs(max_pct - min_pct) < 1e-9:
            equal_performance = True
            equal_pct = max_pct
            print("\nYou have equal performance across all categories.")
            print(f"You're proficient in these topics at about {equal_pct:.2f}%:")
            for cat in category_percentages.keys():
                print(f"  • {cat}")
        else:
            weak_categories = [cat for cat, p in category_percentages.items() if p == min_pct]
            best_categories = [cat for cat, p in category_percentages.items() if p == max_pct]
            print("\nBest category/categories:")
            for cat in best_categories:
                print(f"  • {cat} ({category_percentages[cat]:.2f}%)")
            print("\nWeakest category/categories:")
            for cat in weak_categories:
                print(f"  • {cat} ({category_percentages[cat]:.2f}%)")

    result = {
        "flag": "success",
        "message": "User progress recorded.",
        "total_questions": total,
        "correct_count": correct_count,
        "score_percent": score_pct,
        "details": details,
        "category_stats": category_stats,
        "category_percentages": category_percentages,
        "equal_performance": equal_performance,
        "equal_pct": equal_pct,
        "weak_categories": weak_categories,
    }

    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Quiz_node_record_answers_track_progress_result": result}


def Quiz_node_progress_check(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_progress_check"
    start_time = time.perf_counter()
    print("\n--- Node: Progress Check ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    result_node = state.get("Quiz_node_record_answers_track_progress_result", {})
    score = result_node.get("score_percent", 0.0)
    threshold = 60.0
    passed = score >= threshold
    if passed:
        print(f"Good job! Your score ({score:.2f}%) is above the threshold ({threshold}%).")
    else:
        print(f"Your score ({score:.2f}%) is below the threshold ({threshold}%). Keep practicing!")
    result = {
        "flag": "success",
        "message": "Progress checked.",
        "score_percent": score,
        "passed": passed,
        "threshold": threshold,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Quiz_node_progress_check_result": result}


def Quiz_node_quiz_completed(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_quiz_completed"
    start_time = time.perf_counter()
    print("\n--- Node: Quiz Completed ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    print("✅ Quiz completed.")
    result = {"flag": "success", "message": "Quiz completed."}
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Quiz_node_quiz_completed_result": result}


def Quiz_node_post_quiz_processing(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_post_quiz_processing"
    start_time = time.perf_counter()
    print("\n--- Node: Post Quiz Processing ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    progress = state.get("Quiz_node_progress_check_result", {})
    score = progress.get("score_percent", 0.0)
    print(f"Summary: Your overall quiz score was {score:.2f}%.")
    result = {
        "flag": "success",
        "message": "Post quiz processing done.",
        "score_percent": score,
    }
    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")
    return {"Quiz_node_post_quiz_processing_result": result}


def Quiz_node_redirect_quiz_generation(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "Quiz_node_redirect_quiz_generation"
    start_time = time.perf_counter()
    print("\n--- Node: Redirect Quiz Generation ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    rec = state.get("Quiz_node_record_answers_track_progress_result", {})
    equal_perf = rec.get("equal_performance", False)
    equal_pct = rec.get("equal_pct", 0.0)
    weak_cats = rec.get("weak_categories", [])
    prefill_config: Optional[Dict[str, Any]] = None
    redirect = False

    def ask_yes_no(prompt_text: str) -> bool:
        while True:
            ch = input(prompt_text + " (y/n): ").strip().lower()
            if ch in {"y", "yes"}:
                return True
            if ch in {"n", "no"}:
                return False
            print("Please enter 'y' or 'n'.")

    # ======================================================
    # CASE 1: Equal performance across categories
    # ======================================================
    if equal_perf:
        print(f"\nYour performance is equal across all categories (~{equal_pct:.2f}%).")
        print("Options:")
        print("  1) Continue with the SAME topic (generate another quiz on same topic)")
        print("  2) Move to the NEXT topic (new language/topic/sub-category flow)")
        print("  3) End session")

        while True:
            choice = input("Enter 1 / 2 / 3: ").strip()
            if choice in {"1", "2", "3"}:
                break
            print("Please enter 1, 2, or 3.")

        # -------- Option 1: Continue same topic --------
        if choice == "1":
            prev_cfg = state.get("Quiz_node_generate_quiz_result", {}).get("config")
            if prev_cfg:
                prefill_config = dict(prev_cfg)
            redirect = True
            print("Continuing with same topic.")

        # -------- Option 2: Start completely NEW topic --------
        elif choice == "2":
            print("\n⏭ Moving to NEXT TOPIC...")

            # Re-run language / topic / subcategory node
            lang_topic_state = Quiz_node_language_topic_subcategory({})
            cfg1 = lang_topic_state["Quiz_node_language_topic_subcategory_result"]["config"]

            # Re-run difficulty + quiz count
            diff_state = Quiz_node_topic_difficulty_quizcount(lang_topic_state)
            cfg2 = diff_state["Quiz_node_topic_difficulty_quizcount_result"]["config"]

            prefill_config = cfg2
            redirect = True
            print("New topic & category settings collected successfully.")

        else:
            redirect = False
            print("Ending the session.")
    

    # ======================================================
    # CASE 2: Weak category detected
    # ======================================================
    elif weak_cats:
        print("\nWeakest category/categories detected:")
        for cat in weak_cats:
            print(f"  - {cat}")

        focus = ask_yes_no("Do you want a quiz focused on the weak categories?")

        if focus:
            # Ask question count
            while True:
                quiz_count_raw = input("Enter number of questions for the focused quiz: ").strip()
                if quiz_count_raw.isdigit():
                    quiz_count = int(quiz_count_raw)
                    if quiz_count > 0:
                        break
                print("Invalid number.")

            difficulty = input("Enter difficulty (easy/medium/hard): ").strip().lower()
            if difficulty not in {"easy", "medium", "hard"}:
                difficulty = "medium"

            prev_cfg = state.get("Quiz_node_generate_quiz_result", {}).get("config", {})
            topic = prev_cfg.get("topic", "Unknown Topic")

            prefill_config = {
                "topic": topic,
                "difficulty": difficulty,
                "quiz_count": quiz_count,
                "focus_categories": weak_cats,
            }

            redirect = True
            print("Generating focused quiz on weak categories.")

        else:
            redirect = False
            print("Okay — ending session.")


    # ======================================================
    # CASE 3: No special condition — just ask to continue
    # ======================================================
    else:
        cont = ask_yes_no("Do you want another quiz on the same topic?")
        if cont:
            prev_cfg = state.get("Quiz_node_generate_quiz_result", {}).get("config")
            if prev_cfg:
                prefill_config = dict(prev_cfg)
            redirect = True
        else:
            redirect = False
            print("Session ended.")

    result = {
        "flag": "success",
        "message": "Redirect decision collected.",
        "redirect": redirect,
        "prefill_config": prefill_config,
    }

    end_time = time.perf_counter()
    logger.info(f"[COMPLETED] {NODE_NAME} in {end_time - start_time:.2f} seconds")

    return {"Quiz_node_redirect_quiz_generation_result": result}


# ---------------------------------------------------------
# Aggregation & final session report
# ---------------------------------------------------------

def print_session_aggregate_report(session_history: List[Dict[str, Any]]) -> None:
    """
    session_history: list of dicts, each with keys:
      - 'config' (topic/difficulty/quiz_count/focus)
      - 'category_stats' (dict mapping category -> {"total": int, "correct": int})
    """
    if not session_history:
        print("\nNo quizzes were taken this session.")
        return

    # Aggregate totals
    agg: Dict[str, Dict[str, int]] = {}
    for run in session_history:
        cfg = run.get("config", {})
        cat_stats = run.get("category_stats", {})
        for cat, stats in cat_stats.items():
            if cat not in agg:
                agg[cat] = {"total": 0, "correct": 0}
            agg[cat]["total"] += stats.get("total", 0)
            agg[cat]["correct"] += stats.get("correct", 0)

    print("\n=== SESSION AGGREGATED CATEGORY PERFORMANCE ===")
    overall_percentages: Dict[str, float] = {}
    for cat, stats in agg.items():
        total_c = stats["total"]
        correct_c = stats["correct"]
        pct = (correct_c / total_c * 100) if total_c > 0 else 0.0
        overall_percentages[cat] = pct
        print(f"- {cat}: {correct_c}/{total_c} correct ({pct:.2f}%)")

    # Determine best & worst across session
    if overall_percentages:
        max_pct = max(overall_percentages.values())
        min_pct = min(overall_percentages.values())
        best = [c for c, p in overall_percentages.items() if abs(p - max_pct) < 1e-9]
        worst = [c for c, p in overall_percentages.items() if abs(p - min_pct) < 1e-9]

        if abs(max_pct - min_pct) < 1e-9:
            print(f"\nAll categories have equal performance ~{max_pct:.2f}%. You're consistently at this level across topics.")
        else:
            print("\nSession best category/categories:")
            for c in best:
                print(f"  • {c} ({overall_percentages[c]:.2f}%)")
            print("\nSession weakest category/categories:")
            for c in worst:
                print(f"  • {c} ({overall_percentages[c]:.2f}%)")

    # Save a JSON summary file for session
    try:
        with open("session_history.json", "w", encoding="utf-8") as f:
            json.dump(session_history, f, indent=2)
        print("\nSaved session run-by-run history to session_history.json")
    except Exception as e:
        print(f"\n(Warning) Could not write session_history.json: {e}")
# pdf genration maduthe
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, Image, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
import matplotlib.pyplot as plt


def save_session_report_pdf(session_history, filename="session_report.pdf"):

    styles = getSampleStyleSheet()
    title = styles["Heading1"]
    h2 = styles["Heading2"]
    normal = styles["BodyText"]

    doc = SimpleDocTemplate(filename, pagesize=A4)
    story = []

    # =====================================================
    # HEADER
    # =====================================================
    story.append(Paragraph("<b>QUIZ PERFORMANCE REPORT</b>", title))
    story.append(Spacer(1, 0.2 * inch))

    candidate = session_history[0].get("candidate_name", "Unknown Candidate") if session_history else "Unknown Candidate"

    story.append(Paragraph(f"<b>Candidate Name:</b> {candidate}", normal))
    story.append(Spacer(1, 0.4 * inch))

    # =====================================================
    # QUIZ SESSION SUMMARY TABLE
    # =====================================================
    story.append(Paragraph("<b>Quiz Session Summary (All Runs)</b>", h2))
    story.append(Spacer(1, 0.25 * inch))

    table_data = [["Quiz No.", "Topic", "Difficulty", "Questions", "Categories Used", "Score %"]]

    for idx, run in enumerate(session_history, 1):
        cfg = run["config"]
        topic = cfg.get("topic", "-")
        difficulty = cfg.get("difficulty", "-")
        qcount = cfg.get("quiz_count", "-")

        categories = cfg.get("focus_categories", ["Auto-selected"])
        cat_text = "<br/>".join(categories) if isinstance(categories, list) else str(categories)

        wrapped_cat = Paragraph(cat_text, normal)

        score = run.get("score_percent", 0.0)

        table_data.append([
            str(idx),
            topic,
            difficulty,
            str(qcount),
            wrapped_cat,
            f"{score:.2f}%"
        ])

    summary_table = Table(table_data, colWidths=[50, 90, 70, 70, 200, 70])

    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))

    story.append(summary_table)
    story.append(Spacer(1, 0.5 * inch))

    # =====================================================
    # CATEGORY PERFORMANCE TABLE
    # =====================================================
    story.append(Paragraph("<b>Aggregated Category Performance (Topic-wise)</b>", h2))
    story.append(Spacer(1, 0.25 * inch))

    category_rows = []
    categories_list = []
    total_vals = []
    correct_vals = []
    accuracy_vals = []

    for run in session_history:
        topic = run["config"].get("topic", "Unknown")
        cat_stats = run["category_stats"]

        for cat, stats in cat_stats.items():
            total = stats["total"]
            correct = stats["correct"]
            pct = (correct / total * 100) if total else 0.0

            category_rows.append([topic, cat, f"{pct:.2f}%"])

            categories_list.append(cat)
            total_vals.append(total)
            correct_vals.append(correct)
            accuracy_vals.append(pct)

    cat_table = [["Topic", "Category", "Score (%)"]] + category_rows

    cat_summary_table = Table(cat_table, colWidths=[150, 150, 100])

    cat_summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#D4A017")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    story.append(cat_summary_table)
    story.append(Spacer(1, 0.5 * inch))

    # =====================================================
    # GRAPHS
    # =====================================================

    # -------- Accuracy Chart --------
    plt.figure(figsize=(6, 4))
    plt.bar(categories_list, accuracy_vals)
    plt.title("Category-wise Percentage (%)")
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig("accuracy_chart.png")
    plt.close()

    story.append(KeepTogether([
        Paragraph("<b>Category-wise Percentage Chart</b>", h2),
        Spacer(1, 0.2 * inch),
        Image("accuracy_chart.png", width=400, height=300)
    ]))

    story.append(Spacer(1, 0.5 * inch))

    # -------- Correct vs Total --------
    plt.figure(figsize=(6, 4))
    plt.bar(categories_list, total_vals, label="Total")
    plt.bar(categories_list, correct_vals, alpha=0.7)
    plt.legend()
    plt.title("Correct vs Total Questions")
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig("correct_vs_total.png")
    plt.close()

    story.append(KeepTogether([
        Paragraph("<b>Correct vs Total Questions</b>", h2),
        Spacer(1, 0.2 * inch),
        Image("correct_vs_total.png", width=400, height=300)
    ]))

    # =====================================================
    # BUILD PDF
    # =====================================================
    doc.build(story)

    print(f"\n PDF saved: {filename}")


# ---------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------

if __name__ == "__main__":
    candidate_name = input("\nEnter Candidate Name: ").strip() or "Unknown Candidate"

    saved_prefill: Optional[Dict[str, Any]] = None
    # session_history stores run-by-run info for final aggregation
    session_history: List[Dict[str, Any]] = []

    while True:
        state: Dict[str, Any] = {}

        state.update(Quiz_node_start(state))

        if saved_prefill:
            print("\nUsing prefill config from previous decision.")
            state["prefill_config"] = saved_prefill
            state["Quiz_node_topic_difficulty_quizcount_result"] = {"flag": "success", "config": saved_prefill}
            saved_prefill = None
        else:
            state.update(Quiz_node_language_topic_subcategory(state))
            state.update(Quiz_node_topic_difficulty_quizcount(state))


        state.update(Quiz_node_generate_quiz(state))
        state.update(Quiz_node_user_answers_questions(state))
        state.update(Quiz_node_record_answers_track_progress(state))

        # --- RECORD run-level category stats into session_history ---
        rec = state.get("Quiz_node_record_answers_track_progress_result", {})
        if rec.get("flag") == "success":
            run_entry = {
                "candidate_name": candidate_name,
                "config": state.get("Quiz_node_generate_quiz_result", {}).get("config", {}),
                "category_stats": rec.get("category_stats", {}),
                "score_percent": rec.get("score_percent", 0.0),
                "timestamp": time.time(),
            }
            session_history.append(run_entry)

        state.update(Quiz_node_progress_check(state))
        state.update(Quiz_node_quiz_completed(state))
        state.update(Quiz_node_post_quiz_processing(state))
        state.update(Quiz_node_redirect_quiz_generation(state))

        redirect_info = state.get("Quiz_node_redirect_quiz_generation_result", {})
        if not redirect_info.get("redirect", False):
            break

        saved_prefill = redirect_info.get("prefill_config")

    # ---- After user ends session: print aggregated report ----
    print("\nPipeline finished. Thanks for using the quiz system!")
    print_session_aggregate_report(session_history)

    # Automatically save PDF
    save_session_report_pdf(session_history, filename="session_report.pdf")

    # CERTIFICATE GENERATION
    final_agg = {}
    for run in session_history:
        for cat, stats in run["category_stats"].items():
            total = final_agg.get(cat, {}).get("total", 0)
            correct = final_agg.get(cat, {}).get("correct", 0)
            final_agg[cat] = {
                "total": total + stats.get("total", 0),
                "correct": correct + stats.get("correct", 0)
            }

    category_percentages = {
        cat: (v["correct"] / v["total"] * 100 if v["total"] else 0)
        for cat, v in final_agg.items()
    }

    all_above_50 = all(pct >= 50 for pct in category_percentages.values())
    grouped_stats = {cat: {cat: pct} for cat, pct in category_percentages.items()}

    if all_above_50:
        cert_file = generate_pass_certificate(candidate_name, grouped_stats)
        print(f"\n🎉 Certificate generated successfully: {cert_file}")
    else:
        print("\n⚠ Certificate NOT generated — some category scores are below 50%.")
        print("Category percentages:", category_percentages)




  


