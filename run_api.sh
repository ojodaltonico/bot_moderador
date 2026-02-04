#!/bin/bash
cd /home/raspbery/services/bot_moderador
source venv/bin/activate
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

