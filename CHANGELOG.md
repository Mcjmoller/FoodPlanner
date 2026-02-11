# Changelog

## [v2.0.0-stable] - 2026-02-11

### Added
- **Fuzzy Matching Engine**: Replaces OpenAI/Gemini dependency with `thefuzz` for deterministic, cost-effective meal planning.
- **Jinja2 Email Templates**: New HTML email format with badges ("Cooking Day", "Leftovers") and FODMAP-safe styling.
- **FODMAP Filters**: Explicit prioritization of safe ingredients (Potatoes, Rice) and exclusion of high-FODMAP ones.
- **Scraper Enhancements**: Pattern-agnostic price parsing (handles "DKK 10" and "10 kr") and cookie concent dismissal logic.
- **Mock Data Mode**: `USE_MOCK_DATA` flag for testing empty lists.

### Removed
- **AI Dependency**: Removed `google-generativeai` and `google.genai` SDKs completely.
- **API Key Management**: Cleaned up rotation logic and `GEMINI_API_KEYS` usage.
- **Deprecated SDK Calls**: Removed old `genai.configure()` calls.

### Fixed
- **Gspread Deprecation Warnings**: Updated all `worksheet.update()` calls to use keyword arguments (`range_name=...`).
- **Empty List Handling**: Script now gracefully handles empty buying lists by logging warnings or using mock data.
- **Scraper Resiliency**: Logs raw HTML snippets when 0 deals are found for easier debugging.
