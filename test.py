import google.generativeai as genai
import os

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# List models
for m in genai.list_models():
    print(m.name)