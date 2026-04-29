#!/usr/bin/env python3
"""
Setup script for Gemini API key
"""

import os
import sys

def setup_api_key():
    """Help user set up their Gemini API key."""
    print("🔑 Gemini API Key Setup")
    print("=" * 40)

    current_key = os.environ.get("GEMINI_API_KEY", "")
    if current_key and not current_key.startswith("REPLACE_WITH"):
        print(f"Current API key: {current_key[:20]}...")
        change = input("Do you want to change it? (y/n): ").strip().lower()
        if change != 'y':
            print("Keeping current API key.")
            return

    print("\n📋 To get a Gemini API key:")
    print("1. Go to: https://makersuite.google.com/app/apikey")
    print("2. Sign in with your Google account")
    print("3. Click 'Create API key'")
    print("4. Copy the API key")
    print()

    api_key = input("Enter your Gemini API key: ").strip()

    if not api_key:
        print("❌ No API key provided.")
        return False

    if len(api_key) < 20:
        print("❌ API key seems too short. Please check and try again.")
        return False

    # Set environment variable
    os.environ["GEMINI_API_KEY"] = api_key

    # Test the key
    print("\n🧪 Testing API key...")
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content("Say 'API key works' in 3 words.")
        if response and response.text:
            print("✅ API key is valid!")
            print(f"Response: {response.text.strip()}")

            # Save to .env file for persistence
            try:
                with open(".env", "w") as f:
                    f.write(f"GEMINI_API_KEY={api_key}\n")
                print("💾 API key saved to .env file")
                print("   The key will be loaded automatically when you run the APIs")
            except Exception as e:
                print(f"⚠️ Could not save to .env file: {e}")
                print("   You can manually add it to the .env file")

            return True
        else:
            print("❌ API key test failed - no response")
            return False

    except Exception as e:
        print(f"❌ API key test failed: {str(e)}")
        return False

if __name__ == "__main__":
    success = setup_api_key()
    if success:
        print("\n🎉 Setup complete! You can now run the APIs:")
        print("  Essay API: python fastapi_app.py")
        print("  Quiz API:  python quiz_fastapi_app.py")
    else:
        print("\n❌ Setup failed. Please try again.")
        sys.exit(1)