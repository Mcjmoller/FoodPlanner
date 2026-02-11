@echo off
:: Batch Wrapper for FoodPlanner Automation
:: Designed for Windows Task Scheduler
:: Sets environment variables and logs output

cd /d "C:\Users\marcu\Desktop\Programming\Python\FoodPlaner"

:: Set Automation Flag (Script checks this to skip input prompts)
set FOODPLANNER_AUTOMATED=1
set PYTHONIOENCODING=utf-8

:: Log Start Time
echo. >> cron_log.txt
echo [Running Planner at %DATE% %TIME%] >> cron_log.txt

:: Activate Virtual Environment
call .venv\Scripts\activate.bat

:: Run Python Script with Output Redirection (Stdout + Stderr)
:: Uses -u to unbuffer output if needed, but PYTHONIOENCODING handles encoding
python -X utf8 foodPlaner_cloud.py >> cron_log.txt 2>&1

:: Log End Time if successful or not (handled by script logging, but wrapper can add separator)
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Script exited with code %ERRORLEVEL% >> cron_log.txt
)

:: Deactivate
call .venv\Scripts\deactivate.bat
