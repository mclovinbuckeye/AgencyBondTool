from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import keyring
import requests

SERVICE_NAME = "BondRenewalTool/Salesforce"
REFRESH_TOKEN_KEY = "refresh_token"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def load_config(project_dir: Path) -> dict:
    path = project_dir / "config.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Copy config.example.json to config.json and fill in Salesforce settings."
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    code = None
    error = None
    state = None
    expected_state = None
    event = threading.Event()

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/OauthRedirect":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        state = params.get("state", [None])[0]
        if state != self.expected_state:
            self.__class__.error = "OAuth state mismatch."
        elif "error" in params:
            self.__class__.error = params.get("error_description", params["error"])[0]
        else:
            self.__class__.code = params.get("code", [None])[0]

        body = b"""<!doctype html>
<html><head><title>Bond Renewal Tool</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;margin:40px">
<h2>Salesforce authorization received.</h2>
<p>You can close this browser tab and return to the Bond Renewal Tool.</p>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.__class__.event.set()


def interactive_login(sf: dict) -> dict:
    base_url = sf["my_domain_url"].rstrip("/")
    client_id = sf["consumer_key"].strip()
    port = int(sf.get("callback_port", 7171))
    redirect_uri = f"http://localhost:{port}/OauthRedirect"

    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)

    OAuthCallbackHandler.code = None
    OAuthCallbackHandler.error = None
    OAuthCallbackHandler.expected_state = state
    OAuthCallbackHandler.event = threading.Event()

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "api refresh_token offline_access",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{base_url}/services/oauth2/authorize?{urllib.parse.urlencode(params)}"

    server = HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("Opening Salesforce authorization in your browser...")
    webbrowser.open(auth_url)

    if not OAuthCallbackHandler.event.wait(timeout=300):
        server.shutdown()
        raise RuntimeError("Timed out waiting for Salesforce OAuth callback.")

    server.shutdown()

    if OAuthCallbackHandler.error:
        raise RuntimeError(f"Salesforce authorization failed: {OAuthCallbackHandler.error}")
    if not OAuthCallbackHandler.code:
        raise RuntimeError("Salesforce did not return an authorization code.")

    token_url = f"{base_url}/services/oauth2/token"
    response = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code": OAuthCallbackHandler.code,
            "code_verifier": verifier,
        },
        timeout=60,
    )
    response.raise_for_status()
    token = response.json()

    refresh_token = token.get("refresh_token")
    if refresh_token:
        keyring.set_password(SERVICE_NAME, REFRESH_TOKEN_KEY, refresh_token)
        print("Salesforce refresh token saved securely using your operating system credential store.")
    else:
        print("Warning: Salesforce did not return a refresh token.")

    return token


def refresh_login(sf: dict) -> dict | None:
    refresh_token = keyring.get_password(SERVICE_NAME, REFRESH_TOKEN_KEY)
    if not refresh_token:
        return None

    base_url = sf["my_domain_url"].rstrip("/")
    response = requests.post(
        f"{base_url}/services/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "client_id": sf["consumer_key"].strip(),
            "refresh_token": refresh_token,
        },
        timeout=60,
    )
    if response.status_code >= 400:
        return None
    return response.json()


def get_access_token(sf: dict, force_interactive: bool = False) -> dict:
    if not force_interactive:
        token = refresh_login(sf)
        if token:
            return token
    return interactive_login(sf)


def clear_saved_login():
    try:
        keyring.delete_password(SERVICE_NAME, REFRESH_TOKEN_KEY)
        print("Saved Salesforce refresh token removed.")
    except keyring.errors.PasswordDeleteError:
        print("No saved Salesforce refresh token was found.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Force an interactive Salesforce OAuth login")
    parser.add_argument("--logout", action="store_true", help="Remove the saved Salesforce refresh token")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent
    if args.logout:
        clear_saved_login()
        return

    cfg = load_config(project_dir)
    sf = cfg["salesforce"]
    token = get_access_token(sf, force_interactive=args.login)
    print(f"Salesforce OAuth succeeded. Instance: {token.get('instance_url', sf.get('my_domain_url'))}")


if __name__ == "__main__":
    main()
