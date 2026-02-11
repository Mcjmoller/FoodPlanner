#  Automated Deployment Guide

This guide details how to configure the **Food Planner** to run automatically every Sunday at 09:00 AM using Windows Task Scheduler.

## 1. Prerequisites

Ensure the following files are present in `C:\Users\marcu\Desktop\Programming\Python\FoodPlaner`:
- `.venv` (Python Virtual Environment)
- `credentials.json` (Google Service Account Key)
- `.env` (Email Credentials)
- `run_planner.bat` (The automation wrapper)

## 2. Windows Task Scheduler Setup (Hardened)

We use Task Scheduler to run the script with high privileges and network checks.

### Step-by-Step Configuration

1.  **Open Task Scheduler**
    - Press `Win + R`, type `taskschd.msc`, and hit Enter.

2.  **Create Task**
    - Click **"Create Task..."** in the right-hand Actions pane.

3.  **General Tab**
    - **Name**: `FoodPlanner_Auto`
    - **Security Options**: Select **"Run with highest privileges"**.
    - **Configure for**: Windows 10/11.

4.  **Triggers Tab**
    - Click **New...**
    - **Begin the task**: On a schedule.
    - **Settings**: Weekly -> **Sunday**.
    - **Start**: `09:00:00`.
    - **Enabled**: Checked.

5.  **Actions Tab**
    - Click **New...**
    - **Action**: Start a program.
    - **Program/script**: `C:\Users\marcu\Desktop\Programming\Python\FoodPlaner\run_planner.bat`
    - **Start in (Optional)**: `C:\Users\marcu\Desktop\Programming\Python\FoodPlaner\`
      *(Crucial to ensure it finds credentials.json)*

6.  **Conditions Tab** (Reliability)
    - Check **"Start only if the following network connection is available"** -> Any connection.
    - Check **"Wake the computer to run this task"** (Optional, ensures it runs even if sleeping).

7.  **Settings Tab** (Error Handling)
    - Check **"If the task fails, restart every:"** -> `30 minutes`.
    - **Attempt to restart up to**: `3 times`.
    - Check **"Run task as soon as possible after a scheduled start is missed"**.

## 3. Monitoring & Maintenance

### Logs
- The script redirects all output (Success messages & Errors) to:
  `C:\Users\marcu\Desktop\Programming\Python\FoodPlaner\cron_log.txt`

### Health Check
Run the diagnostic tool to check status:
```bash
python diagnose_cron.py
```
This utility scans the log for specific success markers (`✅ PIPELINE COMPLETE`) and suggests fixes if missing.

### Updates
To migrate to a new machine:
1. Clone the repo.
2. Check `requirements.txt` and install dependencies.
3. Place `credentials.json` and `.env` in the folder.
4. Follow the Task Scheduler setup above.
