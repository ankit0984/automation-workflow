import os
import sys
import time
import subprocess
import http.client
import json
import zipfile
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_MODEL_DIR = PROJECT_ROOT / "captcha_reader_model" / "model_training" / "checkpoints" / "best_hf_captcha_model"
DEFAULT_ZIP_PATH = PROJECT_ROOT / "captcha_reader_model" / "model_training" / "checkpoints" / "best_hf_captcha_model.zip"

# Configuration (can be overridden via env vars)
REPO = os.environ.get("REPO", "ankit0984/automation-workflow")
TAG_NAME = os.environ.get("TAG_NAME", "v1.0.0")
RELEASE_ID = os.environ.get("RELEASE_ID")  # Optional: auto-detected from TAG_NAME if not provided
ASSET_NAME = os.environ.get("ASSET_NAME", "best_hf_captcha_model.zip")
FILE_PATH = os.environ.get("FILE_PATH", str(DEFAULT_ZIP_PATH))


def get_github_token():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    # Try GitHub CLI (gh auth token)
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass

    # Try git credential helper with terminal prompt disabled to avoid hanging
    try:
        cmd = "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill"
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5, env=env)
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass

    return None


def ensure_zip_exists():
    """Create .zip using store mode (no compression) in 2 seconds instead of burning CPU."""
    if os.path.exists(FILE_PATH):
        return

    if not DEFAULT_MODEL_DIR.exists():
        print(f"Error: Model directory not found: {DEFAULT_MODEL_DIR}")
        sys.exit(1)

    print(f"Zip not found at {FILE_PATH}.")
    print(f"Packing {DEFAULT_MODEL_DIR} into {FILE_PATH} (fast store mode, no re-compression)...")
    start = time.time()
    with zipfile.ZipFile(FILE_PATH, "w", compression=zipfile.ZIP_STORED) as zf:
        for file in DEFAULT_MODEL_DIR.iterdir():
            if file.is_file() and not file.name.startswith("."):
                print(f"  -> Adding {file.name} ({file.stat().st_size / (1024 * 1024):.1f} MB)...")
                zf.write(file, arcname=file.name)
    elapsed = time.time() - start
    print(f"✓ Archive created in {elapsed:.1f}s at: {FILE_PATH}")


import urllib.request
import urllib.error


def github_api(method: str, path: str, token: str, data: dict = None):
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "UploadScript",
        "Accept": "application/vnd.github.v3+json",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            return response.status, json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(err_body)
        except Exception:
            parsed = {"message": err_body}
        return e.code, parsed


def get_or_create_release(token):
    global RELEASE_ID
    if RELEASE_ID:
        return int(RELEASE_ID)

    # 1. Try finding release by TAG_NAME
    status, data = github_api("GET", f"/repos/{REPO}/releases/tags/{TAG_NAME}", token)
    if status == 200:
        RELEASE_ID = data["id"]
        print(f"Found existing release for tag '{TAG_NAME}' (ID: {RELEASE_ID})")
        return RELEASE_ID

    # 2. If tag not found, check latest release
    status, data = github_api("GET", f"/repos/{REPO}/releases/latest", token)
    if status == 200:
        RELEASE_ID = data["id"]
        print(f"Found latest release '{data.get('tag_name')}' (ID: {RELEASE_ID})")
        return RELEASE_ID

    # 3. Create release if not found
    print(f"No release found for tag '{TAG_NAME}'. Creating release...")
    payload = {
        "tag_name": TAG_NAME,
        "name": f"Release {TAG_NAME}",
        "body": "Captcha model weights checkpoint",
        "draft": False,
        "prerelease": False
    }
    status, data = github_api("POST", f"/repos/{REPO}/releases", token, data=payload)
    if status in (200, 201):
        RELEASE_ID = data["id"]
        print(f"✓ Created release '{TAG_NAME}' (ID: {RELEASE_ID})")
        return RELEASE_ID
    else:
        print(f"Error creating release: HTTP {status}: {data}")
        sys.exit(1)


def check_existing_asset(token, release_id):
    status, assets = github_api("GET", f"/repos/{REPO}/releases/{release_id}/assets", token)
    if status == 200 and isinstance(assets, list):
        for asset in assets:
            if asset.get("name") == ASSET_NAME:
                asset_id = asset.get("id")
                print(f"Asset already exists (ID: {asset_id}), deleting old asset before re-upload...")
                del_status, _ = github_api("DELETE", f"/repos/{REPO}/releases/assets/{asset_id}", token)
                print(f"Delete status: {del_status}")
                time.sleep(1)
                break


def upload():
    token = get_github_token()
    if not token:
        print("Error: Could not obtain GitHub token from GITHUB_TOKEN, GH_TOKEN, gh CLI, or git credential.")
        print("Please export your token: export GITHUB_TOKEN='ghp_...'")
        sys.exit(1)

    # Ensure zip is built
    ensure_zip_exists()

    release_id = get_or_create_release(token)

    file_size = os.path.getsize(FILE_PATH)
    file_size_mb = file_size / (1024 * 1024)
    print(f"Target Repo: {REPO}")
    print(f"Release ID:  {release_id}")
    print(f"File:        {FILE_PATH}")
    print(f"Size:        {file_size_mb:.2f} MB ({file_size} bytes)")

    check_existing_asset(token, release_id)

    print(f"Connecting to uploads.github.com...")
    conn = http.client.HTTPSConnection("uploads.github.com", timeout=600)
    path = f"/repos/{REPO}/releases/{release_id}/assets?name={ASSET_NAME}"
    
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/zip",
        "Content-Length": str(file_size),
        "User-Agent": "UploadScript",
        "Accept": "application/vnd.github.v3+json"
    }

    conn.putrequest("POST", path)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders()

    print(f"Starting streamed upload to {path}...")
    chunk_size = 2 * 1024 * 1024  # 2MB chunks
    uploaded = 0
    start_time = time.time()
    last_print_time = start_time

    with open(FILE_PATH, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            conn.send(chunk)
            uploaded += len(chunk)
            
            now = time.time()
            if now - last_print_time >= 3 or uploaded == file_size:
                elapsed = max(now - start_time, 0.001)
                pct = (uploaded / file_size) * 100
                speed = (uploaded / (1024 * 1024)) / elapsed
                print(f"Progress: {uploaded / (1024 * 1024):.1f} / {file_size_mb:.1f} MB ({pct:.1f}%) - {speed:.2f} MB/s", flush=True)
                last_print_time = now

    print("Upload complete, waiting for GitHub response...")
    res = conn.getresponse()
    body = res.read().decode(errors="replace")
    print(f"Response status: {res.status} {res.reason}")

    if res.status in (200, 201):
        print("\nSUCCESS! Checkpoint asset successfully uploaded to GitHub Release.")
        data = json.loads(body)
        print(f"Download URL: {data.get('browser_download_url')}")
    else:
        print(f"Failed: {body}")
        sys.exit(1)


if __name__ == "__main__":
    upload()

