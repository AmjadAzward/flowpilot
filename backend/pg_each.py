import glob, os, shutil, subprocess, sys, tempfile, time
import pixeltable_pgserver as pgserver
data = tempfile.mkdtemp(prefix="flowpilot-pg-")
server = pgserver.get_server(data, cleanup_mode="stop")
try:
    for f in sorted(glob.glob("tests/test_*.py")):
        env = {**os.environ, "FLOWPILOT_DATABASE_URL": server.get_uri()}
        # a fresh schema per file keeps files independent
        server.psql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        t = time.time()
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", f, "-q", "-x", "--no-header", "-p", "no:cacheprovider"], env=env, capture_output=True, text=True, timeout=300)
            tail = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:] 
            print(f"{f}: {tail[0] if tail else r.returncode} ({time.time()-t:.0f}s)", flush=True)
            if r.returncode: print("   ", "\n    ".join([l for l in r.stdout.splitlines() if l.startswith(("E ", "FAILED"))][:6]), flush=True)
        except subprocess.TimeoutExpired:
            print(f"{f}: TIMEOUT after 300s", flush=True)
finally:
    server.cleanup(); shutil.rmtree(data, ignore_errors=True)
