import os
import time
from playwright.sync_api import sync_playwright
from google import genai
from dotenv import load_dotenv
import smtplib
import ssl
from email.message import EmailMessage
import markdown
import json

from api_utils import retry_on_rate_limit, response_cache, gemini_rate_limiter

# --- CONFIGURATION ---
load_dotenv()

# GEMINI CONFIG
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set. Please create a .env file and add your API key.")

# EMAIL CONFIG
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD").replace(" ", "") if os.environ.get("EMAIL_PASSWORD") else None
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

# Configure the Gemini API Client
client = genai.Client(api_key=GEMINI_API_KEY)

STORES = {
    "REMA 1000": "https://etilbudsavis.dk/REMA-1000",
    "Netto": "https://etilbudsavis.dk/Netto",
    "365 Discount": "https://365discount.coop.dk/365avis/",
    "Lidl": "https://etilbudsavis.dk/Lidl"
}


@retry_on_rate_limit(max_retries=5, base_delay=2.0)
def _call_gemini(prompt: str) -> str:
    """Make a rate-limited call to Gemini API with automatic retry."""
    gemini_rate_limiter.wait()
    response = client.models.generate_content(
        model='gemini-2.0-flash',
        contents=prompt
    )
    return response.text


def scrape_deals(store_name, url):
    print(f"Scraping {store_name} using Playwright...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url)
            
            # Wait for content to load - dynamic wait
            try:
                # Wait up to 10 seconds for standard offer elements or body
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass # Continue even if networkidle times out
            
            # Extract text from the page body
            text = page.inner_text("body")
            
            browser.close()
            
            # Simple line splitting and deduplication
            lines = [line.strip() for line in text.split('\n') if len(line.strip()) > 3 and len(line.strip()) < 100]
            unique_lines = list(set(lines))
            
            # Return a reasonable subset to avoid token limit
            return "\n".join(unique_lines[:100])

    except Exception as e:
        return f"Error scraping {store_name}: {e}"

def load_personal_lists():
    buying_list = []
    pantry_list = []
    current_list = None
    
    file_path = "foodplaner_list.txt"
    if not os.path.exists(file_path):
        # Create a template if it doesn't exist
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# === ITEMS TO BUY ===\n# Add items you want to find deals for below:\n\n\n")
            f.write("# === ITEMS TO IGNORE / ALREADY HAVE ===\n# Add items you want the AI to avoid below:\n\n")
        return [], []

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            # Detect sections
            if "ITEMS TO BUY" in line.upper():
                current_list = "buy"
                continue
            elif "ITEMS TO IGNORE" in line.upper() or "ALREADY HAVE" in line.upper():
                current_list = "pantry"
                continue
            
            if line.startswith("#"):
                continue
                
            if current_list == "buy":
                buying_list.append(line)
            elif current_list == "pantry":
                pantry_list.append(line)
                
    return buying_list, pantry_list

def save_personal_lists(buying_list, pantry_list):
    """Save the buying and pantry lists back to foodplaner_list.txt."""
    file_path = "foodplaner_list.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("# === ITEMS TO BUY ===\n# Add items you want to find deals for below:\n\n")
        for item in buying_list:
            f.write(f"{item}\n")
        f.write("\n# === ITEMS TO IGNORE / ALREADY HAVE ===\n# Add items you want the AI to avoid below:\n\n")
        for item in pantry_list:
            f.write(f"{item}\n")

def generate_meal_plan(all_deals, buying_list, pantry_list):
    buying_list_str = ", ".join(buying_list) if buying_list else "None"
    pantry_list_str = ", ".join(pantry_list) if pantry_list else "None"
    
    # Check cache first
    cache_key = f"meal_plan_{hash(frozenset(buying_list + pantry_list + [all_deals[:500]]))}"
    cached_result = response_cache.get(cache_key)
    if cached_result:
        print("Using cached meal plan...")
        return cached_result
    
    prompt = f"""
    Below is raw text scraped from grocery deal sites:
    {all_deals}
    
    My Personal Buying List (Look for these specifically):
    {buying_list_str}
    
    Inventory / Blacklist (I ALREADY HAVE THESE or DON'T want them - DO NOT SUGGEST BUYING THESE):
    {pantry_list_str}
    
    Task:
    1. Identify only the food-related deals.
    2. Check if ANY items from my 'Personal Buying List' are on sale. If they are, prioritize them.
    3. **Store Selection (CRITICAL):**
        - Analyze the deals from ALL stores.
        - Select exactly **TWO (2)** stores that offer the best value for this specific week/list.
        - **STRICTLY LIMIT** the final 'Shopping List' to ONLY these two selected stores. Do not list items from other stores.
        - At the very top of the output, write: "**Udvalgte Butikker:** [Store A] & [Store B]" and a brief one-line reason why in Danish.
    4. **Meal Plan Logic:**
        - Create a 2-person, Low FODMAP meal plan (No garlic, onion, or wheat).
        - **Main Dish:** The core meal MUST be vegetarian/plant-based.
        - **Meat/Protein:** Meat or fish should ONLY be suggested as an *optional side* that is easy to prepare/add (e.g., "Valgfrit: Tilføj stegt kylling bryst"). It must not be the base of the dish.
        - Plan only 2 major cooking sessions for the entire week. The rest of the days rely on leftovers.
    5. **Inventory:**
        - PROHIBITION: Do NOT suggest buying items listed in 'Inventory / Blacklist'.
        - If an item from 'Personal Buying List' is NOT on sale, add it to the 'Shopping List' under one of the two selected stores.
    6. **Formatting & Language (CRITICAL):**
        - **LANGUAGE:** The ENTIRE output must be in **DANISH**.
        - **Shopping List Format:** You MUST use Markdown checkboxes for every item: `- [ ] Varenavn (Butik)`.
        - Group items by the TWO selected stores.
        - **Structure:**
            - **Header:** "Udvalgte Butikker"
            - **Section 1:** "Madplan" (The meal plan with days)
            - **Section 2:** "Indkøbsliste" (Split into 2 clear subsections for the 2 stores).
    """
    
    print("Generating your meal plan...")
    try:
        result = _call_gemini(prompt)
        # Cache the result
        response_cache.set(cache_key, result)
        return result
    except Exception as e:
        return f"Model Error: {e}"

def send_email(content):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("\n[INFO] Email credentials missing in .env. Skipping email.")
        return

    # Handle multiple recipients
    recipients = [r.strip() for r in EMAIL_RECEIVER.split(",")]

    # Convert Markdown to HTML
    html_content = markdown.markdown(content, extensions=['tables', 'nl2br'])

    # Convert checkboxes [ ] to visual styled checkboxes
    html_content = html_content.replace("- [ ]", "☐") # Placeholder for simple text, or use CSS list

    # Premium CSS Styling (Spiced up layout)
    css_style = """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');
        body { 
            font-family: 'Outfit', sans-serif; 
            line-height: 1.6; 
            color: #334155; 
            background-color: #f8fafc; 
            margin: 0; 
            padding: 20px;
        }
        .container { 
            max-width: 600px; 
            margin: 0 auto; 
            background: #ffffff; 
            border-radius: 20px; 
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); 
        }
        .header {
            background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
            padding: 40px 20px;
            text-align: center;
            color: white;
        }
        .header h1 { 
            margin: 0; 
            font-size: 28px; 
            font-weight: 800; 
            letter-spacing: -0.5px;
        }
        .header .subtitle {
            font-size: 14px;
            opacity: 0.9;
            margin-top: 8px;
            font-weight: 400;
        }
        .content {
            padding: 30px;
        }
        h2 { 
            color: #0f172a; 
            font-size: 20px; 
            border-bottom: 2px solid #e2e8f0; 
            padding-bottom: 10px; 
            margin-top: 30px; 
            margin-bottom: 20px;
        }
        h3 {
            color: #0ea5e9;
            font-size: 16px;
            margin-top: 20px;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        /* Shopping List Cards */
        ul { 
            list-style: none; 
            padding: 0; 
            margin: 0; 
        }
        li { 
            background: #ffffff;
            border: 1px solid #e2e8f0;
            padding: 12px 16px; 
            margin-bottom: 8px; 
            border-radius: 8px; 
            display: flex;
            align-items: center;
            font-size: 14px;
            transition: all 0.2s;
        }
        li:hover {
            border-color: #cbd5e1;
            background: #f8fafc;
        }
        /* Bullet point replacement for checklist vibe */
        li::before {
            content: "⬜"; 
            margin-right: 12px;
            font-size: 16px;
        }
        
        .footer { 
            background: #f1f5f9;
            padding: 20px;
            text-align: center;
            font-size: 12px;
            color: #94a3b8;
        }
        a { color: #0ea5e9; text-decoration: none; }
    </style>
    """

    email_body = f"""
    <html>
    <head>{css_style}</head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Din Madplan 🥗</h1>
                <div class="subtitle">Frisk fra tilbudsavisen</div>
            </div>
            <div class="content">
                {html_content}
            </div>
            <div class="footer">
                Genereret af FoodPlaner AI 🤖<br>
                God fornøjelse i køkkenet!
            </div>
        </div>
    </body>
    </html>
    """
    
    for recipient in recipients:
        print(f"\nSending styled email to {recipient}...")
        try:
            msg = EmailMessage()
            msg["Subject"] = "Ugens Madplan & Indkøb 🛒"
            msg["From"] = EMAIL_ADDRESS
            msg["To"] = recipient
            msg.set_content(content)
            msg.add_alternative(email_body, subtype='html')

            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.send_message(msg)
            print(f"Email sent successfully to {recipient}!")
        except Exception as e:
            print(f"Failed to send email to {recipient}: {e}")

if __name__ == "__main__":
    all_store_data = ""
    for store, link in STORES.items():
        data = scrape_deals(store, link)
        if not data or len(data) < 10:
            data = "No data found (Site might require JavaScript scraping)."
        all_store_data += f"\n--- {store} ---\n{data}\n"

    buying_list, pantry_list = load_personal_lists()
    final_note = generate_meal_plan(all_store_data, buying_list, pantry_list)
    
    print("\n--- YOUR WEEKLY NOTE ---\n")
    print(final_note)

    with open("weekly_meal_plan.txt", "w", encoding="utf-8") as f:
        f.write(final_note)
    print("\nSuccess! Note saved to weekly_meal_plan.txt")

    # Send Email
    send_email(final_note)


def run_full_pipeline(progress_callback=None):
    """
    Run the full food planning pipeline.
    progress_callback: optional function(message: str) to report progress
    Returns: the generated meal plan text
    """
    def report(msg):
        if progress_callback:
            progress_callback(msg)
        print(msg)
    
    all_store_data = ""
    for store, link in STORES.items():
        report(f"Scraping {store}...")
        data = scrape_deals(store, link)
        if not data or len(data) < 10:
            data = "No data found (Site might require JavaScript scraping)."
        all_store_data += f"\n--- {store} ---\n{data}\n"

    report("Loading personal lists...")
    buying_list, pantry_list = load_personal_lists()
    
    report("Generating meal plan with AI...")
    final_note = generate_meal_plan(all_store_data, buying_list, pantry_list)
    
    report("Saving meal plan...")
    with open("weekly_meal_plan.txt", "w", encoding="utf-8") as f:
        f.write(final_note)
    
    report("Sending email...")
    send_email(final_note)
    
    report("Done!")
    return final_note

