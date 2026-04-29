import time
import logging
import json
import os
import hashlib
import re
import random
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv()

# Try to import Gemini
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("Warning: google-generativeai not installed. Falling back to local question generator.")

_CACHE_FILE = "prq_question_cache.json"

logger = logging.getLogger("PRQPipeline")
logger.setLevel(logging.INFO)

# Global flag to remember if Gemini failed during this session
_GEMINI_FAILED = False


def _load_cache() -> Dict[str, Any]:
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"(Warning) Could not persist question cache: {e}")


QUESTION_CACHE: Dict[str, List[Dict[str, Any]]] = _load_cache()
_OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"]


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


def _prompt(prompt_text: str, default: str = "") -> str:
    try:
        return input(prompt_text)
    except EOFError:
        print(f"\n[Auto Input] {prompt_text}{default}")
        return default


def _prompt_multiline(prompt_text: str, default: str = "") -> str:
    print(prompt_text)
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            if lines:
                break
            if default:
                print(f"[Auto Input] {default}")
            return default
        if not line.strip():
            break
        lines.append(line)
    if not lines and default:
        return default
    return "\n".join(lines).strip()


def _ask_yes_no(prompt_text: str) -> bool:
    while True:
        ch = _prompt(prompt_text + " (y/n): ", "n").strip().lower()
        if ch in {"y", "yes"}:
            return True
        if ch in {"n", "no"}:
            return False
        print("Please enter 'y' or 'n'.")


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _classify_passage_type_locally(passage: str) -> str:
    text = _normalize_text(passage)
    story_score = sum(k in text for k in [
        "once upon a time", "lived happily", "then", "suddenly",
        "tortoise", "rabbit", "he said", "she said", "they went", "the next day",
    ])
    article_score = sum(k in text for k in [
        "according to", "research", "study", "reported", "news",
        "official", "government", "data", "article",
    ])
    informational_score = sum(k in text for k in [
        "definition", "means", "explains", "information", "process",
        "important", "for example", "steps",
    ])
    if story_score >= article_score and story_score >= informational_score and story_score > 0:
        return "story"
    if article_score >= informational_score and article_score > 0:
        return "article"
    return "informational"


def _question_cache_key(passage: str, config: Dict[str, Any], generator: str) -> str:
    payload = {
        "passage": _normalize_text(passage),
        "difficulty": config.get("difficulty", "medium"),
        "question_count": int(config.get("question_count", 5)),
        "language": config.get("language", "English"),
        "passage_type": config.get("passage_type", "informational"),
        "generator": generator,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _normalize_answer_letter(answer: str, options: List[str]) -> str:
    answer_text = (answer or "").strip().upper()
    if answer_text in _OPTION_LETTERS[:len(options)]:
        return answer_text
    match = re.match(r'^([A-F])[\.\s\)]', answer_text)
    if match:
        return match.group(1)
    normalized_answer = _normalize_text(answer or "")
    for idx, option in enumerate(options):
        if idx >= len(_OPTION_LETTERS):
            break
        option_text = _normalize_text(option)
        if option_text and (option_text == normalized_answer or normalized_answer in option_text or option_text in normalized_answer):
            return _OPTION_LETTERS[idx]
    return "A"


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _shorten(text: str, max_len: int = 110) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rstrip() + "..."


def _extract_keywords(passage: str) -> List[str]:
    passage = str(passage or "")
    words = re.findall(r"[A-Za-z]{5,}", passage)
    stop_words = {
        "about", "there", "their", "which", "would", "could", "should", "these", "those",
        "because", "people", "through", "before", "after", "while", "where", "being", "other",
        "story", "article", "informational", "passage", "question", "answer", "explanation",
    }
    seen = set()
    keywords: List[str] = []
    for w in words:
        lw = w.lower()
        if lw not in stop_words and lw not in seen:
            seen.add(lw)
            keywords.append(lw)
    return keywords[:20]


def _choose_actor(label: str, default: str = "agent") -> str:
    default = (default or "agent").strip().lower()
    default_choice = "1" if default == "agent" else "2"
    while True:
        print(f"{label}: 1) Agent  2) Myself")
        ch = _prompt("Select 1 or 2 (or type agent/myself): ", default_choice).strip().lower()
        if ch in {"1", "agent", "a"}:
            return "agent"
        if ch in {"2", "myself", "me", "self"}:
            return "myself"
        print("Please enter 1 or 2, or type agent/myself.")


def _generate_passage_with_agent(topic: str, language: str = "English", min_words: int = 150, max_words: int = 220) -> str:
    topic = (topic or "General Reading").strip() or "General Reading"
    language = (language or "English").strip() or "English"
    min_words = max(80, int(min_words or 150))
    max_words = max(min_words + 20, int(max_words or 220))

    if GEMINI_AVAILABLE and os.getenv("GEMINI_API_KEY"):
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("models/gemini-2.5-flash")
            prompt = f"""
Write one reading comprehension paragraph in {language}.
Topic: {topic}
Length: between {min_words} and {max_words} words.
Requirements:
- Single coherent paragraph.
- Suitable for school comprehension practice.
- Include enough facts/details for MCQ questions.
- Return only the paragraph text.
"""
            response = model.generate_content(prompt)
            text = (response.text or "").strip()
            if text:
                return text
        except Exception:
            pass

    # Local fallback paragraph when Gemini is unavailable.
    return (
        f"{topic} is an important part of everyday learning because it helps students connect ideas to real life. "
        f"In many schools, teachers use activities, examples, and short discussions so learners can understand the topic step by step. "
        f"When students read carefully, they notice key details, causes, and results, which improves comprehension and confidence. "
        f"They also learn to compare facts, identify main ideas, and explain why events happen in a sequence. "
        f"Regular practice in reading and questioning builds stronger vocabulary and clearer thinking, and it encourages students "
        f"to use what they learn in class, at home, and in group work."
    )


def _answer_questions_with_agent(passage: str, questions: List[Dict[str, Any]], language: str = "English") -> List[str]:
    if not questions:
        return []

    if GEMINI_AVAILABLE and os.getenv("GEMINI_API_KEY"):
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("models/gemini-2.5-flash")
            payload = []
            for idx, q in enumerate(questions, start=1):
                payload.append({
                    "index": idx,
                    "question": q.get("question", ""),
                    "options": q.get("options", [])[:4],
                })
            prompt = f"""
Read the passage and answer all MCQs.
Language: {language}

Passage:
{passage}

Questions JSON:
{json.dumps(payload, ensure_ascii=True, indent=2)}

Return only a JSON array of answer letters like ["A","C","B"].
Use one letter per question, in order.
"""
            response = model.generate_content(prompt)
            raw = (response.text or "").strip()
            arr_str = _extract_json_array(raw)
            arr = json.loads(arr_str)
            if isinstance(arr, list):
                answers: List[str] = []
                for i, q in enumerate(questions):
                    candidate = str(arr[i] if i < len(arr) else "A").strip().upper()
                    normalized = _normalize_answer_letter(candidate, q.get("options", []))
                    answers.append(normalized)
                return answers
        except Exception:
            pass

    # Local fallback: choose option with best keyword overlap to passage/question.
    base_text = _normalize_text(passage)
    answers: List[str] = []
    for q in questions:
        q_text = _normalize_text(q.get("question", ""))
        options = q.get("options", [])[:4]
        best_idx = 0
        best_score = -1
        for idx, opt in enumerate(options):
            opt_text = _normalize_text(opt)
            score = 0
            for token in set(re.findall(r"[a-z]{3,}", opt_text)):
                if token in base_text:
                    score += 2
                if token in q_text:
                    score += 1
            if score > best_score:
                best_score = score
                best_idx = idx
        answers.append(_OPTION_LETTERS[best_idx] if best_idx < len(_OPTION_LETTERS) else "A")
    return answers


def _collect_questions_from_user(question_count: int) -> List[Dict[str, Any]]:
    questions: List[Dict[str, Any]] = []
    print(f"\nEnter {question_count} questions manually.")
    for idx in range(1, question_count + 1):
        print(f"\nManual Question {idx}/{question_count}")
        q_text = _prompt("Question text: ", f"Sample question {idx}?").strip() or f"Sample question {idx}?"
        options: List[str] = []
        for j, letter in enumerate(_OPTION_LETTERS[:4], start=1):
            opt = _prompt(f"Option {letter}: ", f"Option {j}").strip() or f"Option {j}"
            options.append(opt)
        ans = _normalize_answer_letter(_prompt("Correct answer letter (A-D): ", "A").strip().upper(), options)
        q_type = _prompt("Type (literal/inferential/vocabulary): ", "literal").strip().lower()
        if q_type not in {"literal", "inferential", "vocabulary"}:
            q_type = "literal"
        explanation = _prompt("Short explanation: ", "Based on the passage.").strip() or "Based on the passage."
        questions.append({
            "question": q_text,
            "options": options,
            "answer": ans,
            "explanation": explanation,
            "type": q_type,
        })
    return questions


# ---------------------------------------------------------
# IMPROVED LOCAL QUESTION GENERATOR (RANDOM & NON-REPETITIVE)
# ---------------------------------------------------------
def _generate_local_questions(passage: str, question_count: int, difficulty: str = "medium", seed: Optional[int] = None) -> List[Dict[str, Any]]:
    import secrets
    import random
    import re

    passage = str(passage or "")
    difficulty = str(difficulty or "medium").strip().lower()

    # Use a cryptographically random seed if none provided
    if seed is None:
        seed = secrets.randbits(64)
    random.seed(seed)
    
    # Define common stop words for filtering
    stop_words = {"the", "and", "for", "with", "from", "that", "this", "what", "when", "where", "why", "how", "are", "was", "were", "can", "will", "into", "about", "is", "by", "on", "or", "it", "to", "as", "be", "at", "in", "of", "about", "there", "their", "which", "would", "could", "should", "these", "those", "because", "people", "through", "before", "after", "while", "being", "other", "story", "article", "informational", "passage", "question", "answer", "explanation", "has", "have", "been", "very", "just", "some", "any", "all", "each", "every", "also", "such", "more", "most"}
    # Split into sentences and also into clauses (more fragments)
    sentences = _split_sentences(passage)
    if not sentences:
        sentences = ["The passage discusses an important idea."]

    # Create fragments by splitting at commas, semicolons, and conjunctions
    fragments = []
    for s in sentences:
        # Split on common conjunctions and punctuation
        parts = re.split(r'(?<=[,;])\s+|\s+(?:and|but|or|so)\s+', s)
        for part in parts:
            if not isinstance(part, str):
                continue
            part = part.strip()
            if part and len(part.split()) > 3:
                fragments.append(part)
    if not fragments:
        fragments = sentences[:]
    random.shuffle(fragments)

    # Extract keywords and also collect all nouns/verbs for vocabulary
    keywords = _extract_keywords(passage)
    if not keywords:
        keywords = ["context", "meaning", "idea", "lesson", "story"]
    random.shuffle(keywords)

    # Simple synonym map for common words (to rephrase sentences)
    synonym_map = {
        "tiger": ["big cat", "predator", "striped beast"],
        "wolf": ["canine", "predator", "wild dog"],
        "feared": ["was dreaded by", "intimidated", "caused terror in"],
        "ruled": ["governed", "dominated", "controlled"],
        "forest": ["woods", "jungle", "wilderness"],
        "clever": ["intelligent", "cunning", "smart"],
        "proudly": ["with arrogance", "haughtily", "boastfully"],
        "ashamed": ["embarrassed", "humiliated", "remorseful"],
        "borrowed": ["taken", "assumed", "appropriated"],
        "respect": ["admiration", "esteem", "regard"],
    }

    def _rephrase(sentence: str) -> str:
        """Replace some words with synonyms to create a new version."""
        words = sentence.split()
        new_words = []
        for w in words:
            w_lower = w.lower().strip('.,!?')
            if w_lower in synonym_map and random.random() < 0.4:
                replacement = random.choice(synonym_map[w_lower])
                # Preserve capitalization
                if w[0].isupper():
                    replacement = replacement.capitalize()
                new_words.append(replacement)
            else:
                new_words.append(w)
        return " ".join(new_words)

    # Distribute question types
    q_count = max(1, question_count)
    if difficulty == "easy":
        n_literal = max(2, q_count // 2)
        n_inferential = max(1, (q_count - n_literal) // 3)
        n_vocabulary = q_count - n_literal - n_inferential
    elif difficulty == "hard":
        n_literal = max(1, q_count // 4)
        n_inferential = max(2, q_count // 2)
        n_vocabulary = q_count - n_literal - n_inferential
    else:
        n_literal = max(1, q_count // 2)
        n_inferential = max(1, (q_count - n_literal) // 2)
        n_vocabulary = q_count - n_literal - n_inferential

    question_types = (["literal"] * n_literal) + (["inferential"] * n_inferential) + (["vocabulary"] * n_vocabulary)
    random.shuffle(question_types)

    # Question templates (more variety)
    literal_templates = [
        "Which statement is directly mentioned in the passage?",
        "According to the passage, which of these is true?",
        "What does the passage explicitly state?",
        "Identify the fact that appears verbatim in the text.",
        "Which detail can be found word‑for‑word in the passage?",
    ]
    inferential_templates = [
        "What can be inferred from the passage?",
        "Which conclusion is best supported by the text?",
        "The author implies that",
        "Based on the passage, which of the following is most likely true?",
        "What idea is indirectly conveyed?",
    ]
    vocabulary_templates = [
        "In the passage, what is the closest meaning of '{word}'?",
        "The word '{word}' most nearly means",
        "As used in the passage, '{word}' refers to",
        "Which of the following best defines '{word}' as used in the text?",
    ]

    # Helper: generate wrong literal options by mutating sentences
    def _literal_distractors(correct_sentence: str, count: int = 3) -> List[str]:
        candidates = []
        # Use other sentences
        for s in sentences:
            if s != correct_sentence and len(s.split()) > 3:
                candidates.append(_shorten(s, 100))
        # Also use mutated versions of the correct sentence
        for _ in range(2):
            mutated = _rephrase(correct_sentence)
            # Flip meaning with "not"
            if random.random() < 0.5:
                words = mutated.split()
                if len(words) > 4:
                    words.insert(random.randint(2, len(words)-2), "not")
                    mutated = " ".join(words)
            candidates.append(_shorten(mutated, 100))
        # Add generic false statements
        generic = [
            "This detail is not mentioned in the passage.",
            "The passage says the opposite of this.",
            "This statement is contradicted by the text.",
            "The author never makes this claim.",
        ]
        candidates.extend(generic)
        random.shuffle(candidates)
        return candidates[:count]

    # Helper: generate wrong inferences that are plausible but unsupported
    def _inferential_distractors(correct_inference: str, base_sentence: str, count: int = 3) -> List[str]:
        base_phrase = _focus_phrase(base_sentence)
        generic = [
            f"The passage suggests that {base_phrase} has no importance.",
            f"The passage proves the exact opposite about {base_phrase}.",
            f"The text gives complete evidence that {base_phrase} is unrelated to the main topic.",
            "The passage provides no information at all to support any conclusion.",
        ]
        # Avoid returning a distractor that duplicates the correct idea.
        candidates = [d for d in generic if d.strip().lower() != correct_inference.strip().lower()]
        random.shuffle(candidates)
        return candidates[:count]

    def _build_options(correct: str, distractors: List[str]) -> Dict[str, Any]:
        options = distractors[:3]
        correct_slot = random.randint(0, 3)
        options.insert(correct_slot, correct)
        return {
            "options": options,
            "answer": _OPTION_LETTERS[correct_slot],
        }

    def _pick_unique(items: List[str], used: set) -> str:
        available = [item for item in items if item not in used]
        if not available:
            used.clear()
            available = items[:]
        choice = random.choice(available)
        used.add(choice)
        return choice

    def _focus_phrase(text: str, max_words: int = 4) -> str:
        """Extract a short, readable key phrase from a sentence."""
        text = text.strip()
        if not text:
            return "the passage"

        # Prefer the first clause for cleaner, less noisy phrases.
        first_clause = re.split(r"[,;:]", text, maxsplit=1)[0].strip()
        raw_words = re.findall(r"[A-Za-z][A-Za-z'-]*", first_clause or text)
        if not raw_words:
            return "the passage"

        local_stop = {
            "the", "and", "for", "with", "from", "that", "this", "what", "when", "where", "why", "how",
            "are", "was", "were", "can", "will", "into", "about", "is", "by", "on", "or", "it", "to",
            "as", "be", "at", "in", "of", "a", "an", "its", "their", "there", "these", "those"
        }

        # If proper nouns appear (e.g., Mysore, Karnataka), prefer them first.
        proper = [w for w in raw_words if w[:1].isupper() and w.lower() not in local_stop]
        if proper:
            phrase = " ".join(proper[:2]).strip(" .,!?:;")
            if phrase:
                return phrase

        # Collect content words while skipping function words.
        content = [w for w in raw_words if w.lower() not in local_stop]
        if not content:
            return "the passage"

        # Favor a compact phrase so question text remains natural.
        phrase = " ".join(content[:max_words]).strip(" .,!?:;")
        return phrase or "the passage"

    def _literal_question(source: str, idx: int) -> str:
        phrase = _focus_phrase(source)
        templates = [
            f"Which statement about {phrase} is directly stated in the passage?",
            f"What detail about {phrase} is mentioned in the text?",
            f"According to the passage, what is true about {phrase}?",
            f"Which sentence best matches the information about {phrase}?",
        ]
        return templates[idx % len(templates)]

    def _inferential_question(source_a: str, source_b: str, idx: int) -> str:
        phrase_a = _focus_phrase(source_a)
        phrase_b = _focus_phrase(source_b)
        templates = [
            f"Why does the passage suggest that {phrase_a} affects {phrase_b}?",
            f"How does the passage show that {phrase_a} can lead to {phrase_b}?",
            f"Why is {phrase_b} a reasonable result of {phrase_a} in the passage?",
            f"How are {phrase_a} and {phrase_b} connected in a way that is not stated directly?",
            f"Why would a careful reader connect {phrase_a} with {phrase_b}?",
        ]
        return templates[idx % len(templates)]

    def _vocabulary_question(word: str, idx: int) -> str:
        templates = [
            f"In the passage, what is the closest meaning of '{word}'?",
            f"What does the word '{word}' mean as used in the passage?",
            f"As used in the text, '{word}' refers to what?",
            f"Which option best defines '{word}' in this passage?",
        ]
        return templates[idx % len(templates)]

    questions = []
    used_sentences = set()
    used_keywords = set()
    used_fragments = set()
    used_questions = set()

    for q_type in question_types:
        if q_type == "literal":
            # Pick a random sentence or fragment (rephrase it for variety)
            if random.random() < 0.5 and fragments:
                source = _pick_unique(fragments, used_fragments)
            else:
                source = _pick_unique(sentences, used_sentences)
            
            question_text = _literal_question(source, len(questions))
            # Sometimes rephrase the correct answer
            if random.random() < 0.5:
                correct_text = _shorten(_rephrase(source), 110)
            else:
                correct_text = _shorten(source, 110)
            distractors = _literal_distractors(source, 3)
            built = _build_options(correct_text, distractors)
            explanation = "The correct option is directly stated in the passage."

        elif q_type == "inferential":
            # Create inference questions based on what can reasonably be inferred from the passage
            base_sent = _pick_unique(sentences, used_sentences)
            phrase = _focus_phrase(base_sent)
            
            # Generate inference templates that make sense with the passage
            inference_templates = [
                f"Based on the passage, what can be concluded about {phrase}?",
                f"From the information given, which statement about {phrase} is a reasonable inference?",
                f"What does the passage suggest is important about {phrase}?",
                f"Which of the following is best supported by the passage's discussion of {phrase}?",
                f"What can readers infer from the passage's description of {phrase}?",
            ]
            question_text = inference_templates[len(questions) % len(inference_templates)]
            
            # Create a reasonable inference based on a clean key phrase.
            if phrase and phrase != "the passage":
                correct_text = f"The passage suggests that {phrase.lower()} plays an important role in the topic."
            else:
                correct_text = f"Based on the passage, {_shorten(base_sent, 60).lower()} appears to be important."

            distractors = _inferential_distractors(correct_text, base_sent, 3)
            built = _build_options(correct_text, distractors)
            explanation = "This inference is supported by the information presented in the passage."

        else:  # vocabulary
            # Pick a vocabulary word - filter out proper nouns (capitalized words, all-caps, common proper names)
            common_proper_nouns = {"mysore", "mysuru", "karnataka", "wadiyar", "india", "kingdom", "city"}
            generic_words = {"people", "places", "events", "related", "important", "topic", "readers", "learners", "actions", "ideas", "examples", "understanding"}
            
            # Extract words that are likely vocabulary (5+ letters, not proper nouns, not stop words)
            all_words = re.findall(r'\b[A-Za-z]{4,}\b', passage)
            vocab_candidates = [
                w.lower() for w in all_words 
                if (w.lower() not in common_proper_nouns 
                    and w.lower() not in stop_words
                        and w.lower() not in {"about", "there", "their", "which", "would", "could", "might", "should"}
                        and w.lower() not in generic_words
                    and not (w[0].isupper() and len(set(w)) == 1))  # exclude all-caps words
            ]
            
            # Remove duplicates while preserving some randomness
            vocab_candidates = list(dict.fromkeys(vocab_candidates))
            
            if vocab_candidates:
                # Prefer less common words (appear 1-2 times) over very common words
                word_freq = {}
                for w in vocab_candidates:
                    word_freq[w] = word_freq.get(w, 0) + 1
                
                # Weight towards medium-frequency words (appeared 1-2 times)
                weighted = [w for w in vocab_candidates if 1 <= word_freq.get(w, 0) <= 2]
                if not weighted:
                    weighted = vocab_candidates
                
                word = random.choice(weighted)
            else:
                # Fallback
                word = random.choice(keywords) if keywords else "understanding"
            
            question_text = _vocabulary_question(word, len(questions))
            
            # Find context sentence
            context_sent = next((s for s in sentences if word.lower() in s.lower()), sentences[0])
            
            # Build a dynamic definition
            definition_map = {
                "traditional": "passed down from the past",
                "heritage": "cultural history and valuable items from the past",
                "structures": "buildings or physical constructions",
                "functioned": "worked or served a purpose",
                "capital": "the main city of a region or country",
                "headquarters": "the main office or center of an organization",
                "dynasty": "a succession of rulers from the same family",
                "culture": "customs, beliefs, and way of life of a group",
                "palaces": "large and impressive buildings for royalty",
                "divisions": "sections or administrative areas",
                "district": "an area of a region or country",
                "official": "formal and authorized",
                "seat": "the center of power or authority",
            }
            
            if word in definition_map:
                correct = definition_map[word]
            else:
                # Use context-based definition
                preview = " ".join(context_sent.split()[:5]).lower()
                correct = f"a word related to {preview}..."
            
            # Wrong definitions: opposite/unrelated/contradictory
            wrong_defs = [
                f"Something unrelated to {phrase if 'phrase' in locals() else 'the main topic'}",
                f"The opposite meaning of '{word}'",
                "A technical term from a different field",
                f"A concept contradicting the passage's use of '{word}'",
            ]
            random.shuffle(wrong_defs)
            distractors = wrong_defs[:3]
            built = _build_options(correct, distractors)
            explanation = f"The correct meaning matches how '{word}' is used in the passage."

        if question_text in used_questions:
            question_text = f"{question_text} (different detail)"
        used_questions.add(question_text)

        questions.append({
            "question": question_text,
            "options": built["options"],
            "answer": built["answer"],
            "explanation": explanation,
            "type": q_type,
        })

    return questions


def _generate_questions_with_gemini(passage: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    global _GEMINI_FAILED
    if not GEMINI_AVAILABLE:
        raise RuntimeError("Gemini package not installed. Install with: pip install google-generativeai")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")

    genai.configure(api_key=api_key)

    # Use a known working model name – avoid listing models which may fail
    # Common working models: gemini-1.5-pro, gemini-1.5-flash
    gemini_model = "models/gemini-2.5-flash"
    try:
        model = genai.GenerativeModel(gemini_model)
        # Quick test to see if model is accessible
        _ = model.generate_content("test")
    except Exception as e:
        # If fails, try flash variant
        gemini_model = "models/gemini-1.5-flash"
        try:
            model = genai.GenerativeModel(gemini_model)
            _ = model.generate_content("test")
        except Exception as e2:
            _GEMINI_FAILED = True
            raise RuntimeError(f"Gemini model not available: {e2}")

    question_count = config.get("question_count", 5)
    difficulty = config.get("difficulty", "medium")
    passage_type = config.get("passage_type", "informational")
    student_class = config.get("student_class")
    curriculum = config.get("curriculum")
    subject = config.get("subject")
    language = str(config.get("language") or "English").strip() or "English"
    teaching_mode = config.get("teaching_mode")
    learner_context = str(config.get("learner_context") or "").strip()

    profile_note = learner_context or ""
    if not profile_note and (student_class or curriculum or subject or language or teaching_mode):
        profile_note = f"""
Learner profile (apply strictly):
- Preferred subject: {subject or 'Not specified'}
- Class/Grade: {student_class or 'Not specified'}
- Curriculum/Board: {curriculum or 'Not specified'}
- Language: {language}
- Teaching mode: {teaching_mode or 'Not specified'}
- Tune language complexity, vocabulary, and context for this learner.
"""

    prompt = f"""
You are an expert reading comprehension question generator. Based on the following passage, generate exactly {question_count} multiple-choice questions to test understanding.

Passage:
\"\"\"
{passage}
\"\"\"

Requirements:
- Each question must have 4 options (A, B, C, D) and exactly one correct answer.
- Vary question types: literal (directly stated), inferential (implied), and vocabulary (word meaning).
- Difficulty: {difficulty}.
- Passage type: {passage_type}.
{profile_note}
- Write the questions in {language}.
- If {language} is Kannada and Kannada generation is unreliable, write in English instead.
- If teaching mode is Pictorial / visual, use concrete and image-friendly wording.
- If teaching mode is Step-by-step, emphasize ordered reasoning.
- If teaching mode is Example-based, ask for examples or applications.
- If teaching mode is Story-based, frame the question in a short scenario.
- If teaching mode is Simple language, keep wording short and clear.
- If teaching mode is Activity-based, make the question action-oriented.
- If teaching mode is Practice-focused, emphasize repetition and application.
- If teaching mode is Bilingual hints, keep the main question in the chosen language and allow a light English hint if needed.
- Output format: JSON array of objects, each with keys: "question", "options" (list of 4 strings), "answer" (single letter A-D), "explanation" (brief explanation), "type" (literal/inferential/vocabulary).

Example output:
[
  {{
    "question": "What is the main idea of the passage?",
    "options": ["Option 1", "Option 2", "Option 3", "Option 4"],
    "answer": "B",
    "explanation": "The passage states that...",
    "type": "inferential"
  }}
]

Return ONLY valid JSON array, no extra text.
"""

    response = model.generate_content(prompt)
    raw_text = response.text.strip()

    json_str = _extract_json_array(raw_text)
    questions = json.loads(json_str)

    if not isinstance(questions, list):
        raise ValueError("Gemini did not return a JSON array.")

    for q in questions:
        if "question" not in q:
            q["question"] = "Missing question text."
        if "options" not in q or not isinstance(q["options"], list) or len(q["options"]) < 4:
            q["options"] = ["Option A", "Option B", "Option C", "Option D"]
        if "answer" not in q or q["answer"] not in _OPTION_LETTERS[:4]:
            q["answer"] = "A"
        if "explanation" not in q:
            q["explanation"] = "No explanation provided."
        if "type" not in q or q["type"] not in ["literal", "inferential", "vocabulary"]:
            q["type"] = "literal"
        q["answer"] = q["answer"].strip().upper()
        q["options"] = q["options"][:4]

    return questions


# ---------------------------------------------------------
# Nodes
# ---------------------------------------------------------

def PRQ_node_start(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_start"
    t0 = time.perf_counter()
    print("\n=== PRQ (Paragraph Reading + Q&A) Session Starting ===")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    result = {"flag": "success", "message": "PRQ pipeline started.", "session_meta": {"session_started_at": time.time()}}
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_start_result": result}


def PRQ_node_custom_feature_setup(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_custom_feature_setup"
    t0 = time.perf_counter()
    print("\n--- Node: Select PRQ Feature ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    print("\nChoose PRQ mode:")
    print("  1) Standard Paragraph Reading")
    print("  2) Custom Paragraph Reading")

    while True:
        mode_choice = _prompt("Enter 1 or 2: ", "1").strip()
        if mode_choice in {"1", "2"}:
            break
        print("Please enter 1 or 2.")

    if mode_choice == "1":
        result = {
            "flag": "success",
            "mode": "standard",
            "feature_name": "Standard Paragraph Reading",
            "ownership": {"paragraph": "myself", "question": "agent", "answer": "myself"},
            "message": "Standard mode selected.",
        }
    else:
        print("\nCustom Paragraph Reading: select who handles each step.")
        paragraph_owner = _choose_actor("Paragraph", default="agent")
        question_owner = _choose_actor("Question", default="agent")
        answer_owner = _choose_actor("Answer", default="myself")
        print("\nEvaluation will be done by Agent automatically.")
        result = {
            "flag": "success",
            "mode": "custom",
            "feature_name": "Custom Paragraph Reading",
            "ownership": {
                "paragraph": paragraph_owner,
                "question": question_owner,
                "answer": answer_owner,
            },
            "message": "Custom mode selected.",
        }

    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_custom_feature_setup_result": result}


def PRQ_node_input_passage(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_input_passage"
    t0 = time.perf_counter()
    print("\n--- Node: Input Passage ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    passage_text = _prompt_multiline(
        "Enter the passage/paragraph below. Press Enter on a blank line to finish:", default=""
    ).strip()
    if not passage_text:
        err = "No passage was entered."
        print(f"ERROR: {err}")
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_input_passage_result": {"flag": "error", "message": err, "passage": ""}}
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_input_passage_result": {"flag": "success", "message": "Passage accepted.", "passage": passage_text}}


def PRQ_node_prepare_passage_custom(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_prepare_passage_custom"
    t0 = time.perf_counter()
    print("\n--- Node: Prepare Passage (Custom) ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    setup = state.get("PRQ_node_custom_feature_setup_result", {})
    ownership = setup.get("ownership", {})
    paragraph_owner = ownership.get("paragraph", "myself")

    if paragraph_owner == "myself":
        result = PRQ_node_input_passage(state).get("PRQ_node_input_passage_result", {})
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_prepare_passage_custom_result": result}

    topic = _prompt("Enter paragraph topic for Agent generation: ", "Environment").strip() or "Environment"
    language = _prompt("Paragraph language: ", "English").strip() or "English"
    min_words_raw = _prompt("Minimum words: ", "150").strip()
    max_words_raw = _prompt("Maximum words: ", "220").strip()
    min_words = int(min_words_raw) if min_words_raw.isdigit() else 150
    max_words = int(max_words_raw) if max_words_raw.isdigit() else 220

    try:
        passage = _generate_passage_with_agent(topic, language=language, min_words=min_words, max_words=max_words)
        print("\nGenerated Passage:\n")
        print(passage)
        result = {"flag": "success", "message": "Passage generated by Agent.", "passage": passage}
    except Exception as e:
        result = {"flag": "error", "message": str(e), "passage": ""}

    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_prepare_passage_custom_result": result}


def PRQ_node_detect_passage_type(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_detect_passage_type"
    t0 = time.perf_counter()
    print("\n--- Node: Detect Passage Type ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    passage_result = state.get("PRQ_node_input_passage_result", {})
    passage = passage_result.get("passage", "")
    if passage_result.get("flag") != "success" or not passage:
        err = "No passage available to detect type."
        print(f"ERROR: {err}")
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_detect_passage_type_result": {"flag": "error", "message": err, "passage_type": "unknown"}}
    try:
        detected_type = _classify_passage_type_locally(passage)
        print(f"Detected passage type: {detected_type}")
        result = {"flag": "success", "message": "Passage type detected.", "passage_type": detected_type}
    except Exception as e:
        result = {"flag": "error", "message": str(e), "passage_type": "informational"}
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_detect_passage_type_result": result}


def PRQ_node_difficulty_question_count(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_difficulty_question_count"
    t0 = time.perf_counter()
    print("\n--- Node: Difficulty / Question Count ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    try:
        difficulty = _prompt("Enter difficulty (easy / medium / hard): ", "medium").strip().lower()
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"
        while True:
            raw = _prompt("Enter number of comprehension questions (max 10): ", "5").strip()
            if raw.isdigit():
                question_count = min(int(raw), 10)
                if question_count > 0:
                    if int(raw) > 10:
                        print("Capped at 10 to stay within free-tier token limits.")
                    break
            print("Invalid number. Please enter a positive integer.")
        result = {"flag": "success", "message": "Config collected.",
                  "config": {"difficulty": difficulty, "question_count": question_count}}
    except Exception as e:
        result = {"flag": "error", "message": str(e), "config": {}}
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_difficulty_question_count_result": result}


def PRQ_node_generate_passage(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_generate_passage"
    t0 = time.perf_counter()
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    passage_result = state.get("PRQ_node_input_passage_result", {})
    passage = passage_result.get("passage", "")
    config_result = state.get("PRQ_node_difficulty_question_count_result", {})
    config = config_result.get("config", {})
    passage_type = state.get("PRQ_node_detect_passage_type_result", {}).get("passage_type", "informational")
    if passage_result.get("flag") != "success" or not passage:
        err = "No passage available."
        print(f"ERROR: {err}")
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_generate_passage_result": {"flag": "error", "message": err, "passage": "", "config": {}}}
    if not config:
        config = {"difficulty": "medium", "question_count": 5}
    config["passage_type"] = passage_type
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_generate_passage_result": {"flag": "success", "message": "Passage ready.", "passage": passage, "config": config}}


def PRQ_node_generate_questions(state: Dict[str, Any]) -> Dict[str, Any]:
    global _GEMINI_FAILED
    NODE_NAME = "PRQ_node_generate_questions"
    t0 = time.perf_counter()
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    passage_result = state.get("PRQ_node_generate_passage_result", {})
    passage = passage_result.get("passage", "")
    config = passage_result.get("config", {})
    if passage_result.get("flag") != "success" or not passage:
        err = "No passage available to generate questions."
        print(f"ERROR: {err}")
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_generate_questions_result": {"flag": "error", "message": err, "questions": []}}

    question_count = int(config.get("question_count", 5))
    difficulty = config.get("difficulty", "medium")
    print(f"\n--- Node: Generating {question_count} Questions ({difficulty}) ---")

    # Check if we should force regeneration (e.g., "same passage" action)
    force_regenerate = state.get("PRQ_node_redirect_prq_generation_result", {}).get("force_regenerate", False)

    # Decide generator - skip Gemini if it previously failed
    use_gemini = False
    if not _GEMINI_FAILED and GEMINI_AVAILABLE and os.getenv("GEMINI_API_KEY"):
        use_gemini = True
        print("Using Gemini API for question generation...")
    else:
        if _GEMINI_FAILED:
            print("Gemini previously failed. Using local generator.")
        elif not GEMINI_AVAILABLE:
            print("Gemini package not installed. Falling back to local generator.")
        elif not os.getenv("GEMINI_API_KEY"):
            print("GEMINI_API_KEY not found in environment. Falling back to local generator.")
        print("Using local question generator.")

    generator_name = "gemini" if use_gemini else "local"
    cache_key = _question_cache_key(passage, config, generator_name)

    # Check cache unless forced to regenerate
    if not force_regenerate and cache_key in QUESTION_CACHE:
        cached = json.loads(json.dumps(QUESTION_CACHE[cache_key]))
        print("✅ Using cached questions.")
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_generate_questions_result": {"flag": "success", "message": "From cache.", "questions": cached, "config": config}}

    try:
        if use_gemini:
            questions = _generate_questions_with_gemini(passage, config)
        else:
            # Use microsecond seed for variety
            questions = _generate_local_questions(passage, question_count, difficulty=difficulty, seed=int(time.time() * 1_000_000))
        # Normalize answer letters
        for q in questions:
            q["answer"] = _normalize_answer_letter(str(q.get("answer", "")), q.get("options", []))
        # Cache the result (only if not force_regenerate)
        if not force_regenerate:
            QUESTION_CACHE[cache_key] = json.loads(json.dumps(questions))
            _save_cache(QUESTION_CACHE)
        result = {"flag": "success", "message": "Questions generated successfully.", "questions": questions, "config": config}
    except Exception as e:
        print(f"Error generating questions with {generator_name}: {e}")
        # If Gemini failed, remember it for the rest of the session
        if use_gemini:
            _GEMINI_FAILED = True
            print("Falling back to local question generator...")
            try:
                questions = _generate_local_questions(passage, question_count, difficulty=difficulty, seed=int(time.time() * 1_000_000))
                for q in questions:
                    q["answer"] = _normalize_answer_letter(str(q.get("answer", "")), q.get("options", []))
                # Cache under local generator key (if not force_regenerate)
                local_cache_key = _question_cache_key(passage, config, "local")
                if not force_regenerate:
                    QUESTION_CACHE[local_cache_key] = json.loads(json.dumps(questions))
                    _save_cache(QUESTION_CACHE)
                result = {"flag": "success", "message": "Questions generated locally (fallback).", "questions": questions, "config": config}
            except Exception as e2:
                result = {"flag": "error", "message": str(e2), "questions": [], "config": config}
        else:
            result = {"flag": "error", "message": str(e), "questions": [], "config": config}

    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_generate_questions_result": result}


def PRQ_node_generate_questions_custom(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_generate_questions_custom"
    t0 = time.perf_counter()
    print("\n--- Node: Prepare Questions (Custom) ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")

    setup = state.get("PRQ_node_custom_feature_setup_result", {})
    ownership = setup.get("ownership", {})
    question_owner = ownership.get("question", "agent")

    if question_owner == "agent":
        result = PRQ_node_generate_questions(state).get("PRQ_node_generate_questions_result", {})
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_generate_questions_custom_result": result}

    passage_result = state.get("PRQ_node_generate_passage_result", {})
    if passage_result.get("flag") != "success" or not passage_result.get("passage"):
        err = "No passage available to add manual questions."
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_generate_questions_custom_result": {"flag": "error", "message": err, "questions": []}}

    config = passage_result.get("config", {})
    question_count = int(config.get("question_count", 5))
    questions = _collect_questions_from_user(question_count)
    result = {
        "flag": "success",
        "message": "Questions captured from user.",
        "questions": questions,
        "config": config,
    }
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_generate_questions_custom_result": result}


def PRQ_node_present_passage_and_answer(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_present_passage_and_answer"
    t0 = time.perf_counter()
    print("\n--- Node: Present Passage & Answer Questions ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    passage = state.get("PRQ_node_generate_passage_result", {}).get("passage", "")
    q_result = state.get("PRQ_node_generate_questions_result", {})
    questions: List[Dict[str, Any]] = q_result.get("questions", [])
    if q_result.get("flag") != "success" or not questions:
        err = "No questions available."
        print(f"ERROR: {err}")
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_present_passage_and_answer_result": {"flag": "error", "message": err, "user_answers": []}}

    setup = state.get("PRQ_node_custom_feature_setup_result", {})
    ownership = setup.get("ownership", {})
    answer_owner = ownership.get("answer", "myself")

    print("\n" + "=" * 60)
    print("              READ THE FOLLOWING PASSAGE")
    print("=" * 60)
    print(passage)
    print("=" * 60)

    user_answers: List[str] = []
    if answer_owner == "agent":
        print("\nAgent is answering questions...")
        cfg = state.get("PRQ_node_generate_passage_result", {}).get("config", {})
        language = cfg.get("language", "English")
        user_answers = _answer_questions_with_agent(passage, questions, language=language)
        for idx, q in enumerate(questions, start=1):
            print(f"\nQ{idx}: {q.get('question')}")
            options = q.get("options", [])
            for i, opt in enumerate(options):
                if i < len(_OPTION_LETTERS):
                    print(f"  {_OPTION_LETTERS[i]}) {opt}")
            shown = user_answers[idx - 1] if idx - 1 < len(user_answers) else "A"
            print(f"Agent answer: {shown}")
    else:
        _prompt("\nPress Enter when ready to answer the questions...", "")
        for idx, q in enumerate(questions, start=1):
            print(f"\nQ{idx}: {q.get('question')}")
            options = q.get("options", [])
            for i, opt in enumerate(options):
                if i < len(_OPTION_LETTERS):
                    print(f"  {_OPTION_LETTERS[i]}) {opt}")
            while True:
                ans = _prompt(f"Your answer (A-{_OPTION_LETTERS[len(options)-1]}): ", "A").strip().upper()
                if ans in _OPTION_LETTERS[:len(options)]:
                    user_answers.append(ans)
                    break
                print(f"Invalid. Enter A-{_OPTION_LETTERS[len(options)-1]}.")

    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_present_passage_and_answer_result": {"flag": "success", "message": "Answers collected.", "user_answers": user_answers}}


def PRQ_node_record_answers_track_progress(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_record_answers_track_progress"
    t0 = time.perf_counter()
    print("\n--- Node: Agent Evaluation / Track Progress ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    q_result = state.get("PRQ_node_generate_questions_result", {})
    questions: List[Dict[str, Any]] = q_result.get("questions", [])
    ans_result = state.get("PRQ_node_present_passage_and_answer_result", {})
    user_answers: List[str] = ans_result.get("user_answers", [])
    if q_result.get("flag") != "success" or ans_result.get("flag") != "success":
        err = "Questions or answers unavailable."
        print(f"ERROR: {err}")
        logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
        return {"PRQ_node_record_answers_track_progress_result": {"flag": "error", "message": err}}

    total = min(len(questions), len(user_answers))
    correct_count = 0
    details: List[Dict[str, Any]] = []
    type_stats: Dict[str, Dict[str, Any]] = {}

    for i in range(total):
        q = questions[i]
        correct_letter = q.get("answer", "A").strip().upper()
        if correct_letter not in _OPTION_LETTERS:
            correct_letter = "A"
        user_ans = user_answers[i]
        is_correct = user_ans == correct_letter
        if is_correct:
            correct_count += 1
        q_type = q.get("type", "literal")
        if q_type not in type_stats:
            type_stats[q_type] = {"total": 0, "correct": 0}
        type_stats[q_type]["total"] += 1
        if is_correct:
            type_stats[q_type]["correct"] += 1
        details.append({
            "question": q.get("question"),
            "correct_answer": correct_letter,
            "user_answer": user_ans,
            "is_correct": is_correct,
            "explanation": q.get("explanation"),
            "type": q_type
        })

    score_pct = (correct_count / total * 100) if total > 0 else 0.0
    print(f"\nYou answered {correct_count}/{total} correctly. Score: {score_pct:.2f}%")
    for idx, d in enumerate(details, start=1):
        print(f"\nQ{idx}: {d['question']}")
        print(f"Type: {d['type'].capitalize()} | Your: {d['user_answer']} | Correct: {d['correct_answer']} | {'✅' if d['is_correct'] else '❌'}")
        if d["explanation"]:
            print(f"Explanation: {d['explanation']}")

    print("\n=== Question-Type-wise Performance ===")
    type_percentages: Dict[str, float] = {}
    for q_type, stats in type_stats.items():
        pct = (stats["correct"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
        type_percentages[q_type] = pct
        print(f"- {q_type.capitalize()}: {stats['correct']}/{stats['total']} ({pct:.2f}%)")

    weak_types: List[str] = []
    equal_performance = False
    equal_pct = 0.0
    if type_percentages:
        max_pct = max(type_percentages.values())
        min_pct = min(type_percentages.values())
        if abs(max_pct - min_pct) < 1e-9:
            equal_performance = True
            equal_pct = max_pct
            print(f"\nEqual performance across all types at ~{equal_pct:.2f}%.")
        else:
            weak_types = [t for t, p in type_percentages.items() if p == min_pct]
            best_types = [t for t, p in type_percentages.items() if p == max_pct]
            print("\nStrongest:", ", ".join(f"{t.capitalize()} ({type_percentages[t]:.2f}%)" for t in best_types))
            print("Weakest:", ", ".join(f"{t.capitalize()} ({type_percentages[t]:.2f}%)" for t in weak_types))

    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_record_answers_track_progress_result": {
        "flag": "success", "message": "Progress recorded.",
        "total_questions": total, "correct_count": correct_count, "score_percent": score_pct,
        "details": details, "type_stats": type_stats, "type_percentages": type_percentages,
        "equal_performance": equal_performance, "equal_pct": equal_pct, "weak_types": weak_types,
    }}


def PRQ_node_progress_check(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_progress_check"
    t0 = time.perf_counter()
    print("\n--- Node: Progress Check ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    score = state.get("PRQ_node_record_answers_track_progress_result", {}).get("score_percent", 0.0)
    threshold = 60.0
    passed = score >= threshold
    print(f"{'Good job!' if passed else 'Keep practicing!'} Score: {score:.2f}% (threshold: {threshold}%)")
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_progress_check_result": {"flag": "success", "message": "Progress checked.", "score_percent": score, "passed": passed, "threshold": threshold}}


def PRQ_node_prq_completed(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_prq_completed"
    t0 = time.perf_counter()
    print("\n--- Node: PRQ Session Completed ---\n✅ PRQ session completed.")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_prq_completed_result": {"flag": "success", "message": "PRQ completed."}}


def PRQ_node_post_prq_processing(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_post_prq_processing"
    t0 = time.perf_counter()
    print("\n--- Node: Post PRQ Processing ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    score = state.get("PRQ_node_progress_check_result", {}).get("score_percent", 0.0)
    print(f"Summary: Overall PRQ score was {score:.2f}%.")
    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_post_prq_processing_result": {"flag": "success", "message": "Post processing done.", "score_percent": score}}


def PRQ_node_redirect_prq_generation(state: Dict[str, Any]) -> Dict[str, Any]:
    NODE_NAME = "PRQ_node_redirect_prq_generation"
    t0 = time.perf_counter()
    print("\n--- Node: Continue or Next? ---")
    logger.info(f"[DEBUG] Entering node '{NODE_NAME}'")
    print("\nOptions:\n  1) More questions for THIS passage\n  2) NEW passage\n  3) Exit and view report")
    while True:
        choice = _prompt("Enter 1 / 2 / 3: ", "3").strip()
        if choice in {"1", "2", "3"}:
            break
        print("Please enter 1, 2, or 3.")

    action = {"1": "same_passage", "2": "new_passage", "3": "exit"}[choice]
    prefill_config = None
    if action == "same_passage":
        p_res = state.get("PRQ_node_generate_passage_result", {})
        if p_res.get("passage"):
            prefill_config = {"passage": p_res["passage"], "config": p_res.get("config", {})}

    logger.info(f"[COMPLETED] {NODE_NAME} in {time.perf_counter() - t0:.2f}s")
    return {"PRQ_node_redirect_prq_generation_result": {
        "flag": "success", "message": f"User chose: {action}",
        "action": action, "redirect": action != "exit", "prefill_config": prefill_config,
        "force_regenerate": (action == "same_passage")
    }}


# ---------------------------------------------------------
# Report / Certificate helpers
# ---------------------------------------------------------

def print_session_aggregate_report(session_history: List[Dict[str, Any]]) -> None:
    if not session_history:
        print("\nNo PRQ sessions were taken.")
        return
    agg: Dict[str, Dict[str, int]] = {}
    for run in session_history:
        for q_type, stats in run.get("type_stats", {}).items():
            if q_type not in agg:
                agg[q_type] = {"total": 0, "correct": 0}
            agg[q_type]["total"] += stats.get("total", 0)
            agg[q_type]["correct"] += stats.get("correct", 0)
    print("\n=== SESSION AGGREGATED QUESTION-TYPE PERFORMANCE ===")
    overall: Dict[str, float] = {}
    for q_type, stats in agg.items():
        pct = (stats["correct"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
        overall[q_type] = pct
        print(f"- {q_type.capitalize()}: {stats['correct']}/{stats['total']} ({pct:.2f}%)")
    if overall:
        max_p, min_p = max(overall.values()), min(overall.values())
        if abs(max_p - min_p) < 1e-9:
            print(f"\nAll types equal ~{max_p:.2f}%. Consistently good!")
        else:
            print("Best:", ", ".join(t.capitalize() for t, p in overall.items() if abs(p - max_p) < 1e-9))
            print("Worst:", ", ".join(t.capitalize() for t, p in overall.items() if abs(p - min_p) < 1e-9))
    try:
        with open("prq_session_history.json", "w", encoding="utf-8") as f:
            json.dump(session_history, f, indent=2)
        print("\nSaved session history to prq_session_history.json")
    except Exception as e:
        print(f"(Warning) Could not write session history: {e}")


def generate_certificate(candidate_name: str, overall_score: float, filename: str = "prq_certificate.pdf") -> None:
    pdf_filename = filename if filename.lower().endswith(".pdf") else "prq_certificate.pdf"
    if overall_score < 70.0:
        print(f"\nScore {overall_score:.2f}% < 70%. No certificate generated.")
        return

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError:
        print("(Warning) reportlab not installed. Cannot generate PDF certificate.")
        return

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CertificateTitle",
        parent=styles["Heading1"],
        alignment=1,
        spaceAfter=18,
    )
    center_style = ParagraphStyle(
        "CertificateCenter",
        parent=styles["BodyText"],
        alignment=1,
        leading=18,
        fontSize=12,
    )

    doc = SimpleDocTemplate(pdf_filename, pagesize=A4, rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54)
    story: List[Any] = []

    story.append(Paragraph("CERTIFICATE OF ACHIEVEMENT", title_style))
    story.append(Paragraph("Paragraph Reading & Comprehension (PRQ)", center_style))
    story.append(Spacer(1, 0.4 * inch))

    info_table = Table([
        [Paragraph("<b>Candidate</b>", styles["BodyText"]), Paragraph(candidate_name, styles["BodyText"])],
        [Paragraph("<b>Score</b>", styles["BodyText"]), Paragraph(f"{overall_score:.2f}%", styles["BodyText"])],
        [Paragraph("<b>Issued On</b>", styles["BodyText"]), Paragraph(time.strftime("%B %d, %Y"), styles["BodyText"])],
    ], colWidths=[1.4 * inch, 4.6 * inch])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
        ("BOX", (0, 0), (-1, -1), 1, colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    story.append(info_table)
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("This certificate confirms successful completion of the PRQ assessment with a score of 70% or higher.", center_style))

    try:
        doc.build(story)
        print(f"\n✅ Certificate PDF saved to: {pdf_filename}")
    except Exception as e:
        print(f"(Warning) Could not generate certificate PDF: {e}")


def generate_detailed_report(candidate_name: str, session_history: List[Dict[str, Any]]) -> str:
    lines = ["\n" + "=" * 80, "                    DETAILED PRQ SESSION REPORT", "=" * 80,
             f"\nCandidate: {candidate_name}", f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Total Sessions: {len(session_history)}", "\n" + "-" * 80, "SESSION-BY-SESSION BREAKDOWN", "-" * 80]
    total_q = total_c = 0
    for idx, session in enumerate(session_history, 1):
        cfg = session.get("config", {})
        lines += [f"\n[Session {idx}]",
                  f"  Passage Type: {cfg.get('passage_type','?')}",
                  f"  Difficulty: {cfg.get('difficulty','?')}",
                  f"  Questions: {cfg.get('question_count',0)}",
                  f"  Score: {session.get('score_percent', 0.0):.2f}%"]

        passage = session.get("passage", "")
        if passage:
            lines += ["  Passage:", f"    {passage.replace(chr(10), chr(10) + '    ')}"]

        questions = session.get("questions", [])
        user_answers = session.get("user_answers", [])
        answer_details = session.get("answer_details", [])

        if questions:
            lines.append("  Questions:")
            for q_idx, question in enumerate(questions, 1):
                user_answer = user_answers[q_idx - 1] if q_idx - 1 < len(user_answers) else "?"
                detail = answer_details[q_idx - 1] if q_idx - 1 < len(answer_details) else {}
                correct_answer = detail.get("correct_answer", question.get("answer", "?"))
                explanation = detail.get("explanation", question.get("explanation", ""))
                lines.append(f"    Q{q_idx}: {question.get('question', '?')}")
                for opt_idx, option in enumerate(question.get("options", [])):
                    letter = _OPTION_LETTERS[opt_idx] if opt_idx < len(_OPTION_LETTERS) else str(opt_idx + 1)
                    lines.append(f"      {letter}) {option}")
                lines.append(f"      Your Answer: {user_answer}")
                lines.append(f"      Correct Answer: {correct_answer}")
                if explanation:
                    lines.append(f"      Explanation: {explanation}")

        for q_type, stats in session.get("type_stats", {}).items():
            t, c = stats.get("total", 0), stats.get("correct", 0)
            pct = (c / t * 100) if t > 0 else 0.0
            lines.append(f"    - {q_type.capitalize()}: {c}/{t} ({pct:.2f}%)")
            total_q += t
            total_c += c
    overall = (total_c / total_q * 100) if total_q > 0 else 0.0
    lines += ["\n" + "-" * 80, "OVERALL", "-" * 80,
              f"Questions: {total_q} | Correct: {total_c} | Score: {overall:.2f}%",
              f"Certificate: {'YES ✅' if overall >= 70.0 else 'NO ❌'}", "\n" + "=" * 80]

    pdf_filename = "prq_detailed_report.pdf"
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError:
        print("(Warning) reportlab not installed. Cannot generate PDF report.")
        return "\n".join(lines)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        alignment=1,
        spaceAfter=10,
    )
    section_style = styles["Heading2"]
    body_style = styles["BodyText"]

    doc = SimpleDocTemplate(pdf_filename, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story: List[Any] = []

    story.append(Paragraph("DETAILED PRQ SESSION REPORT", title_style))
    story.append(Paragraph(f"Candidate: {candidate_name}", body_style))
    story.append(Paragraph(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", body_style))
    story.append(Paragraph(f"Total Sessions: {len(session_history)}", body_style))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("SESSION-BY-SESSION BREAKDOWN", section_style))
    story.append(Spacer(1, 0.1 * inch))

    total_q = 0
    total_c = 0
    for idx, session in enumerate(session_history, 1):
        cfg = session.get("config", {})
        story.append(Paragraph(f"Session {idx}", section_style))
        rows = [
            ["Passage Type", str(cfg.get("passage_type", "?"))],
            ["Difficulty", str(cfg.get("difficulty", "?"))],
            ["Questions", str(cfg.get("question_count", 0))],
            ["Score", f"{session.get('score_percent', 0.0):.2f}%"],
        ]
        session_table = Table(rows, colWidths=[1.5 * inch, 4.8 * inch])
        session_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(session_table)
        story.append(Spacer(1, 0.08 * inch))

        passage = session.get("passage", "")
        if passage:
            story.append(Paragraph("Passage", styles["Heading3"]))
            story.append(Paragraph(passage.replace("\n", "<br/>"), body_style))
            story.append(Spacer(1, 0.08 * inch))

        questions = session.get("questions", [])
        user_answers = session.get("user_answers", [])
        answer_details = session.get("answer_details", [])
        if questions:
            story.append(Paragraph("Questions and Answers", styles["Heading3"]))
            for q_idx, question in enumerate(questions, 1):
                detail = answer_details[q_idx - 1] if q_idx - 1 < len(answer_details) else {}
                user_answer = user_answers[q_idx - 1] if q_idx - 1 < len(user_answers) else "?"
                correct_answer = detail.get("correct_answer", question.get("answer", "?"))
                explanation = detail.get("explanation", question.get("explanation", ""))
                qa_rows = [["Question", question.get("question", "?")], ["Your Answer", user_answer], ["Correct Answer", correct_answer]]
                qa_table = Table(qa_rows, colWidths=[1.5 * inch, 4.8 * inch])
                qa_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                    ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]))
                story.append(qa_table)
                options = question.get("options", [])
                if options:
                    option_lines = [f"{_OPTION_LETTERS[i]}) {opt}" for i, opt in enumerate(options) if i < len(_OPTION_LETTERS)]
                    story.append(Paragraph("<br/>".join(option_lines), body_style))
                if explanation:
                    story.append(Paragraph(f"<b>Explanation:</b> {explanation}", body_style))
                story.append(Spacer(1, 0.1 * inch))

        type_stats = session.get("type_stats", {})
        if type_stats:
            type_rows = [["Question Type", "Correct / Total", "Accuracy"]]
            for q_type, stats in type_stats.items():
                t, c = stats.get("total", 0), stats.get("correct", 0)
                pct = (c / t * 100) if t > 0 else 0.0
                type_rows.append([q_type.capitalize(), f"{c}/{t}", f"{pct:.2f}%"])
                total_q += t
                total_c += c
            type_table = Table(type_rows, colWidths=[2.2 * inch, 1.5 * inch, 1.5 * inch])
            type_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(type_table)
            story.append(Spacer(1, 0.15 * inch))

    overall = (total_c / total_q * 100) if total_q > 0 else 0.0
    story.append(Paragraph("OVERALL PERFORMANCE", section_style))
    summary_rows = [
        ["Total Questions Answered", str(total_q)],
        ["Total Correct", str(total_c)],
        ["Overall Score", f"{overall:.2f}%"],
        ["Certificate Eligible", "YES ✅" if overall >= 70.0 else "NO ❌"],
    ]
    summary_table = Table(summary_rows, colWidths=[2.4 * inch, 4.5 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)

    try:
        doc.build(story)
        print(f"\n📄 Report saved to: {pdf_filename}")
    except Exception as e:
        print(f"(Warning) Could not generate PDF report: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------

if __name__ == "__main__":
    candidate_name = _prompt("\nEnter Candidate Name: ", "Unknown Candidate").strip() or "Unknown Candidate"
    print(f"\n✅ Welcome, {candidate_name}!")

    session_history: List[Dict[str, Any]] = []
    saved_prefill: Optional[Dict[str, Any]] = None

    while True:
        state: Dict[str, Any] = {}
        state.update(PRQ_node_start(state))

        if saved_prefill and "mode_setup" in saved_prefill:
            state["PRQ_node_custom_feature_setup_result"] = saved_prefill["mode_setup"]
        else:
            state.update(PRQ_node_custom_feature_setup(state))

        mode_setup = state.get("PRQ_node_custom_feature_setup_result", {})
        mode = mode_setup.get("mode", "standard")

        if saved_prefill and "passage" in saved_prefill:
            print("\nContinuing with the same passage...")
            state["PRQ_node_input_passage_result"] = {"flag": "success", "passage": saved_prefill["passage"]}
            state["PRQ_node_detect_passage_type_result"] = {
                "flag": "success", "passage_type": saved_prefill["config"].get("passage_type", "informational")
            }
            state.update(PRQ_node_difficulty_question_count(state))
            state.update(PRQ_node_generate_passage(state))
        else:
            if mode == "custom":
                state.update(PRQ_node_prepare_passage_custom(state))
                custom_passage = state.get("PRQ_node_prepare_passage_custom_result", {})
                state["PRQ_node_input_passage_result"] = custom_passage
                state.update(PRQ_node_detect_passage_type(state))
                state.update(PRQ_node_difficulty_question_count(state))
                state.update(PRQ_node_generate_passage(state))
                # Preserve language when paragraph is generated by agent.
                if mode_setup.get("ownership", {}).get("paragraph") == "agent":
                    p_cfg = state.get("PRQ_node_generate_passage_result", {}).get("config", {})
                    p_cfg["language"] = _prompt("Question language: ", "English").strip() or "English"
            else:
                state.update(PRQ_node_input_passage(state))
                state.update(PRQ_node_detect_passage_type(state))
                state.update(PRQ_node_difficulty_question_count(state))
                state.update(PRQ_node_generate_passage(state))

        if mode == "custom":
            state.update(PRQ_node_generate_questions_custom(state))
            state["PRQ_node_generate_questions_result"] = state.get("PRQ_node_generate_questions_custom_result", {})
        else:
            state.update(PRQ_node_generate_questions(state))

        state.update(PRQ_node_present_passage_and_answer(state))
        state.update(PRQ_node_record_answers_track_progress(state))

        rec = state.get("PRQ_node_record_answers_track_progress_result", {})
        if rec.get("flag") == "success":
            session_history.append({
                "candidate_name": candidate_name,
                "config": state.get("PRQ_node_generate_passage_result", {}).get("config", {}),
                "passage": state.get("PRQ_node_generate_passage_result", {}).get("passage", ""),
                "questions": state.get("PRQ_node_generate_questions_result", {}).get("questions", []),
                "user_answers": state.get("PRQ_node_present_passage_and_answer_result", {}).get("user_answers", []),
                "answer_details": rec.get("details", []),
                "type_stats": rec.get("type_stats", {}),
                "score_percent": rec.get("score_percent", 0.0),
                "timestamp": time.time(),
            })

        state.update(PRQ_node_progress_check(state))
        state.update(PRQ_node_prq_completed(state))
        state.update(PRQ_node_post_prq_processing(state))
        state.update(PRQ_node_redirect_prq_generation(state))

        action = state.get("PRQ_node_redirect_prq_generation_result", {}).get("action", "exit")
        if action == "exit":
            break
        elif action == "same_passage":
            saved_prefill = state.get("PRQ_node_redirect_prq_generation_result", {}).get("prefill_config")
            if saved_prefill is not None:
                saved_prefill["mode_setup"] = state.get("PRQ_node_custom_feature_setup_result", {})
        else:
            saved_prefill = None

    print("\n" + "=" * 80)
    print("                    Thank you for using PRQ System!")
    print("=" * 80)

    if session_history:
        report_text = generate_detailed_report(candidate_name, session_history)
        print(report_text)
        total_q = total_c = 0
        for s in session_history:
            for stats in s.get("type_stats", {}).values():
                total_q += stats.get("total", 0)
                total_c += stats.get("correct", 0)
        overall_score = (total_c / total_q * 100) if total_q > 0 else 0.0
        print("\n📄 Report saved to: prq_detailed_report.pdf")
        generate_certificate(candidate_name, overall_score, filename="prq_certificate.pdf")
        try:
            with open("prq_session_history.json", "w", encoding="utf-8") as f:
                json.dump(session_history, f, indent=2)
            print("📊 Session history saved to: prq_session_history.json")
        except Exception as e:
            print(f"(Warning) Could not save session history: {e}")
    else:
        print("\nNo sessions were completed.")