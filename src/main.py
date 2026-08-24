"""
Food Planner - Hybrid Engine (Rule-Based + Gemini 3 Flash Preview)
Uses 'thefuzz' for fuzzy matching and Gemini 3 Flash via google-genai SDK
for AI-enhanced meal planning and Low-FODMAP optimization.
"""
import os
import re
import json
import time
import sys
import smtplib
import ssl
import sqlite3
import argparse
import math
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from io import StringIO

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv
from jinja2 import Template
from google import genai
from google.genai import types as genai_types

# FUZZY LOGIC LIBRARIES
from thefuzz import fuzz, process

import store

import logging

# --- CLI ARGUMENT PARSING ---
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Food Planner - Automated Meal Planning")
    parser.add_argument(
        "--cron", "--auto",
        action="store_true",
        dest="auto_mode",
        help="Run in headless automation mode (no prompts, log to file)"
    )
    return parser.parse_args(argv)


# Importing this module must NOT consume sys.argv - a test runner passes its own
# flags, and parsing them here made `pytest src/` fail at collection. The command
# line is read only in the __main__ block; CI sets the env var instead.
AUTO_MODE = os.environ.get("FOODPLANNER_AUTOMATED") == "1"

logger = logging.getLogger("FoodPlanner")


def configure_logging(auto_mode):
    """Sets up log handlers. Called from the entry point, never at import time."""
    if auto_mode:
        # Redirect all logs to a timestamped file for automation
        log_filename = f"automation_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.FileHandler(log_filename, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ],
            force=True,
        )
        # Pointer file so the newest run is easy to find without listing the directory
        try:
            with open("automation_log.txt", 'w', encoding='utf-8') as f:
                f.write(f"Latest log: {log_filename}\n")
        except OSError:
            pass
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(message)s',
            datefmt='%H:%M',
            force=True,
        )


# --- DIRECTORIES ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# --- CONFIGURATION ---
load_dotenv(os.path.join(BASE_DIR, ".env"))
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
# README documents this as configurable; it used to be hardcoded, so the .env
# value was silently ignored. The old literal stays as the default.
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Food Planner")

# --- EMAIL CONFIG ---
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "").replace(" ", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

# --- STORES ---
# Most Danish chains publish through etilbudsavis.dk; the Coop chains only put
# their catalogue on their own sites. ALDI and Irma are deliberately absent -
# both left the Danish market in 2023 (REMA 1000 took over most ALDI stores,
# Irma was folded into Coop), so there is nothing left to scrape.
#
# "365 Discount" keeps its original spelling: it is the primary key of the
# cached scrape rows, and renaming it would orphan the existing cache.
# status:
#   "ok"      - scraped successfully
#   "blocked" - the source refuses scraping; do NOT work around it
#
# etilbudsavis.dk (operated by Tjek) began returning a hard refusal:
#   {"code":1102,"name":"NO_ACCESS","message":"You are not allowed to scrape
#    our API. This is a violation of our terms of service ..."}
# That is a licensing matter, not a technical one. Partner API access is
# available on request via hello@tjek.com; until that exists these stay
# blocked and unselectable rather than silently returning zero deals.
BLOCKED_ETILBUDSAVIS = "etilbudsavis.dk afviser scraping (ToS). Kræver API-aftale med Tjek."

STORE_CATALOG = {
    "365 Discount":         {"url": "https://365discount.coop.dk/365avis/",   "owner": "Coop",          "default": True,  "status": "ok"},
    "SuperBrugsen/Kvickly": {"url": "https://kvickly.coop.dk/avis/",          "owner": "Coop",          "default": True,  "status": "ok"},
    "Dagli'Brugsen":        {"url": "https://brugsen.coop.dk/avis/",          "owner": "Coop",          "default": True,  "status": "ok"},
    "REMA 1000":            {"url": "https://etilbudsavis.dk/REMA-1000",      "owner": "REMA 1000",     "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "Netto":                {"url": "https://etilbudsavis.dk/Netto",          "owner": "Salling Group", "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "Føtex":                {"url": "https://etilbudsavis.dk/fotex",          "owner": "Salling Group", "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "Bilka":                {"url": "https://etilbudsavis.dk/Bilka",          "owner": "Salling Group", "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "Lidl":                 {"url": "https://etilbudsavis.dk/Lidl",           "owner": "Lidl",          "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "MENY":                 {"url": "https://etilbudsavis.dk/MENY",           "owner": "Dagrofa",       "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "SPAR":                 {"url": "https://etilbudsavis.dk/SPAR",           "owner": "Dagrofa",       "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "Min Købmand":          {"url": "https://etilbudsavis.dk/Min-Kobmand",    "owner": "Dagrofa",       "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "LET-KØB":              {"url": "https://etilbudsavis.dk/LET-KOB",        "owner": "Dagrofa",       "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
    "Nemlig":               {"url": "https://etilbudsavis.dk/nemlig",         "owner": "Nemlig.com",    "status": "blocked", "note": BLOCKED_ETILBUDSAVIS},
}

AVAILABLE_STORES = [k for k, v in STORE_CATALOG.items() if v.get("status") != "blocked"]
DEFAULT_STORES = [k for k, v in STORE_CATALOG.items() if v.get("default")]

# Backwards-compatible name -> url mapping used by the scheduled CLI run.
STORES = {name: cfg["url"] for name, cfg in STORE_CATALOG.items() if cfg.get("default")}


def resolve_stores(store_names=None):
    """
    Turns a selection into {name: url}, skipping blocked sources. Unknown or
    blocked names are dropped rather than raising, so a stale cookie naming a
    chain we can no longer scrape degrades to the remaining valid picks
    instead of wasting a browser launch on a refusal page.
    """
    if not store_names:
        return dict(STORES)
    chosen = {
        n: STORE_CATALOG[n]["url"]
        for n in store_names
        if n in STORE_CATALOG and STORE_CATALOG[n].get("status") != "blocked"
    }
    return chosen or dict(STORES)

# ============================================================
#  CACHING & DATABASE (SQLite)
# ============================================================
DB_FILE = os.path.join(DATA_DIR, "deals_cache.db")
CACHE_EXPIRY_SECONDS = 86400 * 3 # 3 days

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS store_deals_cache (
            store_name TEXT PRIMARY KEY,
            raw_text TEXT,
            timestamp REAL
        )
    ''')
    conn.commit()
    return conn

def get_cached_raw_text(store_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT raw_text, timestamp FROM store_deals_cache WHERE store_name = ?', (store_name,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        raw_text, timestamp = row
        if time.time() - timestamp < CACHE_EXPIRY_SECONDS:
            return raw_text
    return None

def set_cached_raw_text(store_name, raw_text):
    conn = get_db_connection()
    conn.execute('''
        INSERT OR REPLACE INTO store_deals_cache (store_name, raw_text, timestamp)
        VALUES (?, ?, ?)
    ''', (store_name, raw_text, time.time()))
    conn.commit()
    conn.close()

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
                
                # --- UNIT EXTRACTION ---
                # Default unit size is 1 (e.g. 1 pack, 1 cabbage)
                unit_size = 1.0
                unit_type = "stk"
                
                # Regex for "10 stk", "500g", "1.5 kg", "1 l"
                # We prioritize "stk" (count) for eggs/buns, "g/kg" for meat/veg
                unit_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(stk|g|kg|l|ml|cl)", item_name, re.IGNORECASE)
                if unit_match:
                    raw_qty = float(unit_match.group(1).replace(",", "."))
                    raw_unit = unit_match.group(2).lower()
                    
                    if raw_unit == "kg":
                        unit_size = raw_qty * 1000
                        unit_type = "g"
                    elif raw_unit == "l":
                        unit_size = raw_qty * 1000  # Treat liter as gram approx
                        unit_type = "ml" # or use g for simplicity
                    elif raw_unit == "stk":
                        unit_size = raw_qty
                        unit_type = "stk"
                    else:
                        unit_size = raw_qty
                        unit_type = raw_unit
                        
                # Clean up common noise AFTER extraction
                item_name = re.sub(r"\d+(?:[.,]\d+)?\s*(?:g|kg|l|stk|ml)\.?", "", item_name, flags=re.IGNORECASE).strip()
                item_name = re.sub(r"per\s*stk\.?", "", item_name, flags=re.IGNORECASE).strip()
                item_name = re.sub(r"[^\w\sæøåÆØÅ.\-]", "", item_name).strip() 
                
                if len(item_name) > 2 and "spar" not in item_name.lower():
                    structured_deals.append({
                        "item": item_name,
                        "price": price,
                        "store": store_name,
                        "unit_size": unit_size, # e.g. 500.0 or 10.0
                        "unit_type": unit_type, # e.g. 'g' or 'stk'
                        "raw": line
                    })
                    
    return structured_deals

# BASE PORTION RULES (Per Person, Per Meal)
# "amount" = how much 1 person needs for 1 meal
# "unit" = the measurement unit
# "pack_item" = True means 1 purchase covers the whole recipe regardless of portions
#               (e.g. you buy 1 pack of tomatoes, 1 jar of curry paste, etc.)
BASE_PORTION_RULES = {
    # COUNTABLE (scale with portions)
    "æg":               {"amount": 2, "unit": "stk", "pack_item": False},
    
    # MEAT (scale by weight)
    "kylling":          {"amount": 150, "unit": "g", "pack_item": False},
    "hel kylling":      {"amount": 1, "unit": "stk", "pack_item": True},
    "oksekød":          {"amount": 125, "unit": "g", "pack_item": False},
    "hakket oksekød":   {"amount": 125, "unit": "g", "pack_item": False},
    "hakket svinekød":  {"amount": 125, "unit": "g", "pack_item": False},
    "svinemørbrad":     {"amount": 1, "unit": "stk", "pack_item": True},
    "svinekoteletter":  {"amount": 1, "unit": "stk", "pack_item": True},
    "flæsk i skiver":   {"amount": 1, "unit": "stk", "pack_item": True},
    "bacon":            {"amount": 1, "unit": "stk", "pack_item": True},
    "skinke":           {"amount": 1, "unit": "stk", "pack_item": True},
    "pølser":           {"amount": 1, "unit": "stk", "pack_item": True},
    
    # FISH
    "laks":             {"amount": 125, "unit": "g", "pack_item": False},
    "torsk":            {"amount": 125, "unit": "g", "pack_item": False},
    "fiskefars":        {"amount": 1, "unit": "stk", "pack_item": True},
    "rejer":            {"amount": 1, "unit": "stk", "pack_item": True},
    
    # STARCHES (scale by weight)
    "pasta":            {"amount": 100, "unit": "g", "pack_item": False},
    "glutenfri pasta":  {"amount": 100, "unit": "g", "pack_item": False},
    "ris":              {"amount": 80,  "unit": "g", "pack_item": False},
    "risnudler":        {"amount": 80,  "unit": "g", "pack_item": False},
    "kartofler":        {"amount": 250, "unit": "g", "pack_item": False},
    "spaghetti":        {"amount": 100, "unit": "g", "pack_item": False},
    "suppehorn":        {"amount": 100, "unit": "g", "pack_item": False},
    
    # DAIRY (1 pack/carton)
    "mælk":             {"amount": 1, "unit": "stk", "pack_item": True},
    "laktosefri mælk":  {"amount": 1, "unit": "stk", "pack_item": True},
    "laktosefri fløde": {"amount": 1, "unit": "stk", "pack_item": True},
    "laktosefri fraiche": {"amount": 1, "unit": "stk", "pack_item": True},
    "kokosmælk":        {"amount": 1, "unit": "stk", "pack_item": True},
    "ost":              {"amount": 1, "unit": "stk", "pack_item": True},
    "cheddar":          {"amount": 1, "unit": "stk", "pack_item": True},
    "parmesan":         {"amount": 1, "unit": "stk", "pack_item": True},
    "smør":             {"amount": 1, "unit": "stk", "pack_item": True},
    
    # PRODUCE (1 pack/bunch covers the recipe)
    "tomater":          {"amount": 1, "unit": "stk", "pack_item": True},
    "hakkede tomater":  {"amount": 1, "unit": "stk", "pack_item": True},
    "tomatpuré":        {"amount": 1, "unit": "stk", "pack_item": True},
    "gulerødder":       {"amount": 1, "unit": "stk", "pack_item": True},
    "peberfrugt":       {"amount": 1, "unit": "stk", "pack_item": True},
    "spinat":           {"amount": 1, "unit": "stk", "pack_item": True},
    "salat":            {"amount": 1, "unit": "stk", "pack_item": True},
    "agurk":            {"amount": 1, "unit": "stk", "pack_item": True},
    "squash":           {"amount": 1, "unit": "stk", "pack_item": True},
    "rødbeder":         {"amount": 1, "unit": "stk", "pack_item": True},
    "avocado":          {"amount": 1, "unit": "stk", "pack_item": True},
    "pastinak":         {"amount": 1, "unit": "stk", "pack_item": True},
    "knoldselleri":     {"amount": 1, "unit": "stk", "pack_item": True},
    "porre (grøn del)": {"amount": 1, "unit": "stk", "pack_item": True},
    "citron":           {"amount": 1, "unit": "stk", "pack_item": True},
    "edamame bønner":   {"amount": 1, "unit": "stk", "pack_item": True},
    
    # SPICES & CONDIMENTS (always 1 pack/jar)
    "karry":            {"amount": 1, "unit": "stk", "pack_item": True},
    "chili":            {"amount": 1, "unit": "stk", "pack_item": True},
    "paprika":          {"amount": 1, "unit": "stk", "pack_item": True},
    "timian":           {"amount": 1, "unit": "stk", "pack_item": True},
    "oregano":          {"amount": 1, "unit": "stk", "pack_item": True},
    "ingefær":          {"amount": 1, "unit": "stk", "pack_item": True},
    "dild":             {"amount": 1, "unit": "stk", "pack_item": True},
    "persille":         {"amount": 1, "unit": "stk", "pack_item": True},
    "purløg":           {"amount": 1, "unit": "stk", "pack_item": True},
    "soja":             {"amount": 1, "unit": "stk", "pack_item": True},
    "eddike":           {"amount": 1, "unit": "stk", "pack_item": True},
    "olivenolie":       {"amount": 1, "unit": "stk", "pack_item": True},
    "maizena":          {"amount": 1, "unit": "stk", "pack_item": True},
    "remoulade":        {"amount": 1, "unit": "stk", "pack_item": True},
    "mayonnaise":       {"amount": 1, "unit": "stk", "pack_item": True},
    
    # OTHER
    "rugbrød":          {"amount": 1, "unit": "stk", "pack_item": True},
    "tacoskaller":      {"amount": 1, "unit": "stk", "pack_item": True},
    "bønner":           {"amount": 1, "unit": "stk", "pack_item": True},
}

def calculate_quantity(ingredient_name, portions):
    """
    Calculates total amount needed.
    - Pack items: Always 1 (one purchase covers the recipe).
    - Scalable items: amount * portions (e.g. 2 eggs * 4 portions = 8 eggs).
    Returns: (amount, unit)
    """
    key = ingredient_name.lower().strip()
    
    # Direct lookup
    rule = BASE_PORTION_RULES.get(key)
    
    # Partial match fallback (e.g. "Hakket Oksekød" -> "oksekød")
    if not rule:
        for k, v in BASE_PORTION_RULES.items():
            if k in key or key in k:
                rule = v
                break
    
    # Default: treat as pack item (1 purchase)
    if not rule:
        rule = {"amount": 1, "unit": "stk", "pack_item": True}
    
    if rule.get("pack_item", False):
        # Pack item: 1 purchase per recipe, not per portion
        return rule["amount"], rule["unit"]
    else:
        # Scalable: multiply by portions
        return rule["amount"] * portions, rule["unit"]

# ============================================================
#  THE MATCHING ENGINE (NLP-Enhanced)
# ============================================================

# --- 1. PROCESSED PRODUCT MARKERS ---
# Global list of keywords that indicate a product is NOT a raw ingredient.
PROCESSED_MARKERS = [
    "nuggets", "schnitzel", "burgerbøf", "færdigret", "paneret",
    "sticks", "fingers", "crispy", "breaded", "dino",
    "cordon bleu", "kiev", "spring rolls", "forårsruller",
    "strips", "bites", "popcorn", "toast", "sandwich",
    "pizza", "lasagne", "gratin", "pølse", "hotdog",
    "frikadelle", "kroketter", "fritter",
    "milkshake", "proteindrik", "smoothie", "is bæger",
]

# --- 2. INGREDIENT-SPECIFIC TRAP LIST ---
# Per-ingredient negative keywords that disqualify a deal.
INGREDIENT_TRAP_LIST = {
    "kylling": ["nuggets", "burger", "schnitzel", "sticks", "dino", "pølse",
                "paneret", "crispy", "kiev", "cordon bleu", "strips", "bites",
                "popcorn kylling", "spring roll", "færdigret", "toast"],
    "oksekød": ["lasagne", "færdigret", "pizza", "burgerbøf", "frosne",
                "spring roll", "forårsruller", "gratin"],
    "hakket oksekød": ["lasagne", "færdigret", "pizza", "burgerbøf",
                       "frosne", "spring roll", "gratin"],
    "hakket kylling": ["nuggets", "burger", "schnitzel", "sticks", "dino",
                       "paneret", "crispy", "færdigret"],
    "svinekød": ["pølse", "hotdog", "bacon bits", "burgerbøf", "færdigret",
                 "spring roll", "nuggets"],
    "fisk": ["fiskepinde", "fish sticks", "paneret", "burgerbøf",
             "færdigret", "fish fingers"],
    "fiskefars": ["fiskepinde", "fish sticks", "paneret", "færdigret"],
    "mælk": ["kakaomælk", "kokosmælk", "mandelmælk", "rismælk", "soyamælk",
             "kærnemælk", "havremælk", "milkshake", "proteindrik", "chokolade"],
    # Short terms collide with Danish words that merely END in them. These traps
    # are what stop "æg" reading "ungkvæg", "ris" reading "Frilandsgris" or
    # "Literpris", and "ost" reading "Æblemost".
    "æg": ["pålæg", "chokoladeæg", "påskeæg", "spejlæg", "kvæg"],
    "smør": ["smørbar", "peanutbutter", "jordnøddesmør"],
    "mel": ["melis", "melon", "melange"],
    "bønner": ["kaffebønner", "jelly beans"],
    "is": ["metropolis", "basis", "chips", "disse", "fisk", "frisk", "gris",
           "hvis", "linser", "maj", "melis", "pris", "pisk", "ris",
           "spidskål", "viskestykker"],
    "ost": ["ostemad", "ostepop", "cheez", "ostesovs", "most", "frost", "kost", "post"],
    "pasta": ["pastasovs", "færdigret"],
    "ris": ["risifrutti", "risdrik", "risengrød", "gris", "pris", "friste"],
    "kartofler": ["kartoffelchips", "chips", "pommes", "fritter"],
    "rejer": ["rejemad", "rejesalat", "færdigret"],
    "laks": ["laksepaté", "laksemousse", "færdigret", "røget laks"],
    "bacon": ["bacon bits", "baconchips", "baconost"],
}

def is_processed_product(deal_item_lower):
    """Returns True if the deal item contains any processed product marker."""
    return any(marker in deal_item_lower for marker in PROCESSED_MARKERS)

def is_match(search_term, deal_item):
    """
    NLP-Enhanced Matching Logic:
    1. Ingredient-Specific Trap List (per-keyword negative filter).
    2. Processed Product Filter (global filter for raw ingredients).
    3. Multi-Word Exact Phrase Priority.
    4. Short Words (<4): Exact Substring Match.
    5. Long Words (>=4): Substring OR High-Confidence Fuzzy.
    """
    term = search_term.lower().strip()
    item = deal_item.lower().strip()

    # --- STEP 1: INGREDIENT-SPECIFIC TRAP LIST ---
    # Check direct key first, then check if any trap-list key is a substring of the search term.
    trap_words = INGREDIENT_TRAP_LIST.get(term)
    if not trap_words:
        for trap_key, trap_vals in INGREDIENT_TRAP_LIST.items():
            if trap_key in term or term in trap_key:
                trap_words = trap_vals
                break

    if trap_words:
        for bad_word in trap_words:
            # A trap word contained in the search term is self-defeating: the term is
            # the more specific ingredient. "kokosmælk" inherits mælk's trap list, which
            # lists "kokosmælk"; "risnudler" inherits is's list, which lists "ris".
            # Without this guard those ingredients can never match any deal.
            if bad_word in term:
                continue
            if bad_word in item:
                return False

    # --- STEP 2: PROCESSED PRODUCT FILTER ---
    # If the search term looks like a raw ingredient (single common word),
    # reject any deal that is clearly a processed product.
    # Skipped when the term IS the processed product: a recipe asking for "pølser"
    # wants sausages, so "pølse" must not filter them out.
    if not any(marker in term for marker in PROCESSED_MARKERS):
        if is_processed_product(item):
            return False

    # --- STEP 3: MULTI-WORD EXACT PHRASE ---
    # If search term has multiple words (e.g. "Hakket Kylling"),
    # ALL words must appear in the deal item.
    term_words = term.split()
    if len(term_words) > 1:
        # Every word in the search term must be present in the deal item
        if all(w in item for w in term_words):
            return True
        # Fuzzy fallback for multi-word: token_sort_ratio > 85
        try:
            if fuzz.token_sort_ratio(term, item) > 85:
                return True
        except:
            pass
        # If multi-word term doesn't match fully, reject.
        return False

    # --- STEP 4 & 5: SINGLE-WORD MATCHING ---
    if len(term) < 4:
        # STRICT MODE for short words
        if term == "is":
            padded = f" {item} "
            return f" {term} " in padded or padded.startswith(f" {term} ") or padded.endswith(f" {term} ")
        # A bare substring test matches mid-word noise: "ris" hit "Fristelser",
        # putting a bag of sweets on the shopping list as rice.
        # Danish compounds are still honoured ("jasminris", "skrabeaeg"), but a
        # compound has to be meaningfully longer than the term itself - otherwise
        # "pris" (price), which ends every offer line, reads as "ris".
        return any(
            w == term or (len(w) >= len(term) + 3 and (w.startswith(term) or w.endswith(term)))
            for w in re.split(r"[\s\-/]+", item)
        )
    else:
        # LONG WORDS: Substring first
        if term in item:
            return True
        # Fuzzy Fallback (typos, word order)
        try:
            if fuzz.token_sort_ratio(term, item) > 90:
                return True
        except:
            pass

    return False

# --- PRICE PLAUSIBILITY ---
# Average expected prices per kg for common raw ingredients (in DKK).
# Used to flag deals that are suspiciously cheap/expensive (likely processed).
EXPECTED_PRICE_RANGES = {
    "kylling":   {"min": 30, "max": 120},  # per kg
    "oksekød":   {"min": 50, "max": 150},
    "svinekød":  {"min": 30, "max": 100},
    "laks":      {"min": 80, "max": 200},
    "fisk":      {"min": 40, "max": 160},
    "æg":        {"min": 15, "max": 50},   # per pack (10stk)
    "mælk":      {"min": 8,  "max": 25},   # per liter
}

def is_price_plausible(search_term, deal):
    """
    Checks if a deal's price falls within expected range for the ingredient.
    Returns True if plausible or no rule exists. False if suspicious.
    """
    term = search_term.lower().strip()
    
    # Find matching price range
    price_range = EXPECTED_PRICE_RANGES.get(term)
    if not price_range:
        for k, v in EXPECTED_PRICE_RANGES.items():
            if k in term:
                price_range = v
                break
    
    if not price_range:
        return True  # No rule -> assume OK
    
    price = deal.get("price", 0)
    if price <= 0:
        return True
        
    # Normalize to per-unit price using deal's unit_size
    unit_size = deal.get("unit_size", 1.0)
    unit_type = deal.get("unit_type", "stk")
    
    # For weight-based items, normalize to per-kg
    if unit_type == "g" and unit_size > 0:
        price_per_kg = (price / unit_size) * 1000
    elif unit_type == "kg" and unit_size > 0:
        price_per_kg = price / unit_size
    else:
        # Can't normalize meaningfully (stk, ml, etc.)
        # Just check raw price against range
        price_per_kg = price
    
    # Allow 50% tolerance outside range
    lower_bound = price_range["min"] * 0.5
    upper_bound = price_range["max"] * 1.5
    
    return lower_bound <= price_per_kg <= upper_bound

def find_matching_deals(item_name, all_deals):
    """
    Returns every deal that passes NLP matching and the price plausibility check.
    Keeping the full set (rather than only the winner) lets callers measure the
    real spread between the cheapest and dearest offer for the same ingredient.
    """
    return [
        deal for deal in all_deals
        if is_match(item_name, deal['item']) and is_price_plausible(item_name, deal)
    ]


def find_cheapest_deal(item_name, all_deals, threshold=MATCH_THRESHOLD):
    """
    Finds the best deal using:
    1. NLP-enhanced boolean matching (is_match).
    2. Price plausibility check.
    3. Price minimization among valid matches.
    """
    matches = find_matching_deals(item_name, all_deals)
    return min(matches, key=lambda d: d['price']) if matches else None

# ============================================================
#  MEAL PLANNING LOGIC
# ============================================================

def load_meal_templates():
    try:
        path = os.path.join(CONFIG_DIR, "meal_templates.json")
        if not os.path.exists(path):
             return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[ERROR] Could not load meal_templates.json: {e}")
        return []

# --- EMAIL TEMPLATE LOADER ---
def load_email_template():
    try:
        path = os.path.join(TEMPLATES_DIR, "email_template.html")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"[ERROR] Could not load email_template.html: {e}")
        return "<html><body>Meal Plan Attached</body></html>"

# ============================================================
#  HTML/JS STRIPPER (Token Squeezer)
# ============================================================

class _HTMLTextExtractor(HTMLParser):
    """Strips HTML tags and extracts plain text."""
    def __init__(self):
        super().__init__()
        self._result = StringIO()
        self._skip_tags = {"script", "style", "noscript"}
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._result.write(data)

    def get_text(self):
        return self._result.getvalue()


def strip_html_js(raw_text):
    """
    Token Squeezer: Strips all HTML tags, JavaScript, and CSS from scraped text.
    Keeps only the visible text content for clean Gemini prompts.
    """
    if not raw_text:
        return ""

    # Step 1: Remove <script>...</script> and <style>...</style> blocks
    cleaned = re.sub(r'<script[^>]*>.*?</script>', '', raw_text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<noscript[^>]*>.*?</noscript>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)

    # Step 2: Parse remaining HTML to extract text
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(cleaned)
        text = extractor.get_text()
    except Exception:
        # Fallback: brute-force strip tags
        text = re.sub(r'<[^>]+>', ' ', cleaned)

    # Step 3: Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Step 4: Remove common JS artifacts
    text = re.sub(r'(?:function|var|let|const|window\.|document\.)\s*\w+', '', text)

    return text


# ============================================================
#  CREDENTIAL VERIFICATION
# ============================================================

def verify_credentials():
    """
    Pre-flight check: Verifies API key and Google Sheets credentials exist.
    Exits with code 1 if missing (prevents Task Scheduler from hanging).
    """
    errors = []

    # Check Gemini API Key
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        errors.append("GEMINI_API_KEY is not set in environment / .env file.")

    # Check Google Sheets credentials file
    creds_file = os.path.join(BASE_DIR, "credentials.json")
    if not os.path.exists(creds_file):
        errors.append(f"Google Sheets credentials file '{creds_file}' not found.")
    else:
        try:
            with open(creds_file, "r") as f:
                creds_data = json.load(f)
            if "client_email" not in creds_data:
                errors.append(f"'{creds_file}' does not contain a valid service account.")
        except (json.JSONDecodeError, IOError) as e:
            errors.append(f"'{creds_file}' is invalid: {e}")

    if errors:
        for err in errors:
            logger.critical(f"[ERROR] CREDENTIAL CHECK FAILED: {err}")
        logger.critical("Exiting with code 1. Fix credentials before next run.")
        sys.exit(1)

    logger.info("[SUCCESS] Credential check passed.")
    return api_key


# ============================================================
#  GEMINI 3 FLASH PREVIEW - RETRY LOGIC
# ============================================================

GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_MAX_RETRIES = 5
GEMINI_BACKOFF_BASE = 2  # seconds
GEMINI_COOLDOWN_SECONDS = 30


def call_gemini_with_retry(client, prompt, response_mime_type=None):
    """
    Calls Gemini 3.0 Flash with robust retry logic:
    - 429 (Resource Exhausted): Exponential backoff (2s → 4s → 8s → 16s → 32s)
    - 500/503 (Server Error): 30-second cooldown + single retry
    - Other errors: Raise immediately
    
    Args:
        client: google.genai.Client instance
        prompt: The prompt string to send
        response_mime_type: Optional MIME type for structured output (e.g. "application/json")
    
    Returns:
        The response text from Gemini
    """
    config = {}
    if response_mime_type:
        config = genai_types.GenerateContentConfig(
            response_mime_type=response_mime_type,
        )

    last_exception = None
    server_error_retried = False

    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            logger.info(f"[INFO] Gemini API call (attempt {attempt}/{GEMINI_MAX_RETRIES})...")
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config if config else None,
            )
            logger.info("[SUCCESS] Gemini response received.")
            return response.text

        except Exception as e:
            error_str = str(e).lower()
            last_exception = e

            # --- 404: Model Not Found ---
            # google-genai raises its own errors.ClientError, which does NOT inherit
            # from google.api_core.exceptions.NotFound - catching that class here was
            # dead code. Match on the status instead, before the generic branches.
            if "404" in str(e) or "not_found" in error_str or "not found" in error_str:
                logger.critical(f"[ERROR] Model '{GEMINI_MODEL}' not found. Please check model naming conventions.")
                logger.critical("Stopping execution to prevent useless retries.")
                sys.exit(1)

            # --- 429: Resource Exhausted (Rate Limit) ---
            if "429" in str(e) or "resource exhausted" in error_str or "rate" in error_str:
                wait_time = GEMINI_BACKOFF_BASE ** attempt
                logger.warning(
                    f"[WARNING] 429 Rate Limit hit. Exponential backoff: "
                    f"waiting {wait_time}s before retry {attempt}/{GEMINI_MAX_RETRIES}..."
                )
                time.sleep(wait_time)
                continue

            # --- 500/503: Server Error ---
            elif "500" in str(e) or "503" in str(e) or "internal" in error_str or "unavailable" in error_str:
                if not server_error_retried:
                    logger.warning(
                        f"[WARNING] Server Error (500/503). Cooling down for "
                        f"{GEMINI_COOLDOWN_SECONDS}s before single retry..."
                    )
                    time.sleep(GEMINI_COOLDOWN_SECONDS)
                    server_error_retried = True
                    continue
                else:
                    logger.error("[ERROR] Server error persists after cooldown. Giving up.")
                    raise

            # --- Other errors: fail fast ---
            else:
                logger.error(f"[ERROR] Unrecoverable Gemini error: {e}")
                raise

    # Exhausted all retries
    logger.error(f"[ERROR] All {GEMINI_MAX_RETRIES} Gemini retries exhausted.")
    raise last_exception


# ============================================================
#  AI MEAL PLAN GENERATOR
# ============================================================

def build_deals_summary(all_deals, max_deals=400):
    """
    Formats parsed deals into a compact priced list for the Gemini prompt.

    Previously the prompt was fed raw scraped page text truncated to 3000 chars,
    which meant the model saw navigation noise instead of prices, and the last
    stores in the list never reached it at all. Structured deals are ~40 chars
    each, so the whole week's catalogue fits.
    """
    if not all_deals:
        return "No deals available."

    # Dedupe: the same offer often appears several times on one page.
    seen = set()
    by_store = {}
    for deal in all_deals:
        key = (deal["store"], deal["item"].lower(), deal["price"])
        if key in seen:
            continue
        seen.add(key)
        by_store.setdefault(deal["store"], []).append(deal)

    lines = []
    remaining = max_deals
    for store, deals in by_store.items():
        if remaining <= 0:
            break
        # Cheapest first: if we do hit the cap, keep the offers worth planning around.
        deals.sort(key=lambda d: d["price"])
        chunk = deals[:remaining]
        remaining -= len(chunk)
        lines.append(f"--- {store} ---")
        for d in chunk:
            size = f" ({d['unit_size']:g} {d['unit_type']})" if d.get("unit_size") else ""
            lines.append(f"{d['item']} - {d['price']:.2f} kr{size}")

    return "\n".join(lines)


def generate_ai_meal_plan(client, templates, deals_summary, pantry):
    """
    Uses Gemini 3.0 Flash to generate an optimized weekly meal plan.
    Enforces JSON output via response_mime_type="application/json".
    
    Returns:
        Parsed JSON dict with meal plan, or None on failure.
    """
    # Build a compact summary of available info
    template_names = [t["name"] for t in templates]
    template_info = json.dumps(
        [{"name": t["name"], "ingredients": t["ingredients"]} for t in templates],
        ensure_ascii=False
    )

    prompt = f"""You are a meal planning assistant for a Danish household of 2 people.
Rules:
- Plan follows Low-FODMAP diet principles
- Batch cooking: Cook on Monday and Wednesday, eat leftovers the next day
- Friday/Saturday/Sunday are flexible days
- Use ingredients available in current deals when possible
- All meal names should be in Danish

Available meal templates:
{template_info}

Current store deals (item - price - pack size). Prefer meals whose ingredients appear here:
{deals_summary}

Pantry items already available:
{json.dumps(pantry, ensure_ascii=False)}

Generate a weekly meal plan as JSON with this exact structure:
{{
    "meal_plan": [
        {{
            "day": "Mandag",
            "type": "cook",
            "meal_name": "...",
            "ingredients": ["ingredient1", "ingredient2", ...],
            "portions": 4
        }},
        {{
            "day": "Tirsdag",
            "type": "leftover",
            "meal_name": "... (Rester)",
            "portions": 0
        }},
        ...
    ],
    "reasoning": "Brief explanation of choices"
}}

Include all 7 days. Monday and Wednesday are "cook" days, Tuesday and Thursday are "leftover" days, Friday/Saturday/Sunday are "flexible" days.
"""

    try:
        response_text = call_gemini_with_retry(
            client, prompt, response_mime_type="application/json"
        )
        result = json.loads(response_text)
        logger.info(f"[INFO] AI Meal Plan generated. Reasoning: {result.get('reasoning', 'N/A')}")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"[ERROR] Gemini returned invalid JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"[ERROR] AI meal plan generation failed: {e}")
        return None


def generate_weekly_plan(templates, pantry, all_deals):
    """
    Selects 2 primary meals for batch cooking (Mon/Wed) based on FODMAP & Availability.
    Schedule: Mon (Cook A x4), Tue (Leftover A), Wed (Cook B x4), Thu (Leftover B), Fri (Flexible).
    Note: Portions set to 4 to cover 2 people for 2 days (Dinner today + Dinner tomorrow).
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
    # Portions: 4 (Covers 2 days for 2 people)
    schedule = [
        {"day_name": "Monday", "type": "cook", "meal_name": meal_A['meal'], "portions": 4, "ingredients": meal_A['ingredients']},
        {"day_name": "Tuesday", "type": "leftover", "meal_name": f"{meal_A['meal']} (Leftovers)", "portions": 0},
        {"day_name": "Wednesday", "type": "cook", "meal_name": meal_B['meal'], "portions": 4, "ingredients": meal_B['ingredients']},
        {"day_name": "Thursday", "type": "leftover", "meal_name": f"{meal_B['meal']} (Leftovers)", "portions": 0},
        {"day_name": "Friday", "type": "flexible", "meal_name": "Tøm Køleskabet / Tapas", "portions": 0},
        {"day_name": "Saturday", "type": "flexible", "meal_name": "FODMAP Pantry / Flexible", "portions": 0},
        {"day_name": "Sunday", "type": "flexible", "meal_name": "Sunday Roast / Flexible", "portions": 0},
    ]
    
    return schedule

def generate_shopping_list(buying_list, schedule, all_deals, pantry_list):
    """
    Generates Grouped Shopping List.
    Step 1: Tally total NEEDED amount (e.g. 16 eggs).
    Step 2: Subtract Pantry (e.g. have 4 eggs -> need 12).
    Step 3: Find best deal & Optimize Packs (e.g. Deal=10 pack -> Buy 2 packs).
    """
    
    # 1. TALLY NEEDS
    # Key = Ingredient Name, Val = {"amount": X, "unit": Y}
    aggregated_needs = {}
    
    # Helper to add to tally
    def add_need(name, portion_count=1):
        amt, unit = calculate_quantity(name, portion_count)
        k = name.lower()
        if k not in aggregated_needs:
             aggregated_needs[k] = {"name": name, "amount": 0.0, "unit": unit}
        aggregated_needs[k]["amount"] += amt
        
    # A. Schedule Ingredients
    for day in schedule:
        if day['type'] == 'cook':
            portions = day['portions'] # e.g. 4
            for ing in day.get('ingredients', []):
                add_need(ing, portions)
                
    # B. Buying List (Manual Additions)
    for item in buying_list:
        add_need(item, 1) # Treat as 1 portion equivalent
        
    # 2. PANTRY DEDUCTION
    # Parse pantry list: look for "Item (Qty)" or just "Item"
    for pantry_item in pantry_list:
        p_name = pantry_item.lower()
        p_qty = 0
        
        # Try to parse "Æg 4 stk" or "Æg (4)"
        # Simple regex: find digits
        qty_match = re.search(r"(\d+)", p_name)
        if qty_match:
            p_qty = float(qty_match.group(1))
            # Remove digits for name matching
            p_name = re.sub(r"[\d\(\)]", "", p_name).strip()
        else:
            # Default pantry deduction if item exists but no qty specified?
            # Assume we have *some* supply. Maybe deduct 1 portion?
            # User example: "pantry has 6". This implies explicit count.
            # If no count, assume fully stocked? Or 0?
            # Safer to assume 0 deduction if no quantity specified to avoid under-buying
            # UNLESS user explicitly asked for "Pantry First Deduction".
            # Let's deduct 1 'unit' if no qty specified as a conservative heuristic.
            p_qty = 1.0

        # Match against needs
        matched_key = None
        if p_name in aggregated_needs:
            matched_key = p_name
        else:
            # Fuzzy match pantry item to needs
            # e.g. pantry "oats" vs need "havregryn"? (No translation here)
            # e.g. pantry "hakket oksekød" vs need "oksekød"
            for k in aggregated_needs:
                if k in p_name or p_name in k:
                    matched_key = k
                    break
        
        if matched_key:
            # DEDUCT
            # Verify units? Pantry usually implies 'stk' or same unit as base.
            # If need "g" and pantry says "4" (implied packs?), verify.
            # We assume pantry count matches usage unit OR pack count.
            # If need 500g and pantry has 1 (pack). 1 pack = ?
            # Simplifying assumption: Pantry Quantity is in SAME UNIT as Base Rules.
            aggregated_needs[matched_key]["amount"] = max(0, aggregated_needs[matched_key]["amount"] - p_qty)
            
    # 3. MATCHING & OPTIMIZATION
    grouped_list = {}
    final_list_flat = []
    total_savings = 0.0
    
    for key, data in aggregated_needs.items():
        name = data["name"]
        needed_amt = data["amount"]
        unit = data["unit"]
        
        # Pantry deduction already happened in step 2 above, against pantry_list.
        # Anything the pantry fully covers is not shopping - drop it rather than
        # printing "0.0 stk" next to a deal you are not meant to buy.
        if needed_amt <= 0:
            continue

        matches = find_matching_deals(name, all_deals)
        best_deal = min(matches, key=lambda d: d['price']) if matches else None

        entry = {
            "name": name,
            "total_needed": f"{needed_amt:.1f} {unit}",
            "buy_qty": 0,
            "pack_size": "-",
            "price": 0.0,
            "found_name": None,
            "store": "Unknown"
        }
        
        if best_deal:
            # OPTIMIZATION
            deal_size = best_deal.get("unit_size", 1.0)
            deal_unit = best_deal.get("unit_type", "stk")
            
            # Normalize deal unit if possible?
            # If needed "g" and deal "kg", convert deal to g
            if unit == "g" and deal_unit == "kg":
                deal_size *= 1000
            elif unit == "kg" and deal_unit == "g":
                deal_size /= 1000
                
            # If units mismatch (e.g. needed 'stk', deal 'g'), we can't do math.
            # Fallback to 1 pack per X amount?
            # Basic fallback: 1 pack covers 'base rule amount' * 4?
            
            packs_to_buy = 1
            
            if unit == deal_unit or (unit in ["g", "kg", "ml", "l"] and deal_unit in ["g", "kg", "ml", "l"]):
                 # We can do math
                 if deal_size > 0:
                     packs_to_buy = math.ceil(needed_amt / deal_size)
            else:
                 # Units differ (e.g. Need 4 stk eggs, Deal says 500g eggs? Unlikely for eggs)
                 # Fallback: needed 150g, deal is "1 stk".
                 # If deal has no unit parsed, deal_size=1.
                 # If needed > 1 (e.g. 500g), buying 500 packs is wrong.
                 # Heuristic: If needed is "mass" (g) and deal is "count" (stk), usually 1 pack is enough?
                 # Unless quantity is huge.
                 packs_to_buy = 1
            
            entry["buy_qty"] = packs_to_buy
            entry["is_deal"] = True
            entry["price"] = best_deal['price'] * packs_to_buy
            entry["found_name"] = best_deal['item']
            entry["store"] = best_deal['store']
            entry["pack_size"] = f"{best_deal.get('unit_size')} {best_deal.get('unit_type')}"

            # Real, measurable saving: what this ingredient would have cost at the
            # dearest LIKE-FOR-LIKE offer, minus what it costs at the one we picked.
            # (The previous version assumed a flat 20% discount, which was invented -
            # no pre-discount price is ever scraped, so it could not be derived.)
            #
            # Only near-identical product names count. Fuzzy matching happily groups
            # "Jasminris" with "Risotto Arborio", and treating that gap as a saving
            # produced totals larger than the whole basket.
            comparable = [
                d for d in matches
                if d['store'] != best_deal['store']
                and fuzz.token_sort_ratio(d['item'].lower(), best_deal['item'].lower()) >= 80
            ]
            if comparable:
                dearest = max(comparable, key=lambda d: d['price'])
                total_savings += max(0.0, dearest['price'] - best_deal['price']) * packs_to_buy
        else:
            entry["store"] = "General/Other"
            entry["buy_qty"] = 1 # estimation
            
        final_list_flat.append(entry)
        
        store = entry["store"]
        if store not in grouped_list: grouped_list[store] = []
        grouped_list[store].append(entry)
        
    return grouped_list, final_list_flat, total_savings

# ============================================================
#  SHEETS & EMAIL
# ============================================================

def get_sheets_client():
    # Rate limiter removed as per request
    creds_path = os.path.join(BASE_DIR, "credentials.json")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)

def save_to_sheets(schedule, shopping_list):
    try:
        gc = get_sheets_client()
        sh = gc.open(SPREADSHEET_NAME)
        
        # 1. Meal Plan
        try:
            ws = sh.worksheet("MealPlan")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet("MealPlan", 100, 10)
        ws.clear()
        
        headers = ["Day", "Meal", "Ingredients"]
        rows = [[m['day_name'], m['meal_name'], ", ".join(m.get('ingredients', []))] for m in schedule]
        ws.update(range_name="A1", values=[headers] + rows)
        
        # 2. Shopping List
        try:
            ws_shop = sh.worksheet("ShoppingList")
        except gspread.WorksheetNotFound:
            ws_shop = sh.add_worksheet("ShoppingList", 100, 10)
        ws_shop.clear()
        
        headers_shop = ["Item", "Qty", "Price", "Store", "Found Match"]
        rows_shop = []
        for item in shopping_list:
            price = f"{item['price']:.2f}" if item['price'] else "-"
            match = item['found_name'] if item['found_name'] else "-"
            qty = f"x{item['buy_qty']}"
            rows_shop.append([item['name'], qty, price, item['store'], match])
            
        ws_shop.update(range_name="A1", values=[headers_shop] + rows_shop)
        logger.info("[SUCCESS] Saved to Google Sheets")
    except Exception as e:
        logger.error(f"[ERROR] Failed to save to Google Sheets: {e}")
        # Save locally as a fallback
        with open("last_shopping_list_export.json", "w", encoding="utf-8") as f:
            json.dump({"schedule": schedule, "shopping_list": shopping_list}, f)
        logger.info("Shopping list saved locally to last_shopping_list_export.json as fallback.")

def send_email_notification(schedule, shopping_list_grouped, total_savings):
    if not EMAIL_ADDRESS: return
    
    # Load External Template
    html_template = load_email_template()
    template = Template(html_template)
    
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
    logger.info("[MAIL_STATUS] Email sent successfully.")

# ============================================================
#  MAIN PIPELINE
# ============================================================

def scrape_deals_raw(store, url):
    """Playwright Scraper (Raw Text)"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url)
            try:
                # Hardening for specifically 365 Discount or others that load slow
                if "365" in store.lower():
                    page.wait_for_load_state("domcontentloaded")
                    # Confirm render by waiting for common product selectors
                    try:
                        page.wait_for_selector("article, .product, .item, .tile", timeout=7000)
                    except PlaywrightTimeout:
                        logger.warning(f"[WARNING] {store}: Render confirmation selector not found, proceeding...")

                page.wait_for_load_state("networkidle", timeout=10000)

                # Consent banner. Prefer the privacy-preserving option: reject
                # or necessary-only first, and only fall back to a generic
                # accept when the banner offers nothing else. The old pattern
                # matched "tillad" first, which selects "TILLAD ALLE" (allow
                # all) on the Coop sites - the worst available choice.
                for pattern in (
                    r"kun n\wdvendige|only necessary|afvis alle|reject all|n\wdvendige kun",
                    r"afvis|reject|decline",
                    r"accepter|godkend|accept|ok",
                ):
                    try:
                        btn = page.locator("button, a").filter(
                            has_text=re.compile(pattern, re.IGNORECASE)).first
                        if btn.count() > 0:
                            btn.click(timeout=1500)
                            page.wait_for_timeout(1200)
                            logger.debug(f"[DEBUG] {store}: consent handled via /{pattern}/")
                            break
                    except PlaywrightError:
                        continue

            except PlaywrightError as e:
                # Page may still hold usable text even if a wait timed out, so read on.
                logger.warning(f"[WARNING] {store}: page did not settle ({type(e).__name__}), reading anyway.")

            return page.inner_text("body")
        finally:
            browser.close()

def load_lists_from_sheets():
    fallback_file = os.path.join(DATA_DIR, "pantry_buying_fallback.json")
    
    try:
        gc = get_sheets_client()
        sh = gc.open(SPREADSHEET_NAME)
        buy = [i.strip() for i in sh.worksheet("BuyingList").col_values(1)[1:] if i.strip()]
        pantry = [i.strip() for i in sh.worksheet("PantryList").col_values(1)[1:] if i.strip()]
        
        # Save successful fetch to fallback cache
        with open(fallback_file, "w", encoding="utf-8") as f:
            json.dump({"buy": buy, "pantry": pantry, "timestamp": time.time()}, f)
            
    except Exception as e:
        logger.error(f"[ERROR] Google Sheets API Error: {e}. Attempting to load from local fallback...")
        if os.path.exists(fallback_file):
            try:
                with open(fallback_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    buy = data.get("buy", [])
                    pantry = data.get("pantry", [])
                    logger.info("[INFO] Loaded successfully from local fallback cache.")
            except Exception as e_fallback:
                logger.error(f"[ERROR] Fallback cache failed: {e_fallback}")
                buy, pantry = [], []
        else:
            logger.error("[ERROR] No local fallback cache found.")
            buy, pantry = [], []
    
    return buy, pantry

def is_automated_run():
    """Detect if running via Task Scheduler/Cron (legacy env var or --auto flag)."""
    return AUTO_MODE or os.environ.get("FOODPLANNER_AUTOMATED") == "1"


# ============================================================
#  REUSABLE PIPELINE STAGES
#  Split out of main() so the web app can generate a plan without
#  also writing to Sheets or sending mail.
# ============================================================

def collect_deals(force_refresh=False, store_names=None):
    """Scrapes (or reads from cache) the selected stores. Returns parsed deals."""
    all_deals = []
    for store_name, url in resolve_stores(store_names).items():
        try:
            raw_text = None if force_refresh else get_cached_raw_text(store_name)
            if not raw_text:
                logger.info(f"  Scraping fresh deals for {store_name} via Playwright...")
                raw_text = scrape_deals_raw(store_name, url)
                if raw_text:
                    set_cached_raw_text(store_name, raw_text)
            else:
                logger.info(f"  Using cached deals for {store_name}.")

            if raw_text:
                # NB: parse line-by-line on the raw text. strip_html_js collapses
                # newlines, which would flatten every deal into a single line.
                structured = parse_scraped_text(raw_text, store_name)
                if not structured:
                    logger.warning(f"[WARNING] No deals found for {store_name}.")
                all_deals.extend(structured)
            else:
                logger.warning(f"[WARNING] Could not retrieve raw text for {store_name}.")
        except Exception as e:
            logger.error(f"[ERROR] Failed to process {store_name}: {e}")
    return all_deals


def cached_deals(ignore_expiry=True, store_names=None):
    """
    Reads parsed deals straight from the cache without ever launching a browser.
    The web app uses this so page loads stay instant; only an explicit refresh
    is allowed to scrape. By default expiry is ignored - showing last week's
    prices beats showing an empty page.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT store_name, raw_text, timestamp FROM store_deals_cache").fetchall()
    finally:
        conn.close()

    wanted = set(resolve_stores(store_names))
    deals = []
    for store_name, raw_text, ts in rows:
        if not raw_text or store_name not in wanted:
            continue
        if not ignore_expiry and time.time() - ts >= CACHE_EXPIRY_SECONDS:
            continue
        deals.extend(parse_scraped_text(raw_text, store_name))
    return deals


def build_schedule(templates, pantry, all_deals, gemini_client=None):
    """
    Returns (schedule, source). Tries Gemini when a client is supplied and falls
    back to the deterministic scorer on any failure, so the app still produces a
    plan with no API key configured.
    """
    if gemini_client is not None:
        try:
            ai_plan = generate_ai_meal_plan(
                gemini_client, templates, build_deals_summary(all_deals), pantry)
            if ai_plan and "meal_plan" in ai_plan:
                logger.info("[INFO] Using AI-generated meal plan.")
                return [
                    {
                        "day_name": d.get("day", "Unknown"),
                        "type": d.get("type", "flexible"),
                        "meal_name": d.get("meal_name", "Flexible"),
                        "portions": d.get("portions", 0),
                        "ingredients": d.get("ingredients", []),
                    }
                    for d in ai_plan["meal_plan"]
                ], "gemini"
        except Exception as e:
            logger.warning(f"[WARNING] AI meal plan failed, using rule-based fallback: {e}")

    logger.info("[INFO] Using rule-based meal plan (fallback).")
    return generate_weekly_plan(templates, pantry, all_deals), "rule-based"


def generate_plan(buying, pantry, all_deals, gemini_client=None):
    """Full plan generation with no side effects. Returns a result dict."""
    templates = load_meal_templates()
    schedule, source = build_schedule(templates, pantry, all_deals, gemini_client)
    grouped, flat, savings = generate_shopping_list(buying, schedule, all_deals, pantry)
    return {
        "schedule": schedule,
        "grouped": grouped,
        "flat": flat,
        "savings": savings,
        "source": source,
        "deals_found": len(all_deals),
    }


def make_gemini_client(api_key):
    """Gemini needs the v1beta endpoint for preview models."""
    return genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})

def main():
    logger.info("============================================================")
    logger.info("  FOOD PLANNER: HYBRID ENGINE (Rule-Based + Gemini 3 Flash)")
    logger.info("============================================================")
    
    if is_automated_run():
        logger.info("[INFO] AUTOMATED RUN DETECTED: Suppressing all interactive prompts.")
        try:
            sys.stdin.close()
        except Exception:
            pass
    
    try:
        # ── STEP 0: Credential Pre-Flight ──
        logger.info("Verifying credentials...")
        api_key = verify_credentials()
        
        gemini_client = make_gemini_client(api_key)
        logger.info(f"[SUCCESS] Gemini client initialized (model: {GEMINI_MODEL})")

        # ── STEP 1: Load Data ──
        logger.info("Loading templates & lists...")
        buying, pantry = load_lists_from_sheets()

        # ── STEP 2: Scrape & Parse ──
        logger.info("Scraping stores...")
        all_deals = collect_deals()
        logger.info(f"Found {len(all_deals)} total deals.")

        # A plan with no prices is worse than no plan: the shopping list has no
        # stores, no quantities worth trusting, and the weekly email looks like
        # it worked. Fail loudly so the scheduled run reports a failure instead.
        if not all_deals:
            logger.critical("[ERROR] No deals scraped from any store.")
            logger.critical(
                "Every configured source returned nothing. If these are "
                "etilbudsavis.dk stores, the site now blocks scraping "
                "(HTTP body: NO_ACCESS / code 1102)."
            )
            logger.critical("Skipping Sheets and email. Exiting with code 3.")
            sys.exit(3)

        # ── STEP 3+4+5: Plan and shopping list ──
        logger.info("Generating meal plan...")
        result = generate_plan(buying, pantry, all_deals, gemini_client)

        # ── STEP 6: Persist locally so the web app sees this run ──
        try:
            store.init_db()
            store.save_plan(result["schedule"], result["flat"],
                            result["savings"], result["source"])
        except Exception as e:
            logger.warning(f"[WARNING] Could not save plan to local store: {e}")

        # ── STEP 7: Save to Google Sheets ──
        logger.info("Saving to Google Sheets...")
        save_to_sheets(result["schedule"], result["flat"])

        # ── STEP 8: Send Email ──
        logger.info("Sending email...")
        send_email_notification(result["schedule"], result["grouped"], result["savings"])

        logger.info("[SUCCESS] PIPELINE COMPLETE")

    except SystemExit:
        # Let sys.exit() propagate (from credential check)
        raise
    except Exception as e:
        logger.critical(f"[ERROR] FATAL ERROR: {e}")
        if AUTO_MODE:
            sys.exit(2)  # Non-zero exit for Task Scheduler error detection
        raise

if __name__ == "__main__":
    AUTO_MODE = parse_args().auto_mode or AUTO_MODE
    configure_logging(AUTO_MODE)
    main()

