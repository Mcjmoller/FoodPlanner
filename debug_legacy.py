
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

keys_str = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY")
key = [k.strip() for k in keys_str.split(',') if k.strip()][0]

print(f"Testing Legacy SDK with key: ...{key[-4:]}")

genai.configure(api_key=key)

print("\n--- Listing Models (Legacy) ---")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)

print("\n--- Generating (Legacy) ---")
try:
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content("ping")
    print(f"✅ Success: {response.text}")
except Exception as e:
    print(f"❌ Failed: {e}")
