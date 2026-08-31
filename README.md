# Bond Renewal Tool v5.2

v5.2 adds more robust OCR normalization, a conservative multi-signal matching audit trail, and exact Salesforce bond-number fallback when address matching fails. The runtime safety/review modes introduced in v5 remain intact.

The proven v4 workflow remains intact:
- OCR / PDF extraction
- Conservative address matching with normalization
- Exact Salesforce bond-number fallback when address matching fails
- Conservative DBA + Permit Name fallback
- Match confidence/method audit trail
- Salesforce Account matching
- PDF rename/move
- CSV output
- Direct Salesforce OAuth + PKCE
- Direct ContentVersion upload
- PDF duplicate protection
- Optional Salesforce Account field updates
- Data Loader CSV fallback


## Matching strategy

The tool uses a conservative matching hierarchy. It does not lower thresholds or guess simply to force a match:

1. **Address match** — exact normalized address first, then conservative fuzzy matching with the same house number.
2. **Exact Salesforce bond-number fallback** — if address matching fails, the normalized PDF bond number is compared with `Bond_Number__c`. The bond number must match exactly and uniquely.
3. **Name fallback** — if both address and bond-number matching fail, business-name evidence may be used. Automatic name fallback requires supporting DBA evidence **and** Permit Name evidence; DBA alone is not sufficient.
4. If the evidence is missing, conflicting, or ambiguous, the PDF remains **Unmatched** for manual review.

Bond-number matching is intentionally **not fuzzy**. A similar-looking number is not enough to select a Salesforce Account.

### Address normalization

Address comparison normalizes common formatting differences, including street suffixes and directions such as `Street`/`St`, `Road`/`Rd`, and `North`/`N`. U.S. highway variants such as `US HWY 50`, `US HIGHWAY 50`, `US ROUTE 50`, and `US-50` are normalized to a common form. Directional suffixes such as `RD NW` are supported during principal-address extraction.

### OCR normalization

The extraction layer includes narrow corrections for OCR errors observed on the bond continuation forms, including:

- continuation dates containing OCR substitutions such as `@` or `e` for `0`;
- the observed `16/01/...` month error where the form should read `10/01/...`;
- bond amounts such as `$100, e00` and the observed `$100,080` rendering of `$100,000`;
- Erie bond-number variants such as `94`, `094`, `994`, or `Q94` prefixes and punctuation such as `BOND NO,`;
- Erie bond numbers are normalized to the `Q94-#######` form when the recognized format is known.

Unknown formats are preserved rather than aggressively corrected.

## Match audit trail

`output\Raw_List.csv` records how each PDF was matched so results can be reviewed before Salesforce changes are allowed. Audit fields include:

- `Match Status`
- `Match Confidence`
- `Match Method`
- `Address Match`
- `Agency DBA Similarity` / `Agency DBA Containment`
- `Salesforce DBA Similarity` / `Salesforce DBA Containment`
- `Permit Name Similarity` / `Permit Name Containment`
- `Salesforce Match Status`
- `Match Detail`

Examples of `Match Method` include `Address - Exact Normalized`, `Address - Conservative Fuzzy`, `Bond Number - Exact Salesforce`, `Name Fallback`, and `No Match`.

A bond-number fallback can therefore produce a high-confidence match even when the bond's address cannot be matched, while `Address Match` remains `No` and `Match Detail` records both the failed address attempt and successful exact bond-number match.

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
5. Review `Raw_List.csv`, especially `Match Method`, `Match Detail`, and any `Unmatched` rows, plus the success/error files after the batch.


| Mode                    | Process PDFs | Generate CSVs | Upload PDF | Update SF Fields |
| ----------------------- | ------------ | ------------- | ---------- | ---------------- |
| **1 — Process Only**    | ✅            | ✅             | ❌          | ❌                |
| **2 — Upload Files**    | ✅            | ✅             | ✅          | ❌                |
| **3 — Full Automation** | ✅            | ✅             | ✅          | ✅                |
