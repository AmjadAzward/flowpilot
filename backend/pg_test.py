"""Run the test suite (or any command) against a throwaway real Postgres that needs no installation:

    pip install pixeltable-pgserver
    python pg_test.py -m pytest tests -q

A temporary Postgres starts, FLOWPILOT_DATABASE_URL points at it for the command, and it stops afterwards.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pixeltable_pgserver as pgserver

data = tempfile.mkdtemp(prefix="flowpilot-pg-")
server = pgserver.get_server(data, cleanup_mode="stop")
try:
    env = {**os.environ, "FLOWPILOT_DATABASE_URL": server.get_uri()}
    code = subprocess.call([sys.executable, *sys.argv[1:]], env=env)
finally:
    server.cleanup()
    shutil.rmtree(data, ignore_errors=True)
sys.exit(code)
