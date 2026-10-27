import os
import sys
import time
import subprocess
import http.client
import json
from urllib.parse import urlparse

RELEASE_ID = 385304345
REPO = "ankit0984/ecinet-automation"
ASSET_NAME = "best_hf_captcha_model.zip"
FILE_PATH = os.path.abspath("captcha_reader_model/model_training/checkpoints/best_hf_captcha_model.zip")

def get_github_token():
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    cmd = "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill"
    proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return None

def check_existing_asset(token):
    conn = http.client.HTTPSConnection("api.github.com")
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "UploadScript",
        "Accept": "application/vnd.github.v3+json"
    }
    conn.request("GET", f"/repos/{REPO}/releases/{RELEASE_ID}/assets", headers=headers)
    res = conn.getresponse()
    if res.status == 200:
        assets = json.loads(res.read().decode())
        for asset in assets:
            if asset.get("name") == ASSET_NAME:
                asset_id = asset.get("id")
                print(f"Asset already exists (ID: {asset_id}), deleting old asset before re-upload...")
                del_conn = http.client.HTTPSConnection("api.github.com")
                del_conn.request("DELETE", f"/repos/{REPO}/releases/assets/{asset_id}", headers=headers)
                del_res = del_conn.getresponse()
                print(f"Delete status: {del_res.status}")
                time.sleep(1)
                break
    conn.close()

def upload():
    token = get_github_token()
    if not token:
        print("Error: Could not obtain GitHub token from git credentials or environment.")
        sys.exit(1)

    if not os.path.exists(FILE_PATH):
        print(f"Error: File not found: {FILE_PATH}")
        sys.exit(1)

    file_size = os.path.getsize(FILE_PATH)
    file_size_mb = file_size / (1024 * 1024)
    print(f"File: {FILE_PATH}")
    print(f"Size: {file_size_mb:.2f} MB ({file_size} bytes)")

    check_existing_asset(token)

    print(f"Connecting to uploads.github.com...")
    conn = http.client.HTTPSConnection("uploads.github.com", timeout=600)
    path = f"/repos/{REPO}/releases/{RELEASE_ID}/assets?name={ASSET_NAME}"
    
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
