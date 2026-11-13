#!/usr/bin/env python3
"""
SharePoint / OneDrive Excel Publisher (Microsoft Graph, delegated sign-in)
==========================================================================
Uploads a local .xlsx (e.g. form10_data.xlsx or form10_daily_records_summary.xlsx)
over an existing workbook in SharePoint / OneDrive, addressed by its URL.

Authentication uses YOUR Microsoft account via device code sign-in:
  - First run prints a code -> open https://microsoft.com/devicelogin, enter it, sign in.
  - The refresh token is cached in ms_token_cache.json, so later runs are silent.
  - The script can update any workbook your account can edit.

One-time setup (Azure / Entra app registration, no client secret needed):
  1. https://entra.microsoft.com -> App registrations -> New registration
       Supported account types: "Accounts in any organizational directory and personal Microsoft accounts"
  2. Authentication -> Advanced settings -> "Allow public client flows" = Yes
  3. API permissions -> Microsoft Graph -> Delegated -> Files.ReadWrite.All
  4. Copy the "Application (client) ID" and export it:
       export MS_CLIENT_ID="<client-id>"

Usage:
    python push_to_sharepoint.py "<workbook url>"
    python push_to_sharepoint.py "<workbook url>" --excel-path output/form10_daily_records_summary.xlsx
    SHAREPOINT_WORKBOOK_URL="<workbook url>" python push_to_sharepoint.py
"""

import os
import sys
import base64
import argparse
from pathlib import Path

import msal
import requests

SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_EXCEL_PATH = SCRIPT_DIR / "output" / "form10_data.xlsx"
DEFAULT_TOKEN_CACHE = SCRIPT_DIR / "ms_token_cache.json"

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
SCOPES = ["Files.ReadWrite.All"]

# Graph requires upload chunks to be multiples of 320 KiB
UPLOAD_CHUNK_SIZE = 320 * 1024 * 16  # 5 MiB


def load_token_cache(cache_path: Path) -> msal.SerializableTokenCache:
    """Load MSAL token cache from disk."""
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text(encoding="utf-8"))
    return cache


def save_token_cache(cache_path: Path, cache: msal.SerializableTokenCache):
    """Persist MSAL token cache (owner read/write only)."""
    if cache.has_state_changed:
        cache_path.write_text(cache.serialize(), encoding="utf-8")
        os.chmod(cache_path, 0o600)


def get_access_token(client_id: str, tenant: str, cache_path: Path) -> str:
    """Return a Graph access token, silently from cache or via device code sign-in."""
    cache = load_token_cache(cache_path)
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Could not start device code sign-in: {flow.get('error_description', flow)}")
        print("\n" + "=" * 60)
        print(flow["message"])
        print("=" * 60 + "\n")
        result = app.acquire_token_by_device_flow(flow)

    save_token_cache(cache_path, cache)

    if "access_token" not in result:
        raise RuntimeError(f"Sign-in failed: {result.get('error')}: {result.get('error_description')}")

    account = (result.get("id_token_claims") or {}).get("preferred_username")
    if account:
        print(f"Signed in as:    {account}")
    return result["access_token"]


def encode_sharing_url(url: str) -> str:
    """Encode a SharePoint/OneDrive URL into a Graph /shares id (u!<base64url>)."""
    encoded = base64.urlsafe_b64encode(url.strip().encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{encoded}"


def resolve_drive_item(session: requests.Session, workbook_url: str) -> dict:
    """Resolve a workbook URL to its Graph driveItem (drive id + item id)."""
    resp = session.get(f"{GRAPH_ROOT}/shares/{encode_sharing_url(workbook_url)}/driveItem")
    if resp.status_code != 200:
        raise RuntimeError(
            f"Could not resolve workbook URL (HTTP {resp.status_code}): {resp.text}\n"
            "Check that the URL opens the workbook in your browser and your account has Edit access."
        )
    return resp.json()


def upload_workbook(session: requests.Session, item: dict, excel_path: Path) -> dict:
    """Replace the remote workbook content with the local file using a resumable upload session."""
    drive_id = item["parentReference"]["driveId"]
    item_id = item["id"]

    resp = session.post(
        f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/createUploadSession",
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
    )
    if resp.status_code == 423:
        raise RuntimeError("Workbook is locked (someone may have it open for editing). Close it and retry.")
    resp.raise_for_status()
    upload_url = resp.json()["uploadUrl"]

    total_size = excel_path.stat().st_size
    with open(excel_path, "rb") as f:
        start = 0
        while start < total_size:
            chunk = f.read(UPLOAD_CHUNK_SIZE)
            end = start + len(chunk) - 1
            # Upload URL is pre-authenticated; it must NOT receive the Authorization header
            put = requests.put(
                upload_url,
                data=chunk,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{total_size}",
                },
            )
            if put.status_code == 423:
                requests.delete(upload_url)
                raise RuntimeError("Workbook is locked (someone may have it open for editing). Close it and retry.")
            if put.status_code not in (200, 201, 202):
                requests.delete(upload_url)
                raise RuntimeError(f"Upload failed at bytes {start}-{end} (HTTP {put.status_code}): {put.text}")
            print(f"  -> Uploaded {end + 1:,} / {total_size:,} bytes")
            start = end + 1

    return put.json()


def push_to_sharepoint(
    workbook_url: str,
    excel_path: Path = DEFAULT_EXCEL_PATH,
    client_id: str | None = None,
    tenant: str = "common",
    token_cache: Path = DEFAULT_TOKEN_CACHE,
) -> bool:
    """Upload excel_path over the SharePoint/OneDrive workbook at workbook_url."""
    client_id = client_id or os.environ.get("MS_CLIENT_ID")
    if not client_id:
        print("Warning: MS_CLIENT_ID is not set. Skipping SharePoint upload (see setup steps in push_to_sharepoint.py).", file=sys.stderr)
        return False

    if not excel_path.exists():
        print(f"Error: Excel file not found at: {excel_path.resolve()}", file=sys.stderr)
        return False

    print("=" * 60)
    print("SharePoint / OneDrive Excel Publisher")
    print("=" * 60)
    print(f"Local Excel:     {excel_path.name} ({excel_path.stat().st_size:,} bytes)")

    token = get_access_token(client_id, tenant, token_cache)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    item = resolve_drive_item(session, workbook_url)
    print(f"Target workbook: {item.get('name')}")

    uploaded = upload_workbook(session, item, excel_path)

    print("\n" + "=" * 60)
    print("✓ Successfully updated SharePoint workbook!")
    print("=" * 60)
    print(f"File:     {uploaded.get('name')}")
    print(f"Modified: {uploaded.get('lastModifiedDateTime')}")
    print(f"Live URL: {uploaded.get('webUrl')}")
    print("=" * 60)
    return True


def main():
    parser = argparse.ArgumentParser(description="Upload a local Excel workbook over an existing SharePoint/OneDrive workbook.")
    parser.add_argument(
        "workbook_url",
        nargs="?",
        default=os.environ.get("SHAREPOINT_WORKBOOK_URL"),
        help="SharePoint/OneDrive workbook URL (or set SHAREPOINT_WORKBOOK_URL)",
    )
    parser.add_argument(
        "--excel-path",
        type=Path,
        default=DEFAULT_EXCEL_PATH,
        help="Local .xlsx file to upload",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Azure app (client) ID (or set MS_CLIENT_ID)",
    )
    parser.add_argument(
        "--tenant",
        type=str,
        default=os.environ.get("MS_TENANT_ID", "common"),
        help="Tenant: 'common' (work + personal), 'consumers', 'organizations', or a tenant ID",
    )
    parser.add_argument(
        "--token-cache",
        type=Path,
        default=DEFAULT_TOKEN_CACHE,
        help="Path to cached Microsoft sign-in tokens",
    )
    args = parser.parse_args()

    if not args.workbook_url:
        parser.error("workbook_url is required (or set SHAREPOINT_WORKBOOK_URL)")

    try:
        ok = push_to_sharepoint(
            workbook_url=args.workbook_url,
            excel_path=args.excel_path,
            client_id=args.client_id,
            tenant=args.tenant,
            token_cache=args.token_cache,
        )
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
