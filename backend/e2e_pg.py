"""Run the browser test suite against an app whose storage is a throwaway Postgres:  python e2e_pg.py
Needs: pip install pixeltable-pgserver, the frontend dev server on :5173, and port 8000 free."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pixeltable_pgserver as pgserver

data = tempfile.mkdtemp(prefix="flowpilot-pg-")
server = pgserver.get_server(data, cleanup_mode="stop")
env = {**os.environ, "FLOWPILOT_DATABASE_URL": server.get_uri(), "FLOWPILOT_ALLOW_PRIVATE_HOSTS": "1"}
api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], env=env)
try:
    for _ in range(40):
        try:
            print(urllib.request.urlopen("http://127.0.0.1:8000/api/health").read().decode()); break
        except Exception: time.sleep(0.5)
    code = subprocess.call("npm run test:e2e", cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"), shell=True)
finally:
    api.terminate(); api.wait()
    server.cleanup(); shutil.rmtree(data, ignore_errors=True)
sys.exit(code)
