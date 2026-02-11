# Food Planner - Batch Cooking (Rule-Based)

A robust, AI-free meal planning system designed for efficiency and gut health. This tool scrapes weekly deals from major Danish supermarkets (REMA 1000, Netto, 365 Discount, Lidl), matches them against your buying list and pantry, and generates a batched meal plan to minimize cooking time.

## Key Features

*   **Rule-Based Matching Engine**: Uses fuzzy logic (thefuzz) to identify the best deals without hallucinations.
*   **2x Weekly Batch Cooking**: Optimized schedule (Cook Mon/Wed, eat leftovers Tue/Thu/Fri) to save time.
*   **Low-FODMAP Filter**: Prioritizes gut-friendly ingredients (Potatoes, Rice) and flags high-FODMAP items (Onion, Wheat).
*   **Scraped Prices**: Fetches real-time prices from etilbudsavis.dk and coop.dk.
*   **Responsive Email Reports**: Sends a professional HTML meal plan with "Cooking Day" badges and grouped shopping lists.
*   **Google Sheets Integration**: Syncs directly with your Food Planner spreadsheet.

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/food-planner.git
    cd food-planner
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

3.  **Configuration**:
    *   Create a .env file with your email credentials (EMAIL_ADDRESS, EMAIL_PASSWORD, etc.).
    *   Place your Google Service Account credentials.json in the root folder.

## Usage

Run the script manually or via cron:

```bash
python foodPlaner_cloud.py
```

### Mock Data Mode
To test the pipeline with dummy data (if you don't want to type into Sheets), set USE_MOCK_DATA = True in foodPlaner_cloud.py.

## Architecture

*   **Engine**: Python 3.12 + thefuzz (Levenshtein Distance)
*   **Scraper**: Microsoft Playwright (Headless Chromium)
*   **Database**: Google Sheets (via gspread v6.1.2)
*   **Templating**: Jinja2 (HTML Email)

## License
MIT
