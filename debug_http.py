
import os
import requests
from dotenv import load_dotenv

load_dotenv()

keys_str = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY")
key = [k.strip() for k in keys_str.split(',') if k.strip()][0]

print(f"Checking raw API with key: ...{key[-4:]}")

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"

try:
    response = requests.get(url)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Models found: {len(data.get('models', []))}")
        for m in data.get('models', [])[:5]:
            print(f" - {m['name']}")
    else:
        print(f"Error: {response.text}")
except Exception as e:
    print(f"Request failed: {e}")
