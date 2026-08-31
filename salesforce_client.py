from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import requests

from salesforce_oauth import get_access_token


class SalesforceClient:
    def __init__(self, sf_config: dict):
        self.sf = sf_config
        self.api_version = sf_config.get("api_version", "67.0")
        token = get_access_token(sf_config)
        self.access_token = token["access_token"]
        self.instance_url = token.get("instance_url", sf_config["my_domain_url"]).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.instance_url}/services/data/v{self.api_version}/{path.lstrip('/')}"

    def create_content_version(self, pdf_path: Path, title: str, account_id: str) -> str:
        raw = pdf_path.read_bytes()
        payload = {
            "Title": title,
            "PathOnClient": pdf_path.name,
            "VersionData": base64.b64encode(raw).decode("ascii"),
            "FirstPublishLocationId": account_id,
        }
        r = self.session.post(self._url("sobjects/ContentVersion"), data=json.dumps(payload), timeout=180)
        if r.status_code >= 400:
            raise RuntimeError(f"ContentVersion insert failed ({r.status_code}): {r.text}")
        result = r.json()
        if not result.get("success"):
            raise RuntimeError(f"ContentVersion insert failed: {result}")
        return result["id"]

    def update_account(self, account_id: str, fields: dict):
        r = self.session.patch(
            self._url(f"sobjects/Account/{account_id}"),
            data=json.dumps(fields),
            timeout=60,
        )
        if r.status_code not in (204,):
            raise RuntimeError(f"Account update failed ({r.status_code}): {r.text}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
