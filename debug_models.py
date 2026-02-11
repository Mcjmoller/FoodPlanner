
import os
import sys
from google import genai
from dotenv import load_dotenv

load_dotenv()

keys_str = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY")
if not keys_str:
    print("❌ No keys found in .env")
    sys.exit(1)

keys = [k.strip() for k in keys_str.split(',') if k.strip()]
print(f"Found {len(keys)} keys.")

key = keys[0]
print(f"Testing with first key: ...{key[-4:]}")

client = genai.Client(api_key=key)

print("\n--- 1. Listing Models ---")
try:
    # Try the standard list method for valid models
    # Note: method might be client.models.list() or similar
    if hasattr(client, 'models') and hasattr(client.models, 'list'):
        pager = client.models.list()
        print("Models found:")
        for m in pager:
            print(f" - {m.name}")
    else:
        print("client.models.list() not found.")
except Exception as e:
    print(f"List models failed: {e}")

print("\n--- 2. Testing Generation ---")
models_to_test = ["gemini-1.5-flash", "models/gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest"]

for model in models_to_test:
    print(f"\nTrying {model}...")
    try:
        response = client.models.generate_content(
            model=model,
            contents="ping"
        )
        print(f"✅ Success! Response: {response.text}")
    except Exception as e:
        print(f"❌ Failed: {type(e).__name__} - {e}")
