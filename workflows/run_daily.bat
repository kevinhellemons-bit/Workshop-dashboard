@echo off
:: BBQ Experience Center – Dagelijkse bezettingsgraad refresh
:: Zet dit in als Windows Task Scheduler taak (dagelijks, bijv. 07:00)

cd /d "%~dp0"

echo [%DATE% %TIME%] Start dagelijkse refresh >> logs\run_daily.log

python tools\scrape_workshops.py >> logs\run_daily.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij scrapen >> logs\run_daily.log
    exit /b 1
)

python tools\update_database.py >> logs\run_daily.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij database update >> logs\run_daily.log
    exit /b 1
)

python tools\generate_dashboard.py >> logs\run_daily.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij dashboard genereren >> logs\run_daily.log
    exit /b 1
)

python tools\send_slack_digest.py >> logs\run_daily.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij Slack digest >> logs\run_daily.log
    exit /b 1
)

python tools\publish_to_apps_script.py >> logs\run_daily.log 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] FOUT bij publiceren Apps Script >> logs\run_daily.log
    exit /b 1
)

echo [%DATE% %TIME%] Refresh voltooid >> logs\run_daily.log
