"""
Food Planner - Cloud Version
Uses Google Sheets as database instead of local files.
Designed to run on GitHub Actions.
"""
import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from google import genai
import smtplib
import ssl
from email.message import EmailMessage
import markdown
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv()

# --- GOOGLE SHEETS CONFIG ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SPREADSHEET_NAME = 'Food Planner'

# --- GEMINI CONFIG ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

client = genai.Client(api_key=GEMINI_API_KEY)

# --- EMAIL CONFIG ---
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "").replace(" ", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

# --- STORES ---
STORES = {
    "REMA 1000": "https://etilbudsavis.dk/REMA-1000",
    "Netto": "https://etilbudsavis.dk/Netto",
    "365 Discount": "https://365discount.coop.dk/365avis/",
    "Lidl": "https://etilbudsavis.dk/Lidl"
}


def get_sheets_client():
    """Authenticate with Google Sheets using service account."""
    creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    return gspread.authorize(creds)


def load_lists_from_sheets():
    """Load buying and pantry lists from Google Sheets."""
    gc = get_sheets_client()
    spreadsheet = gc.open(SPREADSHEET_NAME)
    
    # Get all values from column A, skip header row
    buying_sheet = spreadsheet.worksheet('BuyingList')
    pantry_sheet = spreadsheet.worksheet('PantryList')
    
    buying_list = [item.strip() for item in buying_sheet.col_values(1)[1:] if item.strip()]
    pantry_list = [item.strip() for item in pantry_sheet.col_values(1)[1:] if item.strip()]
    
    return buying_list, pantry_list


def save_plan_to_sheets(plan_text):
    """Save the generated meal plan to Google Sheets."""
    gc = get_sheets_client()
    spreadsheet = gc.open(SPREADSHEET_NAME)
    plan_sheet = spreadsheet.worksheet('MealPlan')
    
    # Clear previous content
    plan_sheet.clear()
    
    # Prepare data for batch update
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    data = [['Meal Plan', 'Generated']]  # Header row
    
    lines = plan_text.split('\n')
    
    # Add first line with timestamp in column B
    if lines:
        cleaned_first = lines[0].replace('**', '').replace('##', '').strip()
        data.append([cleaned_first, timestamp])
        remaining_lines = lines[1:150]
    else:
        data.append(["", timestamp])
        remaining_lines = []
    
    for line in remaining_lines:
        line = line.strip()
        if not line:
            continue
            
        # Clean Markdown bold/headers
        cleaned_line = line.replace('**', '').replace('##', '').strip()
        
        # Check if it's a markdown table row (starts and ends with |)
        if cleaned_line.startswith('|') and cleaned_line.count('|') > 1:
            # Drop the outer pipes if they exist empty
            parts = [p.strip() for p in cleaned_line.strip('|').split('|')]
            
            # Skip separator lines like |---|---|
            if all(set(p) <= {'-', ':', ' '} for p in parts):
                continue
                
            data.append(parts)
        else:
            # Regular text line
            data.append([cleaned_line])
    
    # Perform single batch update to avoid quota limits
    # range_name='A1' auto-expands to fit data size
    plan_sheet.update(range_name='A1', values=data)
    
    # Apply formatting
    setup_sheet_style(spreadsheet)

def setup_sheet_style(spreadsheet):
    """Apply visual formatting to the Google Sheet."""
    # Colors (R, G, B in 0-1 range)
    HEADER_BG = {"red": 0.05, "green": 0.65, "blue": 0.9}  # Nice Blue
    HEADER_TEXT = {"red": 1.0, "green": 1.0, "blue": 1.0}  # White
    
    # Common format for headers
    header_format = {
        "backgroundColor": HEADER_BG,
        "textFormat": {
            "foregroundColor": HEADER_TEXT,
            "bold": True,
            "fontSize": 12
        },
        "horizontalAlignment": "CENTER"
    }
    
    # Format BuyingList & PantryList
    for name in ['BuyingList', 'PantryList']:
        try:
            ws = spreadsheet.worksheet(name)
            # Check if header exists
            first_cell = ws.acell('A1').value
            if not first_cell:
                ws.update('A1', [['Item (Use one per row)']])
            
            # Format header row
            ws.format('A1:Z1', header_format)
            ws.freeze(rows=1)
            # Set column width
            ws.set_basic_filter('A:A')
        except Exception as e:
            print(f"Formatting warning for {name}: {e}")
            
    # Format MealPlan
    try:
        ws = spreadsheet.worksheet('MealPlan')
        ws.format('A1:B1', header_format)
        ws.freeze(rows=1)
        
        # Enable text wrapping for content
        ws.format('A2:A100', {
            "wrapStrategy": "WRAP",
            "verticalAlignment": "TOP",
            "textFormat": {"fontSize": 11}
        })
        
        # Set column widths (A wide, B narrow)
        ws.set_column_width(0, 600)  # Column A (Plan)
        ws.set_column_width(1, 150)  # Column B (Date)
    except:
        pass


def scrape_deals(store_name, url):
    """Scrape deals from a store website."""
    print(f"Scraping {store_name}...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url)
            
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            
            text = page.inner_text("body")
            browser.close()
            
            lines = [line.strip() for line in text.split('\n') if 3 < len(line.strip()) < 100]
            unique_lines = list(set(lines))
            return "\n".join(unique_lines[:100])
    except Exception as e:
        return f"Error scraping {store_name}: {e}"


def generate_meal_plan(all_deals, buying_list, pantry_list):
    """Generate meal plan using Gemini AI."""
    buying_list_str = ", ".join(buying_list) if buying_list else "None"
    pantry_list_str = ", ".join(pantry_list) if pantry_list else "None"
    
    # Fallback logic for empty lists
    if not buying_list and not pantry_list:
        print("DEBUG: lists are empty. Injecting defaults to force AI generation.")
        buying_list = ["Grøntsager", "Pasta", "Brød", "Mælk"]
        user_context = "User has NO specific requests. You MUST create a generic, budget-friendly 2-person vegetarian meal plan based PURELY on the best deals found below."
    else:
        user_context = f"User Wants: {buying_list_str}\nPantry/Ignore: {pantry_list_str}"

    # Re-generate string with injected defaults
    buying_list_str = ", ".join(buying_list)
    
    prompt = f"""
    Context:
    - Scraped Deals: {all_deals[:3000]}... (truncated)
    - {user_context}
    
    Task:
    Create a weekly meal plan and shopping list.
    
    Requirements:
    1. Select 2 best stores from the deals.
    2. Plan 2 cooking sessions (Low FODMAP, Vegetarian).
    3. CRITICAL: NEVER return an empty plan. If user requests are empty, invent a plan based on the best deals.
    
    Output PURE JSON:
    {{
        "selected_stores": ["Store A", "Store B"],
        "meal_plan": [
            {{"day": "Monday", "meal": "Dish Name", "ingredients": "Main ingredients list"}},
            {{"day": "Thursday", "meal": "Dish Name", "ingredients": "Main ingredients list"}}
        ],
        "shopping_list": [
            {{"item": "Milk", "quantity": "1 liter", "category": "Dairy", "store": "Netto"}},
            {{"item": "Pasta", "quantity": "500g", "category": "Grains", "store": "REMA 1000"}}
        ]
    }}
    """
    
    print("Generating structured plan with AI...")
    
    # Retry logic for 429 Rate Limits
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            return response.text
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                wait_time = 60 * (attempt + 1)  # 60s, 120s, 180s
                print(f"WARNING: API Rate Limit hit. Waiting {wait_time} seconds before retry {attempt+1}/{max_retries}...")
                time.sleep(wait_time)
            else:
                # Non-recoverable error
                print(f"ERROR: AI Generation failed: {e}")
                return f'{{"error": "{e}"}}'
    
    return '{"error": "Max retries exceeded"}'


def save_structured_results(json_text):
    """Parse JSON and save to Google Sheets."""
    import json
    
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        print(f"ERROR: JSON Parsing failed. Raw text: {json_text}")
        try:
            cleaned = json_text.replace('```json', '').replace('```', '').strip()
            data = json.loads(cleaned)
        except:
            print("CRITICAL ERROR: Could not parse AI response.")
            return None

    meal_plan = data.get('meal_plan', [])
    shopping_list = data.get('shopping_list', [])
    
    if not meal_plan and not shopping_list:
        print("ERROR: AI returned empty lists! Aborting save to avoid blanking sheet.")
        return None

    gc = get_sheets_client()
    spreadsheet = gc.open(SPREADSHEET_NAME)
    
    # --- Save Meal Plan ---
    mp_headers = ["Day", "Meal Name", "Key Ingredients"]
    mp_rows = [[d.get('day', ''), d.get('meal', ''), d.get('ingredients', '')] for d in meal_plan]
    update_sheet(spreadsheet, 'MealPlan', mp_headers, mp_rows)
    
    # --- Save Shopping List ---
    sl_headers = ["Item", "Quantity", "Category", "Store"]
    sl_rows = [[d.get('item', ''), d.get('quantity', ''), d.get('category', ''), d.get('store', '')] for d in shopping_list]
    update_sheet(spreadsheet, 'ShoppingList', sl_headers, sl_rows)
    
    print("[OK] Saved structured data to Sheets")
    return data  # Return for email formatting


def update_sheet(spreadsheet, tab_name, headers, rows):
    """Helper to clear and write structured data."""
    try:
        ws = spreadsheet.worksheet(tab_name)
    except:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=100, cols=10)
        
    # Clear content but keep header row potential
    ws.clear()
    
    # Prepare batch data
    batch_data = [headers] + rows
    
    # Write all at once
    ws.update(range_name='A1', values=batch_data)
    
    # Format Headers (Blue background, White text, Bold)
    ws.format('A1:Z1', {
        "backgroundColor": {"red": 0.05, "green": 0.65, "blue": 0.9},
        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
        "horizontalAlignment": "CENTER"
    })
    ws.freeze(rows=1)
    
    # Enable text wrapping
    ws.format('A:Z', {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"})


def format_email_from_json(data):
    """Convert JSON data to HTML for email."""
    html = "<h2>Selected Stores</h2>" + ", ".join(data.get('selected_stores', []))
    
    html += "<h2>Meal Plan</h2><ul>"
    for m in data.get('meal_plan', []):
        html += f"<li><b>{m['day']}</b>: {m['meal']} <br><i>({m['ingredients']})</i></li>"
    html += "</ul>"
    
    html += "<h2>Shopping List</h2><ul>"
    current_cat = None
    # Sort by category for email
    sorted_items = sorted(data.get('shopping_list', []), key=lambda x: x['category'])
    
    for item in sorted_items:
        if item['category'] != current_cat:
            html += f"</ul><h3>{item['category']}</h3><ul>"
            current_cat = item['category']
        html += f"<li>[ ] {item['item']} ({item['quantity']}) - {item.get('store', '')}</li>"
    html += "</ul>"
    return html

def send_email(content):
    """Send the meal plan via email."""
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("[INFO] Email credentials missing. Skipping email.")
        return

    recipients = [r.strip() for r in EMAIL_RECEIVER.split(",")]
    # Note: content is already HTML from format_email_from_json
    
    css_style = """
    <style>
        body { font-family: sans-serif; }
    </style>
    """
    
    email_body = f"""
    <html>
    <head>{css_style}</head>
    <body>
        <h1>Din Madplan</h1>
        {content}
        <hr>
        <p>Generated by Food Planner Cloud</p>
    </body>
    </html>
    """
    
    for recipient in recipients:
        print(f"Sending email to {recipient}...")
        try:
            msg = EmailMessage()
            msg["Subject"] = "Ugens Madplan"
            msg["From"] = EMAIL_ADDRESS
            msg["To"] = recipient
            msg.set_content("Please enable HTML.")
            msg.add_alternative(email_body, subtype='html')

            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.send_message(msg)
            print(f"[OK] Email sent to {recipient}")
        except Exception as e:
            print(f"[FAIL] Failed to send email to {recipient}: {e}")

def main():
    """Main pipeline."""
    print("=" * 50)
    print("  Food Planner Cloud (Structured)")
    print("=" * 50)
    
    # Scrape deals
    all_store_data = ""
    for store, link in STORES.items():
        data = scrape_deals(store, link)
        if not data or len(data) < 10:
            data = "No data found."
        all_store_data += f"\n--- {store} ---\n{data}\n"
    
    # Load lists from Google Sheets
    print("Loading lists from Google Sheets...")
    buying_list, pantry_list = load_lists_from_sheets()
    
    # THROTTLING: pause between read and write/generate
    time.sleep(2)
    
    # Generate plan (JSON)
    json_response = generate_meal_plan(all_store_data, buying_list, pantry_list)
    
    # Save to Sheets
    print("\nSaving structured plan to Google Sheets...")
    
    # THROTTLING: pause before heavy write
    time.sleep(2)
    
    data_obj = save_structured_results(json_response)
    
    if data_obj:
        # Send email
        email_html = format_email_from_json(data_obj)
        send_email(email_html)
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
