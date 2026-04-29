#!/usr/bin/env python3
"""
Test script for the Quiz Generation FastAPI
"""

import requests
import json

BASE_URL = "http://localhost:8001"

def test_health():
    """Test health endpoint."""
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("✓ Health check passed.")
            return True
        else:
            print(f"✗ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Health check error: {e}")
        return False

def test_generate_quiz():
    """Test quiz generation."""
    payload = {
        "topic": "Python Programming",
        "language": "English",
        "difficulty": "medium",
        "quiz_count": 5
    }

    try:
        response = requests.post(f"{BASE_URL}/generate-quiz", json=payload, timeout=60)
        if response.status_code == 200:
            result = response.json()
            quiz = result.get("quiz", [])
            print(f"✓ Quiz generation works. Generated {len(quiz)} questions.")
            if quiz:
                print(f"  First question: {quiz[0].get('question', 'N/A')[:50]}...")
            return True
        else:
            print(f"✗ Quiz generation failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ Quiz generation error: {e}")
        return False

def test_evaluate_answers():
    """Test answer evaluation."""
    # First generate a quiz
    generate_payload = {
        "topic": "Mathematics",
        "language": "English",
        "difficulty": "easy",
        "quiz_count": 3
    }

    try:
        gen_response = requests.post(f"{BASE_URL}/generate-quiz", json=generate_payload, timeout=60)
        if gen_response.status_code != 200:
            print("✗ Could not generate quiz for evaluation test")
            return False

        quiz = gen_response.json().get("quiz", [])
        if not quiz:
            print("✗ Generated quiz is empty")
            return False

        # Prepare answers (just pick first option for each)
        answers = [q.get("options", [""])[0] for q in quiz]

        eval_payload = {
            "quiz": quiz,
            "user_answers": answers,
            "candidate_name": "Test User"
        }

        eval_response = requests.post(f"{BASE_URL}/evaluate-answers", json=eval_payload, timeout=30)
        if eval_response.status_code == 200:
            result = eval_response.json()
            score = result.get("score_percent", 0)
            print(f"✓ Answer evaluation works. Score: {score:.2f}%")
            return True
        else:
            print(f"✗ Answer evaluation failed: {eval_response.status_code} - {eval_response.text}")
            return False
    except Exception as e:
        print(f"✗ Answer evaluation error: {e}")
        return False

def test_focused_quiz():
    """Test quiz generation with focus categories."""
    payload = {
        "topic": "Web Development",
        "language": "English",
        "difficulty": "hard",
        "quiz_count": 4,
        "focus_categories": ["Frontend", "Backend"]
    }

    try:
        response = requests.post(f"{BASE_URL}/generate-quiz", json=payload, timeout=60)
        if response.status_code == 200:
            result = response.json()
            quiz = result.get("quiz", [])
            print(f"✓ Focused quiz generation works. Generated {len(quiz)} questions.")
            categories = [q.get("category") for q in quiz]
            print(f"  Categories: {set(categories)}")
            return True
        else:
            print(f"✗ Focused quiz generation failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Focused quiz generation error: {e}")
        return False

def main():
    """Run all tests."""
    print("Testing Quiz Generation FastAPI...")
    print("=" * 50)

    # Test 1: Health check
    print("\n1. Testing /health endpoint...")
    test1_pass = test_health()

    # Test 2: Quiz generation
    print("\n2. Testing /generate-quiz endpoint...")
    test2_pass = test_generate_quiz()

    # Test 3: Answer evaluation
    print("\n3. Testing /evaluate-answers endpoint...")
    test3_pass = test_evaluate_answers()

    # Test 4: Focused quiz
    print("\n4. Testing focused quiz generation...")
    test4_pass = test_focused_quiz()

    print("\n" + "=" * 50)
    print("Test Results:")
    print(f"  Health check: {'PASS' if test1_pass else 'FAIL'}")
    print(f"  Quiz generation: {'PASS' if test2_pass else 'FAIL'}")
    print(f"  Answer evaluation: {'PASS' if test3_pass else 'FAIL'}")
    print(f"  Focused quiz: {'PASS' if test4_pass else 'FAIL'}")

    if all([test1_pass, test2_pass, test3_pass, test4_pass]):
        print("\n🎉 All tests passed! Quiz API is working correctly.")
    else:
        print("\n⚠️  Some tests failed. Check the API server and Gemini API key.")

if __name__ == "__main__":
    main()