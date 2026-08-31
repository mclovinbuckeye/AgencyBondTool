# Bond Renewal Tool v5

v5 adds the final safety/review layer: choose what the tool is allowed to do each time you run it.

The proven v4 workflow remains intact:
- OCR / PDF extraction
- Agency matching
- Salesforce Account matching
- PDF rename/move
- CSV output
- Direct Salesforce OAuth + PKCE
- Direct ContentVersion upload
- PDF duplicate protection
- Optional Salesforce Account field updates
- Data Loader CSV fallback

## Run modes

Run:

```powershell
.\.venv\Scripts\python.exe .\bond_processor.py
```

You will get this menu:

```text
1) PROCESS ONLY
   Extract, match, rename/move PDFs, and generate CSVs.
   No Salesforce records are changed.

2) UPLOAD FILES
   Everything above + upload matched PDFs to Salesforce.
   Account bond fields are NOT changed.

3) FULL AUTOMATION
   Everything above + upload PDFs + update:
     Bond_Amount__c
     Bond_Expiration__c
     Bond_Number__c

0) Cancel
```

FULL AUTOMATION requires you to type `YES` before processing starts.

## Direct command modes

You can also skip the menu:

```powershell
# Local processing only
.\.venv\Scripts\python.exe .\bond_processor.py process

# Process + upload PDFs
.\.venv\Scripts\python.exe .\bond_processor.py upload

# Process + upload PDFs + update bond fields
.\.venv\Scripts\python.exe .\bond_processor.py full
```

`full` still asks for `YES`.

For a future trusted unattended/scheduled job only:

```powershell
.\.venv\Scripts\python.exe .\bond_processor.py full --yes
```

Do not use `--yes` until you are comfortable with the extraction and matching results over real batches.

## PowerShell wrapper

`run.ps1` forwards command-line arguments, so after unblocking it you can use:

```powershell
Unblock-File .\run.ps1
Unblock-File .\salesforce-login.ps1

.\run.ps1
.\run.ps1 process
.\run.ps1 upload
.\run.ps1 full
```

If your PowerShell policy still blocks scripts, use the direct Python commands above.

## Salesforce configuration

Copy the example if you do not already have `config.json`:

```powershell
Copy-Item .\config.example.json .\config.json
```

Configure:

```json
{
  "tesseract_path": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
  "salesforce": {
    "enabled": true,
    "my_domain_url": "https://YOUR-MY-DOMAIN.my.salesforce.com",
    "consumer_key": "YOUR_CONSUMER_KEY",
    "api_version": "67.0",
    "callback_port": 7171
  }
}
```

Do not put a Salesforce password or Consumer Secret in the project.

The OAuth refresh token is stored in the Windows credential store using Python `keyring`.

## First / renewed Salesforce authorization

Close Data Loader so port 7171 is free, then run:

```powershell
.\.venv\Scripts\python.exe .\salesforce_oauth.py --login
```

or, if PowerShell allows the wrapper:

```powershell
.\salesforce-login.ps1
```

## Folder layout

```text
BondRenewalTool/
├── bond_processor.py
├── salesforce_client.py
├── salesforce_oauth.py
├── config.example.json
├── requirements.txt
├── run.ps1
├── salesforce-login.ps1
├── input/
├── processed/
├── unmatched/
├── reference/
│   ├── Agency Admin.csv
│   └── Salesforce_Export.csv
└── output/
```

Keep your real `config.json`, reference CSVs, processed files, and output history when upgrading.

## Salesforce fields changed by FULL mode

Only these Account fields are updated:

- `Bond_Amount__c`
- `Bond_Expiration__c`
- `Bond_Number__c`

The tool intentionally does not update `DBA__c`, `Insured_By__c`, or unrelated Account fields.

## Duplicate PDF protection

A successful direct upload is recorded in:

```text
output\Salesforce_Upload_State.json
```

The key combines the target Salesforce Account ID and the PDF SHA-256 hash. Reprocessing the same exact PDF for the same Account therefore skips another file upload.

## Data Loader fallback

The tool still generates:

```text
output\Salesforce_File_Upload.csv
```

with:

```text
Title                   -> Title
PathOnClient            -> PathOnClient
VersionData             -> VersionData
FirstPublishLocationId  -> FirstPublishLocationId
```

CSV output uses plain UTF-8 without a BOM, so the Data Loader `Title` header stays clean.

## Recommended everyday workflow

1. Refresh `reference\Agency Admin.csv` and `reference\Salesforce_Export.csv` when needed.
2. Put renewal PDFs into `input`.
3. Run the tool.
4. Choose:
   - `1` while reviewing extraction/matching behavior,
   - `2` when you want document uploads only,
   - `3` for the complete production workflow.
5. Review `Raw_List.csv` and the success/error files after the batch.


| Mode                    | Process PDFs | Generate CSVs | Upload PDF | Update SF Fields |
| ----------------------- | ------------ | ------------- | ---------- | ---------------- |
| **1 — Process Only**    | ✅            | ✅             | ❌          | ❌                |
| **2 — Upload Files**    | ✅            | ✅             | ✅          | ❌                |
| **3 — Full Automation** | ✅            | ✅             | ✅          | ✅                |
