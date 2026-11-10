# SharePoint / OneDrive Push — Setup Guide

`push_to_sharepoint.py` uploads a local Excel file (for example `output/form10_data.xlsx` or
`data/form10_daily_records_summary.xlsx`) over an existing workbook in SharePoint or OneDrive.

It signs in as **your own Microsoft account**. Any workbook you can edit in the browser, the script can update.

> This is separate from the Google Sheets push (`push_to_sheet.py`). Google credentials
> (`service_account.json`) do not work with SharePoint, even if the email address is the same.

---

## How it works

1. **Sign in.** On the first run the script prints a short code. You enter it at
   <https://microsoft.com/devicelogin> and sign in. The login is saved to `ms_token_cache.json`,
   so later runs do not ask again.
2. **Find the workbook.** The script turns the workbook URL you give it into a Microsoft Graph file ID.
3. **Upload.** The local `.xlsx` is uploaded in 5 MB pieces and replaces the remote file's contents.
   The file keeps its URL, name, sharing settings and version history.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Microsoft account | A work/school (Microsoft 365) or personal account |
| Edit access to the workbook | Check that you can edit it in the browser |
| Python environment | The project's `captcha_reader_model/.venv` (managed with `uv`) |
| An Azure / Entra app registration | One-time, free, no client secret. See Step 1 |

---

## Step 1: Register an app in Microsoft Entra (one time)

Microsoft requires every script that signs in to have an app ID. You create one once and reuse it.

1. Open <https://entra.microsoft.com> and sign in with the account that owns or edits the workbook.
   For a personal account, <https://portal.azure.com> → **App registrations** works the same way.
2. Go to **Applications → App registrations → New registration**.
   - **Name:** for example `ecinet-sharepoint-push`
   - **Supported account types:**
     *Accounts in any organizational directory and personal Microsoft accounts*
   - **Redirect URI:** leave empty
   - Click **Register**.
3. On the app's **Overview** page, copy the **Application (client) ID**. You need it in Step 3.
4. Open **Authentication**. Under **Advanced settings**, set **Allow public client flows** to **Yes**,
   then click **Save**.
   *(The sign-in code flow fails without this setting.)*
5. Open **API permissions → Add a permission → Microsoft Graph → Delegated permissions**.
   Search for `Files.ReadWrite.All`, tick it, and click **Add permissions**.
   - **Work account:** if your organisation blocks users from approving apps themselves, ask an admin
     to click **Grant admin consent** on this page.
   - **Personal account:** nothing more to do. You approve it yourself at first sign-in.

You do **not** need to create a client secret or certificate.

---

## Step 2: Install the dependency

`msal` (Microsoft's sign-in library) is already listed in `captcha_reader_model/pyproject.toml`.
Sync the environment:

```bash
cd captcha_reader_model
uv sync
source .venv/bin/activate
```

---

## Step 3: Configure environment variables

| Variable | Required | Value |
|---|---|---|
| `MS_CLIENT_ID` | Yes | Application (client) ID from Step 1 |
| `SHAREPOINT_WORKBOOK_URL` | No | Default workbook URL, used when none is passed on the command line |
| `MS_TENANT_ID` | No | Default `common`. See [Tenant settings](#tenant-settings) |

For the current terminal only:

```bash
export MS_CLIENT_ID="00000000-0000-0000-0000-000000000000"
export SHAREPOINT_WORKBOOK_URL="https://<tenant>.sharepoint.com/:x:/r/sites/<site>/Shared%20Documents/form10_data.xlsx"
```

To keep the values for every terminal, add the same `export` lines to `~/.zshrc`, then run `source ~/.zshrc`.

---

## Step 4: Get the workbook URL

1. Create or upload the target workbook in SharePoint or OneDrive once. The script updates an
   existing file; it does not create new ones.
2. Get its link with either method:
   - **Recommended:** select the file → **Share → Copy link** (a link that people with edit access can use).
   - The browser address bar while the workbook is open in Excel for the web.
3. Use one workbook per local file. The master data and the daily summary each need their own workbook.

---

## Step 5: First run (sign in)

```bash
cd captcha_reader_model/data_processing
python3 push_to_sharepoint.py "<workbook url>"
```

The script prints something like:

```
============================================================
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code ABCD1234 to authenticate.
============================================================
```

Open the page, enter the code, sign in, and accept the permission prompt. The script then continues:

```
Signed in as:    you@example.com
Target workbook: form10_data.xlsx
  -> Uploaded 5,242,880 / 7,092,475 bytes
  -> Uploaded 7,092,475 / 7,092,475 bytes

============================================================
✓ Successfully updated SharePoint workbook!
============================================================
```

Later runs skip the sign-in step. Microsoft keeps the saved login valid for about 90 days if it is used.

---

## Usage

```bash
# Master Form 10 workbook (default file: output/form10_data.xlsx)
python3 push_to_sharepoint.py "<master workbook url>"

# Daily records summary
python3 push_to_sharepoint.py "<summary workbook url>" \
  --excel-path ../../data/form10_daily_records_summary.xlsx

# Use SHAREPOINT_WORKBOOK_URL instead of passing the URL
python3 push_to_sharepoint.py
```

### Options

| Option | Default | Description |
|---|---|---|
| `workbook_url` (positional) | `$SHAREPOINT_WORKBOOK_URL` | URL of the existing SharePoint/OneDrive workbook |
| `--excel-path` | `output/form10_data.xlsx` | Local `.xlsx` to upload |
| `--client-id` | `$MS_CLIENT_ID` | App (client) ID from Step 1 |
| `--tenant` | `$MS_TENANT_ID` or `common` | Which Microsoft directory to sign in against |
| `--token-cache` | `ms_token_cache.json` (next to the script) | Where the saved login is stored |

Exit code is `0` on success and `1` on any failure.

### Calling it from Python

```python
from pathlib import Path
from push_to_sharepoint import push_to_sharepoint

push_to_sharepoint(
    workbook_url="https://<tenant>.sharepoint.com/...",
    excel_path=Path("output/form10_data.xlsx"),
)
```

It returns `False` (and prints a warning) when `MS_CLIENT_ID` is missing or the Excel file does not exist.
It raises an exception for sign-in or upload errors.

---

## Tenant settings

| `MS_TENANT_ID` / `--tenant` | Use when |
|---|---|
| `common` (default) | The app allows both work and personal accounts (Step 1 setting) |
| `organizations` | Work/school accounts only |
| `consumers` | Personal Microsoft accounts only |
| `<tenant-id GUID>` | The app was registered as *single tenant* (only accounts in your organisation) |

If you registered the app as single tenant, you **must** set `MS_TENANT_ID` to the **Directory (tenant) ID**
from the app's Overview page.

---

## Troubleshooting

| Message | Cause | Fix |
|---|---|---|
| `MS_CLIENT_ID is not set. Skipping SharePoint upload` | Environment variable missing | Step 3 |
| `AADSTS7000218 … client_assertion or client_secret` | Public client flows disabled | Step 1.4: set **Allow public client flows** to **Yes** |
| `AADSTS65001` / `AADSTS90094` / "Need admin approval" | Organisation requires an admin to approve apps | Ask an admin to **Grant admin consent** for `Files.ReadWrite.All` |
| `AADSTS700016 … not found in the directory` | Wrong client ID, or wrong account type for the tenant setting | Check `MS_CLIENT_ID`; see [Tenant settings](#tenant-settings) |
| `AADSTS50020` / `AADSTS50194` | App is single-tenant but `tenant=common` | Set `MS_TENANT_ID` to your tenant ID |
| `Could not resolve workbook URL (HTTP 403)` | Your account cannot edit the file | Ask the owner for **Edit** access |
| `Could not resolve workbook URL (HTTP 404)` | Bad or expired link | Copy a fresh link (Step 4) |
| `Workbook is locked` (HTTP 423) | The file is checked out or open for editing | Close it in Excel / the browser and run again |
| Signed in as the wrong account | Old saved login | Delete `ms_token_cache.json` and run again |

---

## Security notes

- `ms_token_cache.json` holds a refresh token that can read and write **your** files.
  - It is created with owner-only permissions (`chmod 600`).
  - It is listed in `.gitignore`, so it is never committed.
  - Do not share or copy it to other machines.
- The script asks for one permission only: `Files.ReadWrite.All`. It acts as you and can only reach
  files your account can already reach.
- To revoke access:
  - **Personal account:** <https://account.live.com/consent/Manage>.
  - **Work account:** <https://myapps.microsoft.com> → the app → revoke, or delete the app registration.

---

## Limitations

- **Full replace.** Each upload overwrites the whole workbook. Edits made directly in the SharePoint copy are
  lost on the next push. Older versions stay available under **Version history** in SharePoint/OneDrive.
- **Interactive first sign-in.** The device code step needs a person, so this does not run unattended in
  GitHub Actions yet. Unattended runs need an app-only setup (client secret or certificate plus admin consent).
- **Existing files only.** The target workbook must already exist at the URL.
