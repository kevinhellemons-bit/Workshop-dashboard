@echo off
:: BBQ Experience Center – Wekelijkse Slack actiemelding (elke maandag 08:00)

cd /d "%~dp0"

echo [%DATE% %TIME%] Start weekly Slack >> logs\run_weekly.log

python tools\scrape_workshops.py >> logs\run_weekly.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij scrapen >> logs\run_weekly.log
    exit /b 1
)

python tools\update_database.py >> logs\run_weekly.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij database update >> logs\run_weekly.log
    exit /b 1
)

python tools\send_slack_weekly.py >> logs\run_weekly.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij weekly Slack >> logs\run_weekly.log
    exit /b 1
)

echo [%DATE% %TIME%] Weekly Slack verstuurd >> logs\run_weekly.log
