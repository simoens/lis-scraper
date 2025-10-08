import requests
from bs4 import BeautifulSoup
import logging
import time
from datetime import datetime, timedelta
import re
import os
import smtplib
from email.mime.text import MIMEText
import json

# --- CONFIGURATIE ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Inloggegevens en e-mailinstellingen uit Render Environment Variables halen
USER = os.environ.get('LIS_USER')
PASS = os.environ.get('LIS_PASS')
SMTP_SERVER = os.environ.get('SMTP_SERVER')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASS = os.environ.get('EMAIL_PASS')
ONTVANGER_EMAIL = os.environ.get('ONTVANGER_EMAIL')

# Pad naar het databestand op Render's gratis persistent disk
DATA_FILE_PATH = '/var/data/oude_bestellingen.json'

# --- FUNCTIES (login, haal_bestellingen_op, etc. zijn grotendeels hetzelfde) ---

def login(session):
    try:
        # ... (de login functie blijft exact hetzelfde als voorheen) ...
    except Exception as e:
        logging.error(f"Fout tijdens login: {e}")
        return False

def haal_bestellingen_op(session):
    try:
        # ... (de haal_bestellingen_op functie blijft exact hetzelfde als voorheen) ...
    except Exception as e:
        logging.error(f"Error bij ophalen bestellingen: {e}")
        return []

# ... (plak hier de ongewijzigde functies: filter_dubbele_schepen, vergelijk_bestellingen, format_wijzigingen) ...

def verstuur_email(onderwerp, inhoud):
    if not all([SMTP_SERVER, EMAIL_USER, EMAIL_PASS, ONTVANGER_EMAIL]):
        logging.error("E-mail niet verstuurd: SMTP-instellingen ontbreken in Environment Variables.")
        return
    try:
        msg = MIMEText(inhoud)
        msg['Subject'] = onderwerp
        msg['From'] = EMAIL_USER
        msg['To'] = ONTVANGER_EMAIL

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, ONTVANGER_EMAIL, msg.as_string())
        logging.info(f"E-mail succesvol verzonden naar {ONTVANGER_EMAIL}")
    except Exception as e:
        logging.error(f"E-mail versturen mislukt: {e}")

def main():
    """Hoofdfunctie die één keer draait."""
    logging.info("--- Cron Job Gestart ---")
    
    # 1. Laad de data van de vorige run
    oude_bestellingen = []
    if os.path.exists(DATA_FILE_PATH):
        try:
            with open(DATA_FILE_PATH, 'r') as f:
                oude_bestellingen = json.load(f)
            logging.info(f"{len(oude_bestellingen)} oude bestellingen geladen uit bestand.")
        except json.JSONDecodeError:
            logging.warning("Kon vorig databestand niet lezen (leeg of corrupt).")

    # 2. Log in en haal nieuwe data op
    session = requests.Session()
    if not login(session):
        logging.error("Inloggen mislukt. Script stopt.")
        return

    nieuwe_bestellingen = haal_bestellingen_op(session)
    if not nieuwe_bestellingen:
        logging.warning("Geen nieuwe bestellingen opgehaald. Script stopt.")
        return
    logging.info(f"{len(nieuwe_bestellingen)} nieuwe bestellingen opgehaald.")

    # 3. Vergelijk en verstuur e-mail indien nodig
    if oude_bestellingen:
        wijzigingen = vergelijk_bestellingen(oude_bestellingen, nieuwe_bestellingen)
        if wijzigingen:
            logging.info(f"{len(wijzigingen)} wijziging(en) gevonden!")
            inhoud = format_wijzigingen(wijzigingen) # Pas format_wijzigingen aan om een platte tekst terug te geven
            onderwerp = f"LIS Update: {len(wijzigingen)} wijziging(en) gedetecteerd"
            verstuur_email(onderwerp, inhoud)
        else:
            logging.info("Geen relevante wijzigingen gevonden.")
    else:
        logging.info("Eerste run, geen oude data om mee te vergelijken. Basislijn wordt opgeslagen.")

    # 4. Sla de nieuwe data op voor de volgende run
    try:
        os.makedirs(os.path.dirname(DATA_FILE_PATH), exist_ok=True)
        with open(DATA_FILE_PATH, 'w') as f:
            json.dump(nieuwe_bestellingen, f)
        logging.info(f"Nieuwe basislijn met {len(nieuwe_bestellingen)} bestellingen opgeslagen.")
    except Exception as e:
        logging.error(f"Kon nieuw databestand niet opslaan: {e}")

    logging.info("--- Cron Job Voltooid ---")

if __name__ == "__main__":
    main()
