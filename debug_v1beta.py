
import os
import sys
from google import genai
from dotenv import load_dotenv

load_dotenv()

keys_str = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY")
keys = [k.strip() for k in keys_str.split(',') if k.strip()]
key = keys[0]
print(f"Testing Key: ...{key[-4:]}")

# Try connecting explicitly to v1beta
print("\n--- Testing v1beta explicit ---")
try:
    client = genai.Client(api_key=key, http_options={'api_version': 'v1beta'})
    
    # Try ping
    response = client.models.generate_content(
        model="gemini-1.5-flash", 
        contents="ping"
    )
    print(f"✅ [gemini-1.5-flash] Success: {response.text}")
except Exception as e:
    print(f"❌ [gemini-1.5-flash] Failed: {e}")

try:
    response = client.models.generate_content(
        model="models/gemini-1.5-flash", 
        contents="ping"
    )
    print(f"✅ [models/gemini-1.5-flash] Success: {response.text}")
except Exception as e:
    print(f"❌ [models/gemini-1.5-flash] Failed: {e}")
