"""Reset the FlowPilot owner account.

Run from the backend folder with the API stopped:

    .venv\\Scripts\\python.exe reset_password.py

This removes the owner account and all sessions (your workflows and run history are kept).
The next visit to the web app shows the "Create your account" screen again.
"""
import sqlite3
import sys

from app.main import DB_PATH

answer = input(f"This deletes the owner account in {DB_PATH}. Workflows are kept. Type 'reset' to continue: ")
if answer.strip().lower() != "reset":
    print("Cancelled.")
    sys.exit(1)

with sqlite3.connect(DB_PATH) as db:
    db.execute("DELETE FROM sessions")
    db.execute("DELETE FROM users")
print("Done. Open the web app to create a new account.")
