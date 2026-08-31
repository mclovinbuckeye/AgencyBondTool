from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import fitz
from PIL import Image
import pytesseract

PROJECT_DIR = Path(__file__).resolve().parent
INPUT_DIR = PROJECT_DIR / "input"
PROCESSED_DIR = PROJECT_DIR / "processed"
UNMATCHED_DIR = PROJECT_DIR / "unmatched"
REFERENCE_DIR = PROJECT_DIR / "reference"
OUTPUT_DIR = PROJECT_DIR / "output"

AGENCY_CSV = REFERENCE_DIR / "Agency Admin.csv"
SALESFORCE_CSV = REFERENCE_DIR / "Salesforce_Export.csv"

RAW_CSV = OUTPUT_DIR / "Raw_List.csv"
SF_UPDATE_CSV = OUTPUT_DIR / "Salesforce_Update.csv"
SF_UPLOAD_CSV = OUTPUT_DIR / "Salesforce_File_Upload.csv"
UPLOAD_STATE = OUTPUT_DIR / "Salesforce_Upload_State.json"
UPLOAD_SUCCESS = OUTPUT_DIR / "Salesforce_File_Upload_Success.csv"
UPLOAD_ERRORS = OUTPUT_DIR / "Salesforce_File_Upload_Errors.csv"

RAW_FIELDS = [
    "Salesforce Id",
    "Agency ID",
    "Business Name",
    "Address",
    "Effective Date",
    "Effective To",
    "Bond Amount",
    "Bond Number",
    "Source Document",
    "Final Document Name",
    "Match Status",
    "Match Confidence",
    "Match Method",
    "Address Match",
    "Agency DBA Similarity",
    "Agency DBA Containment",
    "Salesforce DBA Similarity",
    "Salesforce DBA Containment",
    "Permit Name Similarity",
    "Permit Name Containment",
    "Salesforce Match Status",
    "Match Detail",
]

SF_UPDATE_FIELDS = [
    "Id",
    "Agency_Number__c",
    "Bond_Amount__c",
    "Bond_Expiration__c",
    "Bond_Number__c",
]

SF_UPLOAD_FIELDS = [
    "Title",
    "PathOnClient",
    "VersionData",
    "FirstPublishLocationId",
]


def ensure_dirs():
    for p in (INPUT_DIR, PROCESSED_DIR, UNMATCHED_DIR, REFERENCE_DIR, OUTPUT_DIR):
        p.mkdir(parents=True, exist_ok=True)


def load_config():
    path = PROJECT_DIR / "config.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def configure_tesseract(cfg):
    custom = cfg.get("tesseract_path")
    if custom:
        pytesseract.pytesseract.tesseract_cmd = custom


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_address(s: str) -> str:
    s = (s or "").upper()
    # Normalize U.S. highway variants.
    s = re.sub(r"\bUS[\s-]*(?:HWY|HIGHWAY|RTE|ROUTE)?[\s-]*(\d+[A-Z]?)\b", r"US \1", s)
    s = s.replace("&", " AND ")
    s = re.sub(r"[^\w\s]", " ", s)
    replacements = {
        r"\bROAD\b": "RD",
        r"\bSTREET\b": "ST",
        r"\bAVENUE\b": "AVE",
        r"\bBOULEVARD\b": "BLVD",
        r"\bDRIVE\b": "DR",
        r"\bLANE\b": "LN",
        r"\bCOURT\b": "CT",
        r"\bHIGHWAY\b": "HWY",
        r"\bROUTE\b": "RTE",
        r"\bNORTH\b": "N",
        r"\bSOUTH\b": "S",
        r"\bEAST\b": "E",
        r"\bWEST\b": "W",
        r"\bSUITE\b": "STE",
        r"\bAPARTMENT\b": "APT",
    }
    for pattern, repl in replacements.items():
        s = re.sub(pattern, repl, s)
    return normalize_spaces(s)

def normalize_ocr_date(date_text: str) -> str:
    date_text = normalize_spaces(date_text)

    # Narrow OCR repairs observed in Erie continuation forms.
    date_text = date_text.replace("@", "0")

    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_text)
    if not m:
        return date_text

    month, day, year = m.groups()
    month_num = int(month)

    if month_num > 12 and month == "16":
        month = "10"

    return f"{month}/{day}/{year}"

def normalize_bond_number(bond_no: str) -> str:
    bond_no = normalize_spaces(bond_no).upper()

    if not bond_no:
        return ""

    # Erie bond-number OCR variants we've observed:
    # Q94 5171098 C -> Q94-5171098
    # 994 5171098 C -> Q94-5171098
    # 94  5171099 C -> Q94-5171099
    m = re.fullmatch(
        r"(?:Q94|994|094|94)\s+(\d{7})\s*C?",
        bond_no,
        flags=re.I,
    )

    if m:
        return f"Q94-{m.group(1)}"

    # Unknown format: preserve the extracted value rather than guessing.
    return bond_no
    
def normalize_bond_amount(amount_text: str) -> str:
    amount_text = normalize_spaces(amount_text)

    if not amount_text:
        return ""

    # Known Erie OCR error:
    # $100,080 -> $100,000
    amount_text = re.sub(
        r"\$?\s*100,080\b",
        "$100,000",
        amount_text,
    )

    # Known OCR mistakes inside numeric bond amounts.
    amount_text = re.sub(
        r"(?<=[\d,])\s*[eE@]\s*(?=\d)",
        "0",
        amount_text,
    )

    # Remove spaces OCR may insert inside the number.
    amount_text = re.sub(
        r"(?<=\d)\s+(?=\d)",
        "",
        amount_text,
    )

    m = re.search(
        r"\$?\s*([\d,]+(?:\.\d{2})?)",
        amount_text,
    )

    if not m:
        return ""

    value = m.group(1).split(".")[0]

    return "$" + value

def normalize_business_name(s: str) -> str:
    s = (s or "").upper()
    s = s.replace("&", " AND ")
    s = re.sub(r"\bD\s*/?\s*B\s*/?\s*A\b", " DBA ", s)
    s = re.sub(r"[^\w\s]", " ", s)

    number_replacements = {
        r"\bONE\b": "1",
        r"\bTWO\b": "2",
        r"\bTHREE\b": "3",
        r"\bFOUR\b": "4",
        r"\bFIVE\b": "5",
        r"\bSIX\b": "6",
        r"\bSEVEN\b": "7",
        r"\bEIGHT\b": "8",
        r"\bNINE\b": "9",
        r"\bTEN\b": "10",
    }

    for pattern, repl in number_replacements.items():
        s = re.sub(pattern, repl, s)

    return normalize_spaces(s)

def business_name_similarity(a: str, b: str) -> float:
    a_norm = normalize_business_name(a)
    b_norm = normalize_business_name(b)

    if not a_norm or not b_norm:
        return 0.0

    return SequenceMatcher(None, a_norm, b_norm).ratio() * 100

def business_name_contains(a: str, b: str) -> bool:
    a_norm = normalize_business_name(a)
    b_norm = normalize_business_name(b)

    if not a_norm or not b_norm:
        return False

    return a_norm in b_norm or b_norm in a_norm

def split_business_name(business_name: str):
    name = normalize_spaces(business_name)

    if not name:
        return "", ""

    match = re.split(
        r"\s+D\s*/?\s*B\s*/?\s*A\s+",
        name,
        maxsplit=1,
        flags=re.I,
    )

    if len(match) == 2:
        legal_name = normalize_spaces(match[0])
        dba_name = normalize_spaces(match[1])
        return legal_name, dba_name

    return name, ""

def house_number(s: str) -> str:
    m = re.match(r"\s*(\d+[A-Z]?)\b", normalize_address(s))
    return m.group(1) if m else ""


def read_csv_rows(path: Path):
    # utf-8-sig tolerates a BOM in source/reference files.
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_agencies():
    if not AGENCY_CSV.exists():
        raise FileNotFoundError(f"Missing reference file: {AGENCY_CSV}")
    rows = read_csv_rows(AGENCY_CSV)
    result = []
    for r in rows:
        agency_id = normalize_spaces(r.get("Agency ID", ""))
        address = normalize_spaces(r.get("Address", ""))
        if agency_id and address:
            result.append(
                {
                    "agency_id": agency_id,
                    "address": address,
                    "norm": normalize_address(address),
                    "dba": normalize_spaces(r.get("DBA", "")),
                }
            )
    return result


def load_salesforce():
    if not SALESFORCE_CSV.exists():
        return {}
    rows = read_csv_rows(SALESFORCE_CSV)
    result = {}
    for r in rows:
        agency = normalize_spaces(r.get("Agency_Number__c", ""))
        sfid = normalize_spaces(r.get("Id", ""))
        if agency and sfid:
            result[agency] = r
    return result


def extract_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text("text") for page in doc)
    if len(re.sub(r"\s+", "", text)) >= 100:
        return text

    ocr_parts = []
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2), alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        ocr_parts.append(pytesseract.image_to_string(img, config="--psm 6"))
    return "\n".join(ocr_parts)


def clean_ocr(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def find_dates(text: str):
    dates = re.findall(r"\b(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:20)?\d{2}\b", text)
    out = []
    for d in dates:
        d = d.replace("-", "/")
        try:
            dt = datetime.strptime(d, "%m/%d/%Y")
        except ValueError:
            try:
                dt = datetime.strptime(d, "%m/%d/%y")
            except ValueError:
                continue
        val = dt.strftime("%m/%d/%Y")
        if val not in out:
            out.append(val)
    return out


def extract_fields(text: str) -> dict:
    text = clean_ocr(text)
    lines = [normalize_spaces(x) for x in text.splitlines() if normalize_spaces(x)]
    joined = "\n".join(lines)

    # Bond number
    bond_no = ""
    patterns = [
        r"Bond\s*(?:No[\.,]?|Number)\s*[:#]?\s*([A-Z0-9][A-Z0-9 \-]{4,25})",
        r"Bond\s*#\s*([A-Z0-9][A-Z0-9 \-]{4,25})",
    ]
    for p in patterns:
        m = re.search(p, joined, flags=re.I)
        if m:
            bond_no = normalize_spaces(m.group(1))
            bond_no = re.split(r"\s{2,}|CONTINUATION|EFFECTIVE", bond_no, flags=re.I)[0].strip(" -:")
            break
    bond_no = normalize_bond_number(bond_no)

    # Bond amount
    amount = ""

    m = re.search(
        r"(?:Bond\s*Amount|Penalty|Penal\s*Sum)[^\n$]{0,40}"
        r"(\$?\s*[\d,]+(?:\s*[eE@]\s*\d+)?(?:\.\d{2})?)",
        joined,
        flags=re.I,
    )
    if m:
        amount = normalize_bond_amount(m.group(1))

    # Prefer dates around Continuation Effective Date.
    date_context = joined
    mctx = re.search(
        r"Continuation\s+Effective\s+Date(.{0,220})",
        joined,
        flags=re.I | re.S,
    )
    if mctx:
        date_context = mctx.group(0)

    effective_from = ""
    effective_to = ""

    # Repair known OCR mistakes inside dates before matching.
    # Examples:
    # 10/@1/2026 -> 10/01/2026
    # 10/e1/2027 -> 10/01/2027
    date_context_for_match = re.sub(
        r"(?<=/)[@eE](?=\d)",
        "0",
        date_context,
    )

    from_to_match = re.search(
        r"From\s+(\d{1,2}/\d{1,2}/\d{4})\s+To\s+(\d{1,2}/\d{1,2}/\d{4})",
        date_context_for_match,
        flags=re.I,
    )


    if from_to_match:
        effective_from = normalize_ocr_date(from_to_match.group(1))
        effective_to = normalize_ocr_date(from_to_match.group(2))
    else:
        dates = find_dates(date_context)

        if len(dates) < 2:
            dates = find_dates(joined)

        effective_from = dates[0] if dates else ""
        effective_to = dates[1] if len(dates) > 1 else ""

        # Principal block
    business = ""
    address = ""
    start_idx = None

    for i, line in enumerate(lines):
        if re.search(r"Name\s+and\s+Address\s+of\s+Principal", line, re.I):
            start_idx = i + 1
            break

    if start_idx is not None:
        block = []
        stop_words = (
            "Bond No",
            "Bond Number",
            "Continuation Effective",
            "Obligee",
            "Surety",
            "Premium",
        )

        for line in lines[start_idx:start_idx + 12]:
            if any(sw.lower() in line.lower() for sw in stop_words):
                break

            if re.search(r"Name\s+and\s+Address\s+of\s+Principal", line, re.I):
                continue

            block.append(line)

        street_words = r"(?:ST|STREET|RD|ROAD|AVE|AVENUE|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|CT|COURT|HWY|HIGHWAY|PKWY|PARKWAY|RTE|ROUTE)"

        street_idx = None

        for i, line in enumerate(block):
            normal_street = re.search(
                rf"\b{street_words}\b(?:\s+(?:N|S|E|W|NE|NW|SE|SW))?$",
                line,
                re.I,
            )

            highway_street = re.search(
                r"\b(?:US\s+)?(?:HWY|HIGHWAY|RTE|ROUTE)\s+\d+[A-Z]?\b$",
                line,
                re.I,
            )

            if (
                re.match(r"^\d+[A-Z]?\s+\S+", line)
                and (normal_street or highway_street)
            ):
                street_idx = i
                break


        if street_idx is not None:


            business = normalize_spaces(" ".join(block[:street_idx]))
            address = block[street_idx]


    # Fallback street address: first street-looking line.
    if not address:
        street_words = r"(?:ST|STREET|RD|ROAD|AVE|AVENUE|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|CT|COURT|HWY|HIGHWAY|PKWY|PARKWAY|RTE|ROUTE)"

        for line in lines:
            if re.search(rf"^\d+[A-Z]?\s+.+\b{street_words}\b", line, re.I):
                address = line
                break



    return {
        "Business Name": business,
        "Address": address,
        "Effective Date": effective_from,
        "Effective To": effective_to,
        "Bond Amount": amount,
        "Bond Number": bond_no,
    }




def match_agency(address: str, agencies):
    norm = normalize_address(address)
    if not norm:
        return None, "No address extracted."

    exact = [a for a in agencies if a["norm"] == norm]
    if len(exact) == 1:
        return exact[0], "Exact normalized address match; score 100.0"
    if len(exact) > 1:
        return None, f"Ambiguous exact address match ({len(exact)} agencies)."

    num = house_number(norm)
    if not num:
        return None, "No house number available for conservative fuzzy matching."

    candidates = []
    for a in agencies:
        if house_number(a["norm"]) != num:
            continue
        score = SequenceMatcher(None, norm, a["norm"]).ratio() * 100
        candidates.append((score, a))
    candidates.sort(key=lambda x: x[0], reverse=True)

    if not candidates or candidates[0][0] < 92:
        best = candidates[0][0] if candidates else 0
        return None, f"No safe address match; best score {best:.1f}."

    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 4:
        return None, f"Ambiguous fuzzy match; top scores {candidates[0][0]:.1f} and {candidates[1][0]:.1f}."

    return candidates[0][1], f"Conservative fuzzy address match; score {candidates[0][0]:.1f}"


def match_agency_by_bond_number(pdf_bond_number: str, agencies, salesforce):
    pdf_bond = normalize_bond_number(pdf_bond_number)

    if not pdf_bond:
        return None, "No bond number extracted."

    matches = []

    for agency_id, sfrow in salesforce.items():
        sf_bond = normalize_bond_number(
            normalize_spaces(sfrow.get("Bond_Number__c", ""))
        )

        if sf_bond and sf_bond == pdf_bond:
            matches.append(agency_id)

    if not matches:
        return None, f"No Salesforce bond-number match for {pdf_bond}."

    if len(matches) > 1:
        return None, (
            f"Ambiguous bond-number match; "
            f"{len(matches)} Salesforce Accounts use {pdf_bond}."
        )

    matched_agency_id = matches[0]

    for agency in agencies:
        if agency["agency_id"] == matched_agency_id:
            return (
                agency,
                f"Exact Salesforce bond-number match; {pdf_bond}"
            )

    return None, (
        f"Bond number {pdf_bond} matched Salesforce Agency "
        f"{matched_agency_id}, but that Agency ID was not found "
        f"in Agency Admin.csv."
    )

def match_agency_by_name(pdf_business: str, agencies, salesforce):
    pdf_legal_name, pdf_dba = split_business_name(pdf_business)

    candidates = []

    for agency in agencies:
        agency_id = agency["agency_id"]
        sfrow = salesforce.get(agency_id)

        agency_dba = agency.get("dba", "")
        sf_dba = normalize_spaces(sfrow.get("DBA__c", "")) if sfrow else ""
        sf_permit_name = normalize_spaces(sfrow.get("Permit_Name__c", "")) if sfrow else ""

        # DBA evidence
        if pdf_dba:
            agency_dba_score = business_name_similarity(pdf_dba, agency_dba)
            sf_dba_score = business_name_similarity(pdf_dba, sf_dba)

            agency_dba_contains = business_name_contains(pdf_dba, agency_dba)
            sf_dba_contains = business_name_contains(pdf_dba, sf_dba)
        else:
            agency_dba_score = business_name_similarity(pdf_legal_name, agency_dba)
            sf_dba_score = business_name_similarity(pdf_legal_name, sf_dba)

            agency_dba_contains = business_name_contains(pdf_legal_name, agency_dba)
            sf_dba_contains = business_name_contains(pdf_legal_name, sf_dba)

        # Permit/legal-name evidence
        permit_score = business_name_similarity(
            pdf_legal_name,
            sf_permit_name,
        )

        permit_contains = business_name_contains(
            pdf_legal_name,
            sf_permit_name,
        )

        dba_support = (
            agency_dba_score >= 90
            or sf_dba_score >= 90
            or agency_dba_contains
            or sf_dba_contains
        )

        permit_support = (
            permit_score >= 90
            or permit_contains
        )

        # IMPORTANT:
        # DBA alone is not enough for an automatic match.
        if dba_support and permit_support:
            candidates.append({
                "agency": agency,
                "agency_dba_score": agency_dba_score,
                "sf_dba_score": sf_dba_score,
                "permit_score": permit_score,
            })

    if not candidates:
        return None, (
            "No safe name fallback match; "
            "DBA evidence alone is insufficient or Permit Name is unavailable"
        )

    # More than one qualifying candidate = do not guess.
    if len(candidates) > 1:
        return None, (
            f"Ambiguous name fallback; "
            f"{len(candidates)} candidates met DBA + Permit Name requirements"
        )

    best = candidates[0]

    return (
        best["agency"],
        f"Name fallback match using DBA + Permit Name; "
        f"Agency DBA {best['agency_dba_score']:.1f}; "
        f"Salesforce DBA {best['sf_dba_score']:.1f}; "
        f"Permit Name {best['permit_score']:.1f}"
    )
def unique_destination(directory: Path, filename: str) -> Path:
    p = directory / filename
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 2
    while True:
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def sf_date(mmddyyyy: str) -> str:
    if not mmddyyyy:
        return ""
    return datetime.strptime(mmddyyyy, "%m/%d/%Y").strftime("%Y-%m-%d")


def sf_amount(amount: str) -> str:
    return re.sub(r"[^\d.]", "", amount or "")


def sf_bond_number(bond: str, existing: str = "") -> str:
    b = normalize_spaces(bond).upper()
    existing = normalize_spaces(existing).upper()

    # If OCR dropped a decorative prefix/suffix but the numeric core matches the
    # current Salesforce bond number, preserve Salesforce's known formatting.
    # Example OCR: "94 5170822 C" vs Salesforce: "Q94-5170822".
    if existing:
        ocr_digits = re.sub(r"\D", "", b)
        existing_digits = re.sub(r"\D", "", existing)
        if ocr_digits and ocr_digits == existing_digits:
            return existing

    m = re.fullmatch(r"([A-Z]\d{2})\s+(\d{7})(?:\s+[A-Z])?", b)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return re.sub(r"\s+", "-", b)


def write_csv(path: Path, fieldnames, rows):
    # Intentionally plain UTF-8, not utf-8-sig. This removes the ï»¿Title BOM artifact in Data Loader.
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_upload_state():
    if not UPLOAD_STATE.exists():
        return {}
    try:
        return json.loads(UPLOAD_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_upload_state(state):
    UPLOAD_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def upload_to_salesforce(raw_rows, cfg):
    sf = cfg.get("salesforce", {})
    if not sf.get("enabled"):
        raise RuntimeError(
            "Salesforce is disabled in config.json. Set salesforce.enabled to true before using upload/full mode."
        )

    from salesforce_client import SalesforceClient, sha256_file

    print("\nSalesforce direct upload is enabled.")
    client = SalesforceClient(sf)
    state = load_upload_state()
    successes = []
    errors = []

    for row in raw_rows:
        if row["Match Status"] != "Matched" or row["Salesforce Match Status"] != "Matched":
            continue
        pdf = PROCESSED_DIR / row["Final Document Name"]
        if not pdf.exists():
            errors.append({**row, "Result": "PDF not found"})
            continue

        digest = sha256_file(pdf)
        state_key = f'{row["Salesforce Id"]}:{digest}'
        if state_key in state:
            print(f'  SKIP {pdf.name} - already uploaded as {state[state_key].get("ContentVersionId")}')
            continue

        try:
            cv_id = client.create_content_version(
                pdf_path=pdf,
                title=pdf.stem,
                account_id=row["Salesforce Id"],
            )
            state[state_key] = {
                "ContentVersionId": cv_id,
                "File": pdf.name,
                "AccountId": row["Salesforce Id"],
                "UploadedAt": datetime.now().isoformat(timespec="seconds"),
            }
            save_upload_state(state)
            successes.append({**row, "ContentVersionId": cv_id, "Result": "Uploaded"})
            print(f"  UPLOADED {pdf.name} -> {cv_id}")
        except Exception as e:
            errors.append({**row, "Result": str(e)})
            print(f"  ERROR {pdf.name}: {e}")

    result_fields = RAW_FIELDS + ["ContentVersionId", "Result"]
    write_csv(UPLOAD_SUCCESS, result_fields, successes)
    write_csv(UPLOAD_ERRORS, result_fields, errors)
    return {
        "uploaded": len(successes),
        "skipped_duplicate": sum(
            1
            for row in raw_rows
            if row["Match Status"] == "Matched"
            and row["Salesforce Match Status"] == "Matched"
            and (PROCESSED_DIR / row["Final Document Name"]).exists()
            and f'{row["Salesforce Id"]}:{sha256_file(PROCESSED_DIR / row["Final Document Name"])}' in state
        ) - len(successes),
        "errors": len(errors),
    }


def maybe_update_salesforce_accounts(raw_rows, cfg):
    sf = cfg.get("salesforce", {})
    if not sf.get("enabled"):
        raise RuntimeError(
            "Salesforce is disabled in config.json. Set salesforce.enabled to true before using full mode."
        )

    from salesforce_client import SalesforceClient

    print("\nSalesforce Account updates are ENABLED.")
    client = SalesforceClient(sf)
    updated = 0
    errors = 0
    for row in raw_rows:
        if row["Match Status"] != "Matched" or row["Salesforce Match Status"] != "Matched":
            continue
        fields = {}
        if row["Bond Amount"]:
            fields["Bond_Amount__c"] = float(sf_amount(row["Bond Amount"]))
        if row["Effective To"]:
            fields["Bond_Expiration__c"] = sf_date(row["Effective To"])
        if row["Bond Number"]:
            fields["Bond_Number__c"] = sf_bond_number(row["Bond Number"])
        if fields:
            try:
                client.update_account(row["Salesforce Id"], fields)
                updated += 1
                print(f'  UPDATED Account {row["Agency ID"]}')
            except Exception as e:
                errors += 1
                print(f'  ERROR updating Account {row["Agency ID"]}: {e}')
    return {"updated": updated, "errors": errors}




def print_batch_summary(total_pdfs, raw_rows, mode, upload_stats=None, update_stats=None):
    upload_stats = upload_stats or {"uploaded": 0, "skipped_duplicate": 0, "errors": 0}
    update_stats = update_stats or {"updated": 0, "errors": 0}

    processed = sum(1 for row in raw_rows if row["Match Status"] != "Error")
    matched = sum(1 for row in raw_rows if row["Match Status"] == "Matched")
    unmatched = sum(1 for row in raw_rows if row["Match Status"] == "Unmatched")
    processing_errors = sum(1 for row in raw_rows if row["Match Status"] == "Error")
    sf_not_found = sum(
        1 for row in raw_rows
        if row["Match Status"] == "Matched"
        and row["Salesforce Match Status"] == "Not Found"
    )

    print("\n" + "=" * 46)
    print("                 BATCH SUMMARY")
    print("=" * 46)
    print(f"Run mode:                  {mode.upper()}")
    print()
    print(f"PDFs found:                {total_pdfs}")
    print(f"Successfully processed:    {processed}")
    print(f"Agency matched:            {matched}")
    print(f"Unmatched:                 {unmatched}")
    print(f"Processing errors:         {processing_errors}")
    print(f"Salesforce Account missing:{sf_not_found:>5}")

    if mode in ("upload", "full"):
        print()
        print("Salesforce file uploads:")
        print(f"  Uploaded:                {upload_stats['uploaded']}")
        print(f"  Skipped duplicate:       {upload_stats['skipped_duplicate']}")
        print(f"  Upload errors:           {upload_stats['errors']}")

    if mode == "full":
        print()
        print("Salesforce Account updates:")
        print(f"  Accounts updated:        {update_stats['updated']}")
        print(f"  Update errors:           {update_stats['errors']}")

    print()
    print("Review:")
    print(f"  {RAW_CSV}")
    if mode in ("upload", "full"):
        print(f"  {UPLOAD_SUCCESS}")
        print(f"  {UPLOAD_ERRORS}")
    print("=" * 46)


def choose_run_mode(requested_mode: str | None, assume_yes: bool = False) -> str:
    if requested_mode:
        mode = requested_mode
    else:
        print("""
Choose run mode:

  1) PROCESS ONLY
     Extract, match, rename/move PDFs, and generate CSVs.
     No Salesforce records are changed.

  2) UPLOAD FILES
     Everything above + upload matched PDFs to Salesforce Accounts.
     Account bond fields are NOT changed.

  3) FULL AUTOMATION
     Everything above + upload PDFs + update Account bond fields:
       - Bond_Amount__c
       - Bond_Expiration__c
       - Bond_Number__c

  0) Cancel
""")
        choice = input("Select 0, 1, 2, or 3: ").strip()
        mapping = {"1": "process", "2": "upload", "3": "full", "0": "cancel"}
        mode = mapping.get(choice, "")
        if not mode:
            raise RuntimeError("Invalid run-mode selection.")

    if mode == "cancel":
        return mode

    if mode == "full" and not assume_yes:
        print("""
FULL AUTOMATION will write to Salesforce Account records.

The script will update ONLY these fields on matched Accounts:
  Bond_Amount__c
  Bond_Expiration__c
  Bond_Number__c

DBA__c, Insured_By__c, and unrelated Account fields are not touched.
""")
        confirm = input('Type YES to continue with FULL AUTOMATION: ').strip()
        if confirm != "YES":
            print("Full automation cancelled. No PDFs were processed.")
            return "cancel"

    return mode


def parse_args():
    parser = argparse.ArgumentParser(
        description="Process bond renewal PDFs and optionally upload/update Salesforce."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["process", "upload", "full"],
        help=(
            "process = local processing only; "
            "upload = process + Salesforce PDF upload; "
            "full = process + PDF upload + Account field updates. "
            "Omit to use the interactive safety menu."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the FULL AUTOMATION confirmation prompt. Use only for trusted unattended runs.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    mode = choose_run_mode(args.mode, assume_yes=args.yes)
    if mode == "cancel":
        print("Cancelled.")
        return 0

    ensure_dirs()
    cfg = load_config()
    configure_tesseract(cfg)

    print(f"Run mode: {mode.upper()}")

    agencies = load_agencies()
    salesforce = load_salesforce()

    pdfs = sorted(INPUT_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {INPUT_DIR}")
        return 0

    raw_rows = []
    sf_update_rows = []
    sf_upload_rows = []

    print(f"Loaded {len(agencies)} agency addresses.")
    print(f"Loaded {len(salesforce)} Salesforce agency mappings.")
    print(f"Found {len(pdfs)} PDF(s) to process.\n")

    for pdf in pdfs:
        print(f"Processing: {pdf.name}")
        try:
            text = extract_pdf_text(pdf)
            fields = extract_fields(text)
            agency, detail = match_agency(fields["Address"], agencies)

            if agency:
                if detail.startswith("Exact normalized"):
                    match_method = "Address - Exact Normalized"
                    match_confidence = "High"
                else:
                    match_method = "Address - Conservative Fuzzy"
                    match_confidence = "High"

                address_match = "Yes"

            else:
                address_detail = detail

                bond_agency, bond_detail = match_agency_by_bond_number(
                    fields["Bond Number"],
                    agencies,
                    salesforce,
                )

                if bond_agency:
                    agency = bond_agency
                    detail = (
                        f"Address match failed: {address_detail} "
                        f"Bond-number fallback succeeded: {bond_detail}"
                    )
                    match_method = "Bond Number - Exact Salesforce"
                    match_confidence = "High"
                    address_match = "No"

                else:
                    name_agency, name_detail = match_agency_by_name(
                        fields["Business Name"],
                        agencies,
                        salesforce,
                    )

                    if name_agency:
                        agency = name_agency
                        detail = (
                            f"Address match failed: {address_detail} "
                            f"Bond-number fallback failed: {bond_detail} "
                            f"Name fallback succeeded: {name_detail}"
                        )
                        match_method = "Name Fallback"
                        match_confidence = "High"
                        address_match = "No"

                    else:
                        detail = (
                            f"Address match failed: {address_detail} "
                            f"Bond-number fallback failed: {bond_detail} "
                            f"Name fallback failed: {name_detail}"
                        )
                        match_method = "No Match"
                        match_confidence = "None"
                        address_match = "No"

            agency_id = agency["agency_id"] if agency else ""
            sfrow = salesforce.get(agency_id) if agency_id else None
            sfid = normalize_spaces(sfrow.get("Id", "")) if sfrow else ""
            pdf_business = fields["Business Name"]
            pdf_legal_name, pdf_dba = split_business_name(pdf_business)


            agency_dba = agency.get("dba", "") if agency else ""
            sf_dba = normalize_spaces(sfrow.get("DBA__c", "")) if sfrow else ""
            sf_permit_name = normalize_spaces(sfrow.get("Permit_Name__c", "")) if sfrow else ""

            if pdf_dba:
                agency_dba_score = business_name_similarity(pdf_dba, agency_dba)
                sf_dba_score = business_name_similarity(pdf_dba, sf_dba)
            else:
                agency_dba_score = business_name_similarity(pdf_legal_name, agency_dba)
                sf_dba_score = business_name_similarity(pdf_legal_name, sf_dba)

            permit_name_score = business_name_similarity(pdf_legal_name, sf_permit_name)
            permit_name_contains = business_name_contains(pdf_legal_name, sf_permit_name)
            if pdf_dba:
                agency_dba_contains = business_name_contains(pdf_dba, agency_dba)
                sf_dba_contains = business_name_contains(pdf_dba, sf_dba)
            else:
                agency_dba_contains = business_name_contains(pdf_legal_name, agency_dba)
                sf_dba_contains = business_name_contains(pdf_legal_name, sf_dba)
                sf_dba = normalize_spaces(sfrow.get("DBA__c", "")) if sfrow else ""
                sf_permit_name = normalize_spaces(sfrow.get("Permit_Name__c", "")) if sfrow else ""


            match_status = "Matched" if agency else "Unmatched"
            sf_status = "Matched" if sfid else ("Not Found" if agency else "Not Attempted")

            if agency:
                dest = unique_destination(PROCESSED_DIR, f"{agency_id}_Bond_Renewal.pdf")
            else:
                dest = unique_destination(UNMATCHED_DIR, pdf.name)
            shutil.move(str(pdf), str(dest))


            row = {
                "Salesforce Id": sfid,
                "Agency ID": agency_id,
                **fields,
                "Source Document": pdf.name,
                "Final Document Name": dest.name,
                "Match Status": match_status,
                "Match Confidence": match_confidence,
                "Match Method": match_method,
                "Address Match": address_match,
                "Agency DBA Similarity": f"{agency_dba_score:.1f}" if agency_dba else "",
                "Agency DBA Containment": "Yes" if agency_dba_contains else "No",
                "Salesforce DBA Similarity": f"{sf_dba_score:.1f}" if sf_dba else "",
                "Salesforce DBA Containment": "Yes" if sf_dba_contains else "No",
                "Permit Name Similarity": f"{permit_name_score:.1f}" if sf_permit_name else "",
                "Permit Name Containment": "Yes" if permit_name_contains else "No",
                "Salesforce Match Status": sf_status,
                "Match Detail": detail,
            }

            raw_rows.append(row)

            if agency and sfid:
                sf_update_rows.append(
                    {
                        "Id": sfid,
                        "Agency_Number__c": agency_id,
                        "Bond_Amount__c": sf_amount(fields["Bond Amount"]),
                        "Bond_Expiration__c": sf_date(fields["Effective To"]) if fields["Effective To"] else "",
                        "Bond_Number__c": sf_bond_number(fields["Bond Number"], sfrow.get("Bond_Number__c", "")),
                    }
                )
                full_path = str(dest.resolve())
                sf_upload_rows.append(
                    {
                        "Title": dest.stem,
                        "PathOnClient": full_path,
                        "VersionData": full_path,
                        "FirstPublishLocationId": sfid,
                    }
                )

            # Persist after every file.
            write_csv(RAW_CSV, RAW_FIELDS, raw_rows)
            write_csv(SF_UPDATE_CSV, SF_UPDATE_FIELDS, sf_update_rows)
            write_csv(SF_UPLOAD_CSV, SF_UPLOAD_FIELDS, sf_upload_rows)

            print(f'  Business: {fields["Business Name"]}')
            print(f'  Address:  {fields["Address"]}')
            print(f'  Agency:   {agency_id or "UNMATCHED"}')
            print(f'  SF Id:    {sfid or "N/A"}')
            print(f'  File:     {dest.name}\n')

        except Exception as e:
            dest = unique_destination(UNMATCHED_DIR, pdf.name)
            if pdf.exists():
                shutil.move(str(pdf), str(dest))
            row = {
                "Salesforce Id": "",
                "Agency ID": "",
                "Business Name": "",
                "Address": "",
                "Effective Date": "",
                "Effective To": "",
                "Bond Amount": "",
                "Bond Number": "",
                "Source Document": pdf.name,
                "Final Document Name": dest.name,
                "Match Status": "Error",
                "Salesforce Match Status": "Not Attempted",
                "Match Detail": str(e),
                "Match Confidence": "",
                "Match Method": "Processing Error",
                "Address Match": "",
                "Agency DBA Similarity": "",
                "Salesforce DBA Similarity": "",
                "Permit Name Similarity": "",
                "Agency DBA Containment": "",
                "Salesforce DBA Containment": "",
                "Permit Name Containment": "",
            }
            raw_rows.append(row)
            write_csv(RAW_CSV, RAW_FIELDS, raw_rows)
            print(f"  ERROR: {e}\n")

    # Always retain the proven Data Loader CSV fallback.
    write_csv(RAW_CSV, RAW_FIELDS, raw_rows)
    write_csv(SF_UPDATE_CSV, SF_UPDATE_FIELDS, sf_update_rows)
    write_csv(SF_UPLOAD_CSV, SF_UPLOAD_FIELDS, sf_upload_rows)

    # Runtime safety mode controls Salesforce changes.
    upload_stats = None
    update_stats = None

    if mode in ("upload", "full"):
        upload_stats = upload_to_salesforce(raw_rows, cfg)

    if mode == "full":
        update_stats = maybe_update_salesforce_accounts(raw_rows, cfg)

    print_batch_summary(
        total_pdfs=len(pdfs),
        raw_rows=raw_rows,
        mode=mode,
        upload_stats=upload_stats,
        update_stats=update_stats,
    )

    print("\nDone.")
    print(f"Raw results:       {RAW_CSV}")
    print(f"SF update CSV:     {SF_UPDATE_CSV}")
    print(f"SF file upload:    {SF_UPLOAD_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
