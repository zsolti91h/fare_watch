# file: fare_watch.py
import os, json, time, smtplib, ssl
import datetime as dt
from pathlib import Path
from email.mime.text import MIMEText
import requests

AMADEUS_BASE = "https://api.amadeus.com"  # use https://test.api.amadeus.com if you want only test data
STATE_FILE = Path("state.json")

def get_token():
    r = requests.post(
        f"{AMADEUS_BASE}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["AMADEUS_CLIENT_ID"],
            "client_secret": os.environ["AMADEUS_CLIENT_SECRET"],
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def inspiration(token, origin, max_price_eur, date_range):
    # oneWay=false → round-trip suggestions (cached/indicative)  ─ Amadeus docs
    params = {
        "origin": origin,
        "oneWay": "false",
        "maxPrice": str(max_price_eur),
        "departureDate": date_range,  # e.g., 2025-09-01,2026-03-01
    }
    r = requests.get(
        f"{AMADEUS_BASE}/v1/shopping/flight-destinations",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=20
    )
    r.raise_for_status()
    return r.json().get("data", [])

def offers(token, origin, dest, dep_date, ret_date, currency="EUR", max_results=5):
    # Live round-trip pricing  ─ Amadeus Flight Offers Search
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": dest,
        "departureDate": dep_date,
        "returnDate": ret_date,
        "adults": "1",
        "currencyCode": currency,
        "max": str(max_results),
        # Do NOT set nonStop → allow connections (your requirement)
    }
    r = requests.get(
        f"{AMADEUS_BASE}/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=25
    )
    r.raise_for_status()
    return r.json().get("data", [])

def send_email(subject, html_body):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    recipient = os.environ["RECIPIENT_EMAIL"]

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(user, password)   # Office 365 needs STARTTLS on 587; Gmail requires App Password with 2FA
        server.send_message(msg)

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"alerts": {}}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def main():
    origin = os.getenv("ORIGIN", "BER")
    max_price = int(os.getenv("MAX_PRICE_EUR", "150"))
    days_ahead = int(os.getenv("DAYS_AHEAD", "180"))

    token = get_token()
    today = dt.date.today()
    end = today + dt.timedelta(days=days_ahead)
    date_range = f"{today.isoformat()},{end.isoformat()}"

    insp = inspiration(token, origin, max_price, date_range)
    state = load_state()
    new_alerts = []

    for item in insp:
        dest = item["destination"]         # e.g., BCN
        dep = item.get("departureDate")    # present for RT suggestions
        ret = item.get("returnDate")
        if not (dep and ret):
            continue

        # Confirm with live price
        live = offers(token, origin, dest, dep, ret, currency="EUR", max_results=3)
        if not live:
            continue
        # Amadeus returns grandTotal for full itinerary
        try:
            live_total = float(live[0]["price"]["grandTotal"])
        except Exception:
            continue

        if live_total <= max_price:
            key = f"{origin}-{dest}-{dep}-{ret}-{int(live_total)}"
            last = state["alerts"].get(key, 0)
            if time.time() - last < 48 * 3600:  # suppress duplicates for 48h
                continue

            # Build a simple HTML snippet
            html = f"""
            <p>✈️ <b>Deal found under your round‑trip cap</b></p>
            <p><b>{origin}</b> ⇄ <b>{dest}</b><br/>
               <b>Dates:</b> {dep} → {ret}<br/>
               <b>Price:</b> {live_total:.0f} €</p>
            <p>Checked live via Amadeus Flight Offers Search.</p>
            """
            new_alerts.append(html)
            state["alerts"][key] = time.time()

    if new_alerts:
        body = "<hr/>".join(new_alerts) + "<p>— Your flight price bot</p>"
        send_email("New round‑trip fare(s) under your cap", body)

    save_state(state)

if __name__ == "__main__":
    main()
