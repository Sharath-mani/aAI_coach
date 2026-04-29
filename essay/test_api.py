#!/usr/bin/env python3
"""
Test script for the Essay Evaluation FastAPI
"""

import requests
import json

BASE_URL = "http://localhost:8000"

def test_valid_parameters():
    """Test getting valid parameters."""
    try:
        response = requests.get(f"{BASE_URL}/valid-parameters")
        if response.status_code == 200:
            params = response.json()["parameters"]
            print(f"✓ Valid parameters endpoint works. Found {len(params)} parameters.")
            return True
        else:
            print(f"✗ Valid parameters endpoint failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Valid parameters endpoint error: {e}")
        return False

def test_evaluate_essay():
    """Test essay evaluation."""
    test_essay = """
    Technology has revolutionized education in many ways. Online learning platforms provide
    access to quality education for students worldwide. Digital tools enhance interactive
    learning experiences. However, the digital divide remains a challenge that needs addressing.
    """

    payload = {
        "essay": test_essay,
        "parameters": ["structure", "grammar", "creativity"],
        "candidate_name": "Test User"
    }

    try:
        response = requests.post(f"{BASE_URL}/evaluate", json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            print("✓ Essay evaluation endpoint works.")
            print(f"  Average score: {result.get('avg_score', 'N/A')}")
            print(f"  Essay topic: {result.get('essay_topic', 'N/A')}")
            return True
        else:
            print(f"✗ Essay evaluation endpoint failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ Essay evaluation endpoint error: {e}")
        return False

def test_generate_question():
    """Test question generation."""
    payload = {
        "basis": "artificial intelligence"
    }

    try:
        response = requests.post(f"{BASE_URL}/generate-question", json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            print("✓ Question generation endpoint works.")
            print(f"  Generated question: {result.get('question', 'N/A')}")
            return True
        else:
            print(f"✗ Question generation endpoint failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"✗ Question generation endpoint error: {e}")
        return False

def main():
    """Run all tests."""
    print("Testing Essay Evaluation FastAPI...")
    print("=" * 50)

    # Test 1: Valid parameters
    print("\n1. Testing /valid-parameters endpoint...")
    test1_pass = test_valid_parameters()

    # Test 2: Essay evaluation
    print("\n2. Testing /evaluate endpoint...")
    test2_pass = test_evaluate_essay()

    # Test 3: Question generation
    print("\n3. Testing /generate-question endpoint...")
    test3_pass = test_generate_question()

    print("\n" + "=" * 50)
    print("Test Results:")
    print(f"  Valid parameters: {'PASS' if test1_pass else 'FAIL'}")
    print(f"  Essay evaluation: {'PASS' if test2_pass else 'FAIL'}")
    print(f"  Question generation: {'PASS' if test3_pass else 'FAIL'}")

    if all([test1_pass, test2_pass, test3_pass]):
        print("\n🎉 All tests passed! FastAPI is working correctly.")
    else:
        print("\n⚠️  Some tests failed. Check the API server and Gemini API key.")

if __name__ == "__main__":
    main()