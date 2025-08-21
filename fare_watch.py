# -*- coding: utf-8 -*-
"""
Fare watcher: BER → Anywhere, round-trip under a price cap.
- Uses Amadeus Self-Service APIs (Inspiration + Offers)
- Sends email via SMTP (Gmail App Password recommended)
- Designed to run on GitHub Actions

Environment variables (required):
  AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / RECIPIENT_EMAIL
  ORIGIN (e.g., BER)
  MAX_PRICE_EUR (e.g., 80)
  DAYS_AHEAD (e.g., 180)

Optional:
  AMADEUS_BASE (defaults to https://test.api.amadeus.com for safety)
"""
import os, sys, json, time, ssl, smtplib, traceback
import datetime as dt
from email.mime.text import MIMEText
from pathlib import Path

import requests

# Default to TEST while you validate. Switch to https://api.amadeus.com later.
AMADEUS_BASE = os.getenv("AMADEUS_BASE", "https://test.api.amadeus.com")
STATE_FILE = Path("state.json")


def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        print(f"[ERROR] Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return val


def get_token() -> str:
    cid = require_env("AMADEUS_CLIENT_ID")
    secret = require_env("AMADEUS_CLIENT_SECRET")
    resp = requests.post(
        f"{AMADEUS_BASE}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": secret,
        },
        timeout=20,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"[ERROR] Amadeus token request failed: {e}\n{resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    return data["access_token"]


def inspiration(token: str, origin: str, max_price: int, date_range: str):
    params = {
        "origin": origin,
        "oneWay": "false",              # round-trip
        "maxPrice": str(max_price),
        "departureDate": date_range,    # e.g., 2025-08-21,2026-02-17
    }
    r = requests.get(
        f"{AMADEUS_BASE}/v1/shopping/flight-destinations",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=25
    )
    r.raise_for_status()
    return r.json().get("data", [])


def offers(token: str, origin: str, dest: str, dep_date: str, ret_date: str,
           currency: str = "EUR", max_results: int = 5):
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": dest,
        "departureDate": dep_date,
        "returnDate": ret_date,
        "adults": "1",
        "currencyCode": currency,
        "max": str(max_results),
        # do NOT set nonStop → allow connections
    }
    r = requests.get(
        f"{AMADEUS_BASE}/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=30
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
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(user, password)
        server.send_message(msg)


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
    try:
        origin = os.getenv("ORIGIN", "BER")
        max_price = int(os.getenv("MAX_PRICE_EUR", "80"))
        days_ahead = int(os.getenv("DAYS_AHEAD", "180"))

        token = get_token()

        today = dt.date.today()
        end = today + dt.timedelta(days=days_ahead)
        date_range = f"{today.isoformat()},{end.isoformat()}"

        print(f"[INFO] Searching from {origin} for RT ≤ {max_price}€ within {date_range}")
        insp = inspiration(token, origin, max_price, date_range)
        print(f"[INFO] Inspiration candidates: {len(insp)}")

        state = load_state()
        alerts = []

        for it in insp:
            dest = it.get("destination")
            dep = it.get("departureDate")
            ret = it.get("returnDate")
            if not (dest and dep and ret):
                continue

            # Confirm live price
            try:
                live = offers(token, origin, dest, dep, ret, currency="EUR", max_results=3)
            except requests.HTTPError as e:
                print(f"[WARN] Offers failed for {origin}-{dest} {dep}->{ret}: {e}")
                continue

            if not live:
                continue

            try:
                live_total = float(live[0]["price"]["grandTotal"])
            except Exception:
                continue

            if live_total <= max_price:
                key = f"{origin}-{dest}-{dep}-{ret}-{int(live_total)}"
                last = state["alerts"].get(key, 0)
                if time.time() - last < 48 * 3600:
                    continue

                html = (
                    f"<p>✈️ <b>{origin}</b> ⇄ <b>{dest}</b><br>"
                    f"<b>Dates:</b> {dep} → {ret}<br>"
                    f"<b>Price:</b> {live_total:.0f} €</p>"
                )
                alerts.append(html)
                state["alerts"][key] = time.time()

        if alerts:
            body = "<h3>New round‑trip fare(s) under your cap</h3>" + "<hr/>".join(alerts)
            send_email("New round‑trip fare(s) under your cap", body)
            print(f"[INFO] Sent {len(alerts)} alert(s).")
        else:
            print("[INFO] No new fares under cap.")

        save_state(state)

    except SystemExit:
        raise
    except Exception:
        print("[ERROR] Unhandled exception:\n")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
