"""
Food Planner - Rule-Based Engine (No AI)
Uses 'thefuzz' for fuzzy matching deals against shopping lists and meal templates.
Zero latency, deterministic, and free.
"""
import os
import re
import json
import time
import sys
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

# FUZZY LOGIC LIBRARIES
from thefuzz import fuzz, process

# Rate limiting for Sheets only
from api_utils import sheets_rate_limiter, logger, log_stage, progress_saver

# --- CONFIGURATION ---
load_dotenv()
USE_MOCK_DATA = True
MATCH_THRESHOLD = 80
FODMAP_SAFE_FILTER = True

# FODMAP LISTS
FODMAP_SAFE = ["potatoes", "kartofler", "rice", "ris", "carrots", "gulerødder", "zucchini", "squash", "oats", "havregryn", "chicken", "kylling", "fish", "fisk", "egg", "æg"]
FODMAP_HIGH = ["onion", "garlic", "løg", "hvidløg", "wheat", "hvede", "beans", "bønner", "milk", "mælk", "apple", "æble", "bread", "brød", "rugbrød"]

# --- GOOGLE SHEETS CONFIG ---
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SPREADSHEET_NAME = "Food Planner"

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
    "Lidl": "https://etilbudsavis.dk/Lidl",
}

# ============================================================
#  DATA STRUCTURES & PARSING
# ============================================================

def clean_currency(price_str):
    """
    Converts string prices like "12,95 kr" or "15.-" to float.
    Returns None if parsing fails.
    """
    if not price_str: return None
    
    # Normalize
    s = price_str.lower().replace("kr.", "").replace("kr", "").replace("dkk", "").replace(".-", "").strip()
    s = s.replace(",", ".") # Decimal comma to dot
    
    try:
        return float(s)
    except ValueError:
        return None

def parse_scraped_text(raw_text, store_name):
    """
    Parses raw scraped text into a list of structured deal objects.
    Pattern Agnostic: Looks for lines containing both digits and currency markers.
    """
    structured_deals = []
    lines = raw_text.split('\n')
    
    # 1. Broad filter: Must contain currency symbol AND digit
    # Symbols: 'kr', 'dkk', '.-', ',00', '.00'
    currency_markers = r"(?:kr|dkk|\.-|[,.]00)"
    digit_marker = r"\d"
    
    # 2. Extraction Regex: Supports "10 kr" AND "DKK 10"
    # Group 1: Prefix match (DKK 10) -> returns '10'
    # Group 2: Suffix match (10 kr) -> returns '10'
    extract_pattern = re.compile(
        r"(?:kr\.?|dkk)\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*(?:kr\.?|dkk|\.-|[,.]00)", 
        re.IGNORECASE
    )
    
    for line in lines:
        line = line.strip()
        if len(line) < 3: continue
        
        # Check broad filter first for speed/accuracy
        if not (re.search(currency_markers, line, re.IGNORECASE) and re.search(digit_marker, line)):
            continue
            
        match = extract_pattern.search(line)
        if match:
            # Price could be in group 1 (DKK 10) or group 2 (10 DKK)
            price_str = match.group(1) if match.group(1) else match.group(2)
            
            # Edge case: "12.00 kr. 500g" -> extracts "12.00"
            price = clean_currency(price_str)
            
            if price is not None:
                # Remove price from string to get item name
                item_name = extract_pattern.sub("", line).strip()
                # Clean up common noise
                item_name = re.sub(r"\d+g|\d+kg|\d+l|stk\.|per\s*stk", "", item_name, flags=re.IGNORECASE).strip()
                item_name = re.sub(r"[^\w\sæøåÆØÅ.\-]", "", item_name).strip() 
                
                # Check for "spar" (savings) lines which might be just discounts, not prices?
                # E.g. "Spar 10 kr" -> Price 10? No.
                # If line contains "spar", it might be a discount. BUT sometimes "Pris 10 kr. Spar 5 kr".
                # We'll take the first match for now.
                
                if len(item_name) > 2 and "spar" not in item_name.lower():
                    structured_deals.append({
                        "item": item_name,
                        "price": price,
                        "store": store_name,
                        "raw": line
                    })
                    
    return structured_deals

# ============================================================
#  THE MATCHING ENGINE (FUZZY LOGIC)
# ============================================================

def find_cheapest_deal(item_name, all_deals, threshold=MATCH_THRESHOLD):
    """
    Finds the best deal for a specific item using fuzzy matching.
    Returns: Best Deal Object or None
    """
    best_deal = None
    best_score = 0
    
    # Pre-filter deals? No, iterate all for simplicity or use process.extractOne
    # process.extractOne is good but we need custom logic for price minimization
    
    for deal in all_deals:
        # 1. Calculate similarity
        score = fuzz.partial_ratio(item_name.lower(), deal['item'].lower())
        
        # Boost if exact word match
        if f" {item_name.lower()} " in f" {deal['item'].lower()} ":
             score += 10
             
        if score >= threshold:
            # Found a candidate
            if score > best_score:
                best_score = score
                best_deal = deal
            elif score == best_score:
                # Tie-breaker: Price
                if deal['price'] < best_deal['price']:
                    best_deal = deal
                    
    return best_deal

# ============================================================
#  MEAL PLANNING LOGIC
# ============================================================

def load_meal_templates():
    try:
        with open("meal_templates.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Could not load meal_templates.json: {e}")
        return []

def generate_weekly_plan(templates, pantry, all_deals):
    """
    Selects 2 primary meals for batch cooking (Mon/Wed) based on FODMAP & Availability.
    Schedule: Mon (Cook A x3), Tue (Leftover A), Wed (Cook B x2), Thu (Leftover B), Fri (Leftover A).
    """
    scored_meals = []
    
    # 1. Score all templates
    for meal in templates:
        ingredients = meal['ingredients']
        found_count = 0
        fodmap_score = 0
        
        # Check ingredients against pantry + deals + FODMAP
        for ing in ingredients:
            ing_lower = ing.lower()
            
            # Availability Check
            is_available = False
            if any(fuzz.partial_ratio(ing_lower, p.lower()) > 85 for p in pantry):
                is_available = True
            elif find_cheapest_deal(ing, all_deals):
                is_available = True
            
            if is_available: found_count += 1
            
            # FODMAP Scoring
            if FODMAP_SAFE_FILTER:
                # Check for High FODMAP
                if any(bad in ing_lower for bad in FODMAP_HIGH):
                    fodmap_score -= 50
                # Check for Safe FODMAP
                if any(good in ing_lower for good in FODMAP_SAFE):
                    fodmap_score += 20
        
        base_score = (found_count / len(ingredients)) * 100
        total_score = base_score + fodmap_score
        
        scored_meals.append({
            "meal": meal['name'],
            "score": total_score,
            "ingredients": ingredients
        })
        
    # 2. Select Top 2 Unique
    # Sort descending
    scored_meals.sort(key=lambda x: x['score'], reverse=True)
    
    if len(scored_meals) < 2:
        logger.warning("Not enough meals passed filters! Using placeholders.")
        selected = scored_meals + [{"meal": "Emergency Omelet", "ingredients": ["Eggs", "Spinach"], "score": 0}] * (2 - len(scored_meals))
    else:
        selected = scored_meals[:2]
        
    meal_A = selected[0]
    meal_B = selected[1]
    
    # 3. Build Schedule
    schedule = [
        {"day_name": "Monday", "type": "cook", "meal_name": meal_A['meal'], "portions": 3, "ingredients": meal_A['ingredients']},
        {"day_name": "Tuesday", "type": "leftover", "meal_name": f"{meal_A['meal']} (Leftovers)", "portions": 0},
        {"day_name": "Wednesday", "type": "cook", "meal_name": meal_B['meal'], "portions": 2, "ingredients": meal_B['ingredients']},
        {"day_name": "Thursday", "type": "leftover", "meal_name": f"{meal_B['meal']} (Leftovers)", "portions": 0},
        {"day_name": "Friday", "type": "leftover", "meal_name": f"{meal_A['meal']} (Leftovers)", "portions": 0},
        {"day_name": "Saturday", "type": "flexible", "meal_name": "FODMAP Safe Pantry / Flexible", "portions": 0},
        {"day_name": "Sunday", "type": "flexible", "meal_name": "Sunday Roast / Flexible", "portions": 0},
    ]
    
    return schedule

def generate_shopping_list(buying_list, schedule, all_deals):
    """
    Generates Grouped Shopping List with Multipliers.
    Needs = Buying List (x1) + Schedule Ingredients * Portions.
    """
    shopping_needs = {} # Item -> Multiplier
    
    # 1. Add Schedule Ingredients
    for day in schedule:
        if day['type'] == 'cook':
            mult = day['portions']
            for ing in day.get('ingredients', []):
                shopping_needs[ing] = shopping_needs.get(ing, 0) + mult
                
    # 2. Add Buying List
    for item in buying_list:
        shopping_needs[item] = shopping_needs.get(item, 0) + 1
        
    # 3. Find Deals & Group
    grouped_list = {} # Store -> [Items]
    final_list_flat = []
    total_savings = 0.0
    
    for item, mult in shopping_needs.items():
        deal = find_cheapest_deal(item, all_deals)
        
        entry = {
            "name": item,
            "multiplier": mult,
            "is_deal": False,
            "price": 0.0,
            "found_name": None,
            "store": "Unknown"
        }
        
        if deal:
            entry["is_deal"] = True
            entry["price"] = deal['price'] * mult
            entry["found_name"] = deal['item']
            entry["store"] = deal['store']
            # Assume saving is roughly 20% compared to non-deal? Or just track deal value
            total_savings += (deal['price'] * 0.2) * mult # Est saving
        else:
            entry["store"] = "General/Other"
            
        # Add to flat list for Sheets
        final_list_flat.append(entry)
        
        # Add to Grouped for Email
        store = entry["store"]
        if store not in grouped_list: grouped_list[store] = []
        grouped_list[store].append(entry)
        
    return grouped_list, final_list_flat, total_savings

# ============================================================
#  SHEETS & EMAIL
# ============================================================

def get_sheets_client():
    sheets_rate_limiter.wait()
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    return gspread.authorize(creds)

def save_to_sheets(schedule, shopping_list):
    gc = get_sheets_client()
    sh = gc.open(SPREADSHEET_NAME)
    
    # 1. Meal Plan
    try: ws = sh.worksheet("MealPlan")
    except: ws = sh.add_worksheet("MealPlan", 100, 10)
    ws.clear()
    
    headers = ["Day", "Meal", "Ingredients"]
    rows = [[m['day_name'], m['meal_name'], ", ".join(m.get('ingredients', []))] for m in schedule]
    ws.update(range_name="A1", values=[headers] + rows)
    
    # 2. Shopping List
    try: ws_shop = sh.worksheet("ShoppingList")
    except: ws_shop = sh.add_worksheet("ShoppingList", 100, 10)
    ws_shop.clear()
    
    headers_shop = ["Item", "Qty", "Price", "Store", "Found Match"]
    rows_shop = []
    for item in shopping_list:
        price = f"{item['price']:.2f}" if item['price'] else "-"
        match = item['found_name'] if item['found_name'] else "-"
        # Qty is multiplier
        qty = f"x{item['multiplier']}"
        rows_shop.append([item['name'], qty, price, item['store'], match])
        
    ws_shop.update(range_name="A1", values=[headers_shop] + rows_shop)
    logger.info("✅ Saved to Google Sheets")

def send_email_notification(schedule, shopping_list_grouped, total_savings):
    if not EMAIL_ADDRESS: return
    
    env = Environment(loader=FileSystemLoader('.'))
    template = env.get_template('templates/email_fodmap_batch.html')
    
    html_content = template.render(
        schedule=schedule,
        shopping_list_by_store=shopping_list_grouped,
        total_savings=total_savings,
        today_date=datetime.now().strftime("%Y-%m-%d")
    )
    
    msg = EmailMessage()
    msg["Subject"] = f"Ugens Batch-Madplan (2x Weekly) - {datetime.now().strftime('%d/%m')}"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = EMAIL_RECEIVER
    msg.set_content("Please enable HTML to view this customized meal plan.", subtype="plain")
    msg.add_alternative(html_content, subtype="html")
    
    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)
    logger.info("📧 Email sent successfully.")

# ============================================================
#  MAIN PIPELINE
# ============================================================

def scrape_deals_raw(store, url):
    """Playwright Scraper (Raw Text)"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        try: 
            page.wait_for_load_state("networkidle", timeout=10000)
            
            # Simple Cookie Clicker
            try:
                # Look for common cookie buttons
                # Using broad regex for Danish/English consent buttons
                btn = page.locator("button, a").filter(has_text=re.compile(r"accepter|tillad|ok|godkend|yes|ja|luk|accept", re.IGNORECASE)).first
                if btn.count() > 0:
                    btn.click(timeout=1000)
                    page.wait_for_timeout(1000) # Wait for overlay to fade
            except:
                pass
                
        except: 
            pass
        
        text = page.inner_text("body")
        browser.close()
        return text

def load_lists_from_sheets():
    gc = get_sheets_client()
    sh = gc.open(SPREADSHEET_NAME)
    buy = [i.strip() for i in sh.worksheet("BuyingList").col_values(1)[1:] if i.strip()]
    pantry = [i.strip() for i in sh.worksheet("PantryList").col_values(1)[1:] if i.strip()]
    
    if not buy and USE_MOCK_DATA:
        logger.warning(f"⚠️ MOCK DATA ACTIVE: Injecting fake buying items because list was empty.")
        buy = ["Mælk", "Kylling", "Pasta", "Oksekød", "Rugbrød"]
        
    return buy, pantry

def main():
    logger.info("="*60)
    logger.info("  FOOD PLANNER: BATCH ENGINE (FODMAP SAFE)")
    logger.info("="*60)
    
    try:
        # 1. Load Data
        log_stage("1. LOADING TEMPLATES & LISTS")
        templates = load_meal_templates()
        buying, pantry = load_lists_from_sheets()
        logger.info(f"Templates: {len(templates)} | Buying: {len(buying)} | Pantry: {len(pantry)}")
        
        # 2. Scrape & Parse
        log_stage("2. SCRAPING STORES")
        all_deals = []
        for store, url in STORES.items():
            logger.info(f"Scraping {store}...")
            try:
                raw_text = scrape_deals_raw(store, url)
                structured = parse_scraped_text(raw_text, store)
                
                if not structured:
                    logger.warning(f"  ⚠️ No deals found for {store} using Pattern Agnostic Parser.")
                    snippet = raw_text[:500].replace('\n', ' ')
                    logger.warning(f"  📜 RAW HTML SAMPLE: {snippet}...")
                
                all_deals.extend(structured)
                logger.info(f"  -> Found {len(structured)} deals")
            except Exception as e:
                logger.error(f"Failed to scrape {store}: {e}")
        
        # 3. Matching Engine
        log_stage("3. MATCHING ENGINE (FODMAP & BATCH)")
        schedule = generate_weekly_plan(templates, pantry, all_deals)
        grouped_list, flat_list, total_savings = generate_shopping_list(buying, schedule, all_deals)
        
        logger.info(f"Generated 2x Weekly Schedule.")
        logger.info(f"Shopping List: {len(flat_list)} items (Est. Savings: {total_savings:.2f} kr)")
        
        # 4. Save
        log_stage("4. SAVING RESULTS")
        save_to_sheets(schedule, flat_list)
        
        # 5. Email
        log_stage("5. SENDING NOTIFICATION")
        send_email_notification(schedule, grouped_list, total_savings)
        
        logger.info("✅ PIPELINE COMPLETE")

    except Exception as e:
        logger.critical(f"FATAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
