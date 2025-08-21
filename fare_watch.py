name: fare-watch

on:
  workflow_dispatch: {}
  schedule:
    - cron: "15 05 * * *"
    - cron: "15 16 * * *"

permissions:
  contents: read

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Show repo layout
        run: |
          pwd
          ls -la
          echo "---- .github/workflows ----"
          ls -la .github/workflows || true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests

      - name: Sanity-check required env vars
        env:
          AMADEUS_CLIENT_ID: ${{ secrets.AMADEUS_CLIENT_ID }}
          AMADEUS_CLIENT_SECRET: ${{ secrets.AMADEUS_CLIENT_SECRET }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          RECIPIENT_EMAIL: ${{ secrets.RECIPIENT_EMAIL }}
        run: |
          python - <<'PY'
          import os, sys
          required = [
              "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET",
              "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
              "RECIPIENT_EMAIL"
          ]
          missing = [k for k in required if not os.getenv(k)]
          if missing:
              print("Missing required env vars:", ", ".join(missing))
              sys.exit(1)
          print("All required env vars are present.")
          PY

      - name: Run price watcher (Amadeus TEST environment)
        env:
          # === Amadeus (Self-Service) ===
          AMADEUS_CLIENT_ID: ${{ secrets.AMADEUS_CLIENT_ID }}
          AMADEUS_CLIENT_SECRET: ${{ secrets.AMADEUS_CLIENT_SECRET }}
          # Force TEST base for first runs:
          AMADEUS_BASE: https://test.api.amadeus.com

          # === Gmail SMTP ===
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          RECIPIENT_EMAIL: ${{ secrets.RECIPIENT_EMAIL }}

          # === Your rules ===
          ORIGIN: BER
          MAX_PRICE_EUR: "80"
          DAYS_AHEAD: "180"
        run: |
          python -X dev - <<'PY'
          import os, sys, traceback
          # Ensure script is importable / present
          if not os.path.exists("fare_watch.py"):
              print("ERROR: 'fare_watch.py' not found in repo root.")
              sys.exit(1)
          try:
              import fare_watch  # import to catch syntax errors early
          except Exception as e:
              print("ERROR importing fare_watch.py:\n")
              traceback.print_exc()
              sys.exit(1)
          try:
              # re-run the actual script as a program so __name__ == "__main__"
              import runpy
              runpy.run_path("fare_watch.py", run_name="__main__")
          except SystemExit as se:
              raise
          except Exception:
              traceback.print_exc()
              sys.exit(1)
          PY

