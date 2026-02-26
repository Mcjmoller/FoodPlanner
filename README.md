# FoodPlanner v2.0 - Hybrid Engine

A professional, automated meal planning and shopping list optimization engine for Danish households.

## Vision
The FoodPlanner Hybrid Engine combines the precision of deterministic rule-based matching with the intelligence of LLMs (Gemini 3 Flash) to generate weekly batch-cooking meal plans that are optimized for budget, seasonal deals, and Low-FODMAP dietary constraints.

## Architecture & Logic
- **Advanced NLP Matching**: Uses `thefuzz` and custom linguistic heuristics to match raw grocery deals with meal templates while avoiding "trap" products (e.g., matching "Kylling" but avoiding "Kyllingenuggets").
- **Gemini 3 Flash (v1beta)**: Orchestrates the weekly plan, ensuring a balanced diet and adherence to the 2-day batch cooking schedule.
- **Low-FODMAP Integration**: Filters and scores meals based on high/low FODMAP ingredients.
- **Multi-Store Scraper**: High-performance scraping of Danish grocery stores (365 Discount, Rema 1000, etc.) using Playwright.

## Project Structure
```text
FoodPlanner/
├── src/                # Python source code (main.py, tests)
├── config/             # Meal templates and rule definitions
├── templates/          # Jinja2 email templates
├── data/               # Persistent caches and fallback data
└── requirements.txt    # Pinned dependencies
```

## Setup Instructions

1. **Environment Variables**: Create a `.env` file in the root directory:
   ```env
   GEMINI_API_KEY=your_key_here
   SPREADSHEET_NAME=FoodPlanner
   EMAIL_ADDRESS=your_email@gmail.com
   EMAIL_PASSWORD=your_app_password
   EMAIL_RECEIVER=receiver@example.com
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   ```

2. **Google Sheets**:
   - Save your Service Account JSON as `credentials.json` in the root.
   - Ensure the spreadsheet has worksheets: `MealPlan`, `ShoppingList`, `BuyingList`, `PantryList`.

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

## Troubleshooting 404 Errors
If you encounter a `[ERROR] Model Not Found (404)`, ensure that:
1. You are using the `v1beta` endpoint (handled automatically in `main.py`).
2. The `GEMINI_MODEL` string is correctly set to `gemini-3-flash-preview` or a valid stable equivalent (v1beta compatibility required for preview models).

## Weekly Schedule Format
The engine generates a 4-portion batch schedule:

| Day | Type | Activity |
| :--- | :--- | :--- |
| **Monday** | Cook | Primary Meal A (Portions x4) |
| **Tuesday** | Leftover | Meal A (Heated) |
| **Wednesday** | Cook | Primary Meal B (Portions x4) |
| **Thursday** | Leftover | Meal B (Heated) |
| **Fri-Sun** | Flexible | Pantry staples / Flexible meals |

---
**Status**: [STABLE] | **Emoji Policy**: [STRICT-NONE] | **Logging**: [PROFESSIONAL-ASCII]
