# -*- coding: utf-8 -*-
"""
Fare watcher: BER → Anywhere, one-way under a price cap.
"""

import os, sys, json, time, ssl, smtplib
import datetime as dt
from email.mime.text import MIMEText
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter, Retry

AMADEUS_BASE = os.getenv("AMADEUS_BASE", "https://api.amadeus.com")
STATE_FILE = Path("state.json")

def build_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=4, connect=4, read=4, backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "fare-watch/1.0"})
    return s

SESSION = build_session()

def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        print(f"[ERROR] Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return val

def get_token() -> str:
    cid = require_env("AMADEUS_CLIENT_ID")
    secret = require_env("AMADEUS_CLIENT_SECRET")
    resp = SESSION.post(
        f"{AMADEUS_BASE}/v1/security/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
        timeout=(10, 45),
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def inspiration(token: str, origin: str, max_price: int, date_range: str):
    params = {
        "origin": origin,
        "oneWay": "true",
        "maxPrice": str(max_price),
        "departureDate": date_range,
    }
    r = SESSION.get(
        f"{AMADEUS_BASE}/v1/shopping/flight-destinations",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=(10, 60)
    )
    r.raise_for_status()
    return r.json().get("data", [])

def offers(token: str, origin: str, dest: str, dep_date: str, currency: str = "EUR", max_results: int = 3):
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": dest,
        "departureDate": dep_date,
        "adults": "1",
        "currencyCode": currency,
        "max": str(max_results),
    }
    r = SESSION.get(
        f"{AMADEUS_BASE}/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=(10, 60)
    )
    r.raise_for_status()
    return r.json().get("data", [])

def send_email(subject: str, html_body: str):
    host = require_env("SMTP_HOST")
    port = int(require_env("SMTP_PORT"))
    user = require_env("SMTP_USER")
    password = require_env("SMTP_PASS")
    recipient = require_env("RECIPIENT_EMAIL")

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)
        print("[INFO] Email sent successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {"alerts": {}}
    return {"alerts": {}}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def main():
    origin = os.getenv("ORIGIN", "BER")
    max_price = int(os.getenv("MAX_PRICE_EUR", "80"))
    days_ahead = int(os.getenv("DAYS_AHEAD", "180"))
    max_candidates = int(os.getenv("MAX_CANDIDATES", "8"))
    sleep_ms = int(os.getenv("SLEEP_BETWEEN_MS", "300"))

    token = get_token()

    today = dt.date.today()
    end = today + dt.timedelta(days=days_ahead)
    date_range = f"{today.isoformat()},{end.isoformat()}"

    print(f"[INFO] Searching one-way from {origin} for ≤ {max_price}€ within {date_range}")
    insp = inspiration(token, origin, max_price, date_range)
    print(f"[INFO] Inspiration candidates: {len(insp)} (checking first {max_candidates})")

    state = load_state()
    alerts = []

    for it in insp[:max_candidates]:
        dest = it.get("destination")
        dep = it.get("departureDate")
        if not (dest and dep):
            continue
        try:
            live = offers(token, origin, dest, dep, currency="EUR", max_results=3)
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Offers failed for {origin}-{dest} on {dep}: {e}")
            continue

        if not live:
            time.sleep(sleep_ms / 1000.0)
            continue

        try:
            live_total = float(live[0]["price"]["grandTotal"])
        except Exception:
            time.sleep(sleep_ms / 1000.0)
            continue

        if live_total <= max_price:
            key = f"{origin}-{dest}-{dep}-{int(live_total)}"
            last = state["alerts"].get(key, 0)
            if time.time() - last < 48 * 3600:
                continue

            html = (
                f"<p>✈️ <b>{origin}</b> → <b>{dest}</b><br>"
                f"<b>Date:</b> {dep}<br>"
                f"<b>Price:</b> {live_total:.0f} €</p>"
            )
            alerts.append(html)
            state["alerts"][key] = time.time()

        time.sleep(sleep_ms / 1000.0)

    if alerts:
        body = "<h3>New one-way fare(s) under your cap</h3>" + "<hr/>".join(alerts)
        send_email("New one-way fare(s) under your cap", body)
        save_state(state)

main()
