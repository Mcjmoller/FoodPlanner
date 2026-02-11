
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
key = os.environ.get("GEMINI_API_KEYS").split(',')[0].strip()
genai.configure(api_key=key)

print("Searching for valid model...")
valid_model = None
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        if 'flash' in m.name:
            valid_model = m.name
            break

if valid_model:
    print(f"Found: {valid_model}")
    try:
        model = genai.GenerativeModel(valid_model)
        res = model.generate_content("ping")
        print(f"✅ Success with {valid_model}")
    except Exception as e:
        print(f"❌ Failed with {valid_model}: {e}")
else:
    print("No flash model found.")
