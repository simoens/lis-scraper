import requests
from bs4 import BeautifulSoup
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
import re
import threading
from flask import Flask, render_template, jsonify
import os

# --- CONFIGURATIE ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- GLOBALE STATE DICTIONARY ---
app_state = {
    "wijzigingen_data": {}, "initiële_schepen_data": None,
    "laatste_update": "Nog niet uitgevoerd", "scraper_status": "Inactief. Bezoek /debug-run om te starten.",
    "laatste_update_timestamp": 0
}
data_lock = threading.Lock()

# --- FLASK APPLICATIE ---
app = Flask(__name__)

@app.route('/')
def home():
    with data_lock:
        return render_template('index.html', **app_state)

@app.route('/api/updates')
def api_updates():
    with data_lock:
        return jsonify(app_state)

# --- NIEUWE DEBUG ROUTE ---
@app.route('/debug-run')
def debug_run():
    """Voert één volledige scraper-cyclus uit in de main thread."""
    global app_state
    logging.info("---[DEBUG-RUN]--- Handmatige scraper-run gestart.")
    session = requests.Session()
    
    with data_lock:
        app_state["scraper_status"] = "Handmatige run: Bezig met inloggen..."
    
    login_gelukt = login(session)
    
    if not login_gelukt:
        with data_lock:
            app_state["scraper_status"] = "Handmatige run: INLOGGEN MISLUKT."
        logging.error("---[DEBUG-RUN]--- INLOGGEN MISLUKT.")
        return "DEBUG: INLOGGEN MISLUKT", 500

    logging.info("---[DEBUG-RUN]--- Inloggen gelukt. Data ophalen...")
    bestellingen = haal_bestellingen_op(session)
    if not bestellingen:
        with data_lock:
            app_state["scraper_status"] = "Handmatige run: DATA OPHALEN MISLUKT (lege lijst)."
        logging.error("---[DEBUG-RUN]--- DATA OPHALEN MISLUKT.")
        return "DEBUG: DATA OPHALEN MISLUKT", 500
        
    logging.info(f"---[DEBUG-RUN]--- {len(bestellingen)} bestellingen opgehaald. Filteren...")
    snapshot_data = filter_initiële_schepen(bestellingen)
    
    with data_lock:
        app_state["initiële_schepen_data"] = snapshot_data
        count_i = len(snapshot_data.get('INKOMEND', []))
        count_u = len(snapshot_data.get('UITGAAND', []))
        app_state["scraper_status"] = f"Handmatige run VOLTOOID. Snapshot gemaakt: {count_i} inkomend, {count_u} uitgaand."
        app_state["laatste_update"] = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        app_state["laatste_update_timestamp"] = int(time.time())
    
    logging.info("---[DEBUG-RUN]--- Handmatige run succesvol voltooid.")
    return "OK: Debug run voltooid. Ga terug naar de hoofdpagina en ververs.", 200

# --- SCRAPER CONFIGURATIE ---
gebruikersnaam = os.environ.get('LIS_USER')
wachtwoord = os.environ.get('LIS_PASS')
login_url = "https://lis.loodswezen.be/Lis/Login.aspx"
bestellingen_url = "https://lis.loodswezen.be/Lis/Loodsbestellingen.aspx"
table_id = 'ctl00_ContentPlaceHolder1_ctl01_list_gv'
if not gebruikersnaam or not wachtwoord:
    logging.critical("FATALE FOUT: LIS_USER of LIS_PASS niet ingesteld!")

# --- SCRAPER FUNCTIES (ongewijzigd) ---
def login(session):
    try:
        logging.info("Loginpoging gestart...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"}
        get_response = session.get(login_url, headers=headers)
        get_response.raise_for_status()
        soup = BeautifulSoup(get_response.text, 'html.parser')
        viewstate = soup.find('input', {'name': '__VIEWSTATE'})
        viewstategenerator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
        if not viewstate: return False
        form_data = {
            '__VIEWSTATE': viewstate['value'], '__VIEWSTATEGENERATOR': viewstategenerator['value'] if viewstategenerator else '',
            'ctl00$ContentPlaceHolder1$login$uname': gebruikersnaam, 'ctl00$ContentPlaceHolder1$login$password': wachtwoord,
            'ctl00$ContentPlaceHolder1$login$btnInloggen': 'Inloggen'}
        login_response = session.post(login_url, data=form_data, headers=headers)
        login_response.raise_for_status()
        if "Login.aspx" not in login_response.url:
            logging.info("LOGIN SUCCESVOL!")
            return True
        logging.error("Login Mislukt (terug op loginpagina).")
        return False
    except Exception as e:
        logging.error(f"Fout tijdens login: {e}")
        return False

# ... (Plak hier de rest van je ongewijzigde functies: haal_bestellingen_op, filter_dubbele_schepen, etc.) ...
def haal_bestellingen_op(session):
    try:
        response = session.get(bestellingen_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        table = soup.find('table', id=table_id)
        if table is None: return []
        kolom_indices = {"Type": 0, "Besteltijd": 5, "ETA/ETD": 6, "RTA": 7, "Loods": 10, "Schip": 11, "Entry Point": 20}
        bestellingen = []
        for row in table.find_all('tr')[1:]:
            kolom_data = row.find_all('td')
            if not kolom_data: continue
            bestelling = {k: kolom_data[i].get('title','').strip() if k=="RTA" else kolom_data[i].text.strip() for k, i in kolom_indices.items() if i < len(kolom_data)}
            bestellingen.append(bestelling)
        return bestellingen
    except Exception as e:
        logging.error(f"Error bij ophalen bestellingen: {e}")
        return []

def filter_initiële_schepen(bestellingen):
    gefilterd = {"INKOMEND": [], "UITGAAND": []}
    nu = datetime.now()
    grens_uit_toekomst = nu + timedelta(hours=16)
    grens_in_verleden = nu - timedelta(hours=8)
    grens_in_toekomst = nu + timedelta(hours=8)
    for b in bestellingen:
        try:
            if b.get("Besteltijd"):
                besteltijd = datetime.strptime(b.get("Besteltijd"), "%d/%m/%y %H:%M")
                if b.get("Type") == "U" and nu <= besteltijd <= grens_uit_toekomst: gefilterd["UITGAAND"].append(b)
                elif b.get("Type") == "I" and grens_in_verleden <= besteltijd <= grens_in_toekomst: gefilterd["INKOMEND"].append(b)
        except (ValueError, TypeError): continue
    return gefilterd

# --- START ---
# De automatische start van de scraper-thread is hier verwijderd.
