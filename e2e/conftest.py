"""
End-to-end fixtures: a real backend (uvicorn) on a throwaway SQLite database
initialised with scripts/setup_demo.py, and the production frontend build
served by `vite preview` (which proxies API paths to that backend).

    python -m pytest e2e -q              # from the repository root
Env:
    E2E_PYTHON       interpreter for the backend (default: this one; use .venv\\Scripts\\python.exe)
    E2E_BROWSER      chrome (default) | msedge | chromium (Playwright-managed)
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "e2e", ".artifacts")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait(url, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {url}")


@pytest.fixture(scope="session")
def stack():
    os.makedirs(ART, exist_ok=True)
    py = os.environ.get("E2E_PYTHON", sys.executable)
    tmp = tempfile.mkdtemp(prefix="clearclaim_e2e_")
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp}/e2e.db".replace("\\", "/"), APP_ENV="test",
               AUTO_MIGRATE="true", PYTHONIOENCODING="utf-8")
    env.pop("CLEARCLAIM_WRITE_TOKEN", None)
    subprocess.run([py, "scripts/setup_demo.py", "--reset"], cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api_port, web_port = _free_port(), _free_port()
    env["CORS_ORIGINS"] = f"http://127.0.0.1:{web_port}"
    backend = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                               cwd=os.path.join(ROOT, "backend"), env=env,
                               stdout=open(os.path.join(ART, "backend.log"), "w"), stderr=subprocess.STDOUT)
    npx = shutil.which("npx") or "npx"
    fe_env = dict(os.environ, VITE_BACKEND_URL=f"http://127.0.0.1:{api_port}")
    subprocess.run([npx, "vite", "build"], cwd=os.path.join(ROOT, "frontend"), env=fe_env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=os.name == "nt")
    web = subprocess.Popen([npx, "vite", "preview", "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"],
                           cwd=os.path.join(ROOT, "frontend"), env=fe_env, shell=os.name == "nt",
                           stdout=open(os.path.join(ART, "preview.log"), "w"), stderr=subprocess.STDOUT)
    try:
        _wait(f"http://127.0.0.1:{api_port}/api/v1/health")
        _wait(f"http://127.0.0.1:{web_port}/")
        yield dict(api=f"http://127.0.0.1:{api_port}", web=f"http://127.0.0.1:{web_port}")
    finally:
        for p in (web, backend):
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            else:
                p.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    choice = os.environ.get("E2E_BROWSER", "chrome")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel=None if choice == "chromium" else choice)
        yield b
        b.close()


@pytest.fixture()
def page(browser, stack):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.console_errors = []
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.console_errors.append(str(e)))
    yield pg
    ctx.close()
