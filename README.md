# Food Planner Cloud ☁️🥗

A smart food planning assistant that finds the best grocery deals, generates a meal plan using AI, and syncs everything to Google Sheets and your email.

## Features
- **Deal Scraping**: Checks specific stores (REMA 1000, Netto, etc.) for offers.
- **AI Planning**: Uses Google Gemini to create a meal plan based on deals + your preferences.
- **Google Sheets Sync**:
  - `MealPlan`: Viewing the generated plan.
  - `BuyingList`: Add items you want to buy (from your phone).
  - `PantryList`: Add items you already have.
- **Email Notifications**: received a formatted HTML email with your plan.
- **Automated**: Runs automatically via GitHub Actions (or locally).

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
2.  **Environment Variables**: Create a `.env` file:
    ```ini
    GEMINI_API_KEY=your_key
    EMAIL_ADDRESS=your_email
    EMAIL_PASSWORD=your_app_password
    EMAIL_RECEIVER=recipient_email
    ```
3.  **Google Cloud Setup**:
    - Place your Service Account JSON key as `credentials.json`.
    - Share your "Food Planner" Sheet with the service account email.

## Running

**Cloud Mode (Google Sheets integration):**
```bash
python foodPlaner_cloud.py
```

**Local Mode (File-based):**
```bash
python foodPlaner.py
```

## Hosting (GitHub Actions)
The workflow in `.github/workflows/run-planner.yml` runs automatically every Sunday at 8:00 UTC. Ensure you add your secrets to the GitHub Repository settings.
