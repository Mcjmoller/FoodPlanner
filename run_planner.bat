@echo off
:: Batch Wrapper for FoodPlanner Automation
:: Designed for Windows Task Scheduler
:: Uses --auto flag for headless execution

cd /d "C:\Users\marcu\Desktop\Programming\Python\FoodPlaner"

:: Set Automation Flag (Legacy env var for backwards compatibility)
set FOODPLANNER_AUTOMATED=1
set PYTHONIOENCODING=utf-8

:: Log Start Time
echo. >> cron_log.txt
echo [Running Planner at %DATE% %TIME%] >> cron_log.txt

:: Activate Virtual Environment
call .venv\Scripts\activate.bat

:: Run Python Script with --auto flag (suppresses prompts, logs to file)
python -X utf8 src/main.py --auto >> cron_log.txt 2>&1

:: Log End Time if successful or not (handled by script logging, but wrapper can add separator)
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Script exited with code %ERRORLEVEL% >> cron_log.txt
)

:: Deactivate
call .venv\Scripts\deactivate.bat

