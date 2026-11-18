#!/usr/bin/env bash
# One command on Mac/Linux: installs packages, then starts the planner.
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
python3 planner/selftest.py
python3 -m streamlit run planner/app.py
