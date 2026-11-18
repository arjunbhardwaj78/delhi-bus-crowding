@echo off
REM One command on Windows: installs packages, then starts the planner in your browser.
cd /d "%~dp0"
python -m pip install -r requirements.txt
python planner\selftest.py
python -m streamlit run planner\app.py
