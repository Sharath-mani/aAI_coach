#!/usr/bin/env python3
"""
Test Gemini API key validity
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import google.generativeai as genai

def test_api_key():
    """Test if the Gemini API key is valid."""
    api_key = os.environ.get("GEMINI_API_KEY", "")

    if not api_key:
        print("❌ No GEMINI_API_KEY environment variable set")
        return False

    if api_key.startswith("AIzaSyAErWl4L0uFqNsz6Ge-gvLnUxmpuxdah60") or api_key.startswith("AIzaSyAI0u4LnjGAUck8mTRrFirwgMnafLI2y8w"):
        print("❌ Using placeholder API key - get a real key from Google AI Studio")
        return False

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        # Simple test prompt
        response = model.generate_content("Say 'Hello' in one word.")
        if response and response.text:
            print("✅ API key is valid!")
            print(f"Response: {response.text.strip()}")
            return True
        else:
            print("❌ API key test failed - no response")
            return False

    except Exception as e:
        print(f"❌ API key test failed: {str(e)}")
        return False

if __name__ == "__main__":
    print("Testing Gemini API Key...")
    print("=" * 40)
    success = test_api_key()

    if not success:
        print("\n🔧 To fix this:")
        print("1. Go to https://makersuite.google.com/app/apikey")
        print("2. Create a new API key")
        print("3. Edit the .env file and replace 'your-gemini-api-key-here' with your actual key")
        print("4. Or run: python setup_api_key.py")
        print("5. Test again with: python test_api_key.py")
    else:
        print("\n🎉 API key is working! You can now run the APIs.")