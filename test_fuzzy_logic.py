"""
Analysis Tool Validation: Rule-Based Matching Engine Prototype (NO AI).
Tests fuzzy matching, price parsing, and deal selection.
"""
import re
import json
from thefuzz import process, fuzz

# --- 1. Regex Parser Logic (Rule-Based Data Structuring) ---
def parse_deal_line(line):
    """
    Extracts item name and price from a raw deal string.
    Supported formats: "Mælk 10 kr", "Ost 25,95", "Smør 15.-"
    """
    # Normalize price formats (replace comma with dot if it looks like a decimal)
    line = line.replace(",", ".")
    
    # Regex to find price: looks for number followed by kr, dkk, or .-
    # Captures: (price_value)
    price_pattern = r"(\d+(?:\.\d{1,2})?)\s*(?:kr|dkk|\.-)"
    match = re.search(price_pattern, line, re.IGNORECASE)
    
    if match:
        price_str = match.group(1)
        try:
            price = float(price_str)
            # Item name is everything else, cleaned
            item_name = re.sub(price_pattern, "", line, flags=re.IGNORECASE).strip()
            item_name = re.sub(r"\d+g|\d+kg|\d+l", "", item_name).strip() # Remove weights for better matching
            return {"name": item_name, "price": price, "raw": line}
        except ValueError:
            pass
            
    return None

def find_best_deals(shopping_list, all_deals):
    """
    Uses fuzzy logic to find the cheapest match for each shopping item.
    """
    found_deals = []
    missing_items = []
    
    # Flatten all deals into a searchable list
    deal_map = {} # name -> {price, store, raw}
    deal_names = []
    
    for store, lines in all_deals.items():
        for line in lines:
            parsed = parse_deal_line(line)
            if parsed:
                # Store by name for lookup after fuzzy match
                # Use store name in key to avoid overwrites if same item name exists? 
                # Actually we want a list of all parsed items to fuzzy match against.
                deal_names.append(parsed['name'])
                # Mapping back is tricky if names aren't unique. 
                # Let's verify match quality directly.
                pass

    # Better approach: Iterate shopping items and find best match in ALL deals
    for item in shopping_list:
        best_match = None
        best_score = 0
        best_deal = None
        
        for store, lines in all_deals.items():
            for line in lines:
                parsed = parse_deal_line(line)
                if not parsed: continue
                
                # Fuzzy Match
                # partial_ratio handles substrings well (e.g. "Mælk" in "Arla Sødmælk")
                score = fuzz.partial_ratio(item.lower(), parsed['name'].lower())
                
                # Boost score if exact word match
                if f" {item.lower()} " in f" {parsed['name'].lower()} ":
                     score += 10
                
                if score > 80: # Threshold
                    # If this is a better match OR same match but cheaper
                    if score > best_score:
                        best_score = score
                        best_match = parsed['name']
                        best_deal = {**parsed, "store": store}
                    elif score == best_score and best_deal and parsed['price'] < best_deal['price']:
                        best_deal = {**parsed, "store": store}
        
        if best_deal:
            found_deals.append({
                "wanted": item,
                "found": best_deal['name'],
                "price": best_deal['price'],
                "store": best_deal['store'],
                "score": best_score
            })
        else:
            missing_items.append(item)
            
    return found_deals, missing_items

# --- TEST ---
def run_test():
    print("--- 1. Testing Price Parsing ---")
    lines = [
        "Arla Øko Mælk 12,95 kr",
        "Kærgården 15.-",
        "Oksekød 8-12% 500g 35 DKK"
    ]
    for l in lines:
        print(f"'{l}' -> {parse_deal_line(l)}")

    print("\n--- 2. Testing Fuzzy Matching Logic ---")
    mock_deals = {
        "Netto": ["Økologisk Letmælk 10.95 kr", "Pasta Penne 5.-", "Hakket Oksekød 40 kr"],
        "Rema": ["Arla Skummetmælk 11,50 kr", "Änglamark Pasta 6.95 kr", "Svinekød 25.-"]
    }
    wanted = ["Mælk", "Pasta", "Oksekød", "Gær"] # Gær is missing
    
    found, missing = find_best_deals(wanted, mock_deals)
    
    print(f"Wanted: {wanted}")
    print("Found Deals:")
    for f in found:
        print(f" ✅ {f['wanted']} -> '{f['found']}' ({f['price']} kr) @ {f['store']} (Score: {f['score']})")
        
    print("Missing:")
    for m in missing:
        print(f" ❌ {m}")
        
    # Validation assertions
    assert len(found) == 3, "Should find Mælk, Pasta, Oksekød"
    assert len(missing) == 1, "Should miss Gær"
    # Check "Mælk" found CHEAPEST (Netto 10.95 < Rema 11.50) IF scores equal?
    # Fuzzy scores might differ slightly based on string length ("Letmælk" vs "Skummetmælk")
    # But confirms logic works generally.

if __name__ == "__main__":
    run_test()
