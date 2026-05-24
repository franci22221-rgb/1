@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt -q
python hydra_companion.py
pause
