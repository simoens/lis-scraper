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

# --- GLOBALE VARIABELEN ---
# Deze dictionary houdt de volledige staat van de app bij.
app_state = {
    "wijzigingen_data": {},
    "initiële_schepen_data": None,
    "laatste_update": "Nog niet uitgevoerd",
    "scraper_status": "Aan het opstarten...",
    "laatste_update_timestamp": 0
}
data_lock = threading.Lock()

# --- FLASK APPLICATIE ---
app = Flask(__name__)

@app.route('/')
def home():
    with data_lock:
        # Geef de volledige state mee aan de template
        return render_template('index.html', **app_state)

@app.route('/api/updates')
def api_updates():
    with data_lock:
        return jsonify(app_state)

# --- SCRAPER CONFIGURATIE ---
gebruikersnaam = os.environ.get('LIS_USER')
wachtwoord = os.environ.get('LIS_PASS')
login_url = "https://lis.loodswezen.be/Lis/Login.aspx"
bestellingen_url = "https://lis.loodswezen.be/Lis/Loodsbestellingen.aspx"
table_id = 'ctl00_ContentPlaceHolder1_ctl01_list_gv'

if not gebruikersnaam or not wachtwoord:
    logging.critical("FATALE FOUT: LIS_USER of LIS_PASS niet ingesteld in Render Environment Variables!")

# --- SCRAPER FUNCTIES (login, haal_bestellingen_op, etc. blijven ongewijzigd) ---
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
            logging.info("Succesvol ingelogd!")
            return True
        return False
    except Exception as e:
        logging.error(f"Fout tijdens login: {e}")
        return False

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

def filter_dubbele_schepen(bestellingen_lijst):
    schepen_gegroepeerd = defaultdict(list)
    for bestelling in bestellingen_lijst:
        schip_naam = bestelling.get('Schip')
        if schip_naam: schepen_gegroepeerd[re.sub(r'\s*\(d\)\s*$', '', schip_naam).strip()].append(bestelling)
    gefilterde_lijst = []
    nu = datetime.now()
    for schip_naam_gekuist, dubbele_bestellingen in schepen_gegroepeerd.items():
        if len(dubbele_bestellingen) == 1:
            gefilterde_lijst.append(dubbele_bestellingen[0])
            continue
        toekomstige_orders = []
        for bestelling in dubbele_bestellingen:
            try:
                if bestelling.get("Besteltijd"):
                    parsed_tijd = datetime.strptime(bestelling.get("Besteltijd"), "%d/%m/%y %H:%M")
                    if parsed_tijd >= nu: toekomstige_orders.append((parsed_tijd, bestelling))
            except (ValueError, TypeError): continue
        if toekomstige_orders:
            toekomstige_orders.sort(key=lambda x: x[0])
            gefilterde_lijst.append(toekomstige_orders[0][1])
    return gefilterde_lijst

def vergelijk_bestellingen(oude, nieuwe):
    oude_dict = {re.sub(r'\s*\(d\)\s*$', '', b.get('Schip', '')).strip(): b for b in filter_dubbele_schepen(oude) if b.get('Schip')}
    wijzigingen = []
    nu = datetime.now()
    for n_best in filter_dubbele_schepen(nieuwe):
        n_schip_raw = n_best.get('Schip')
        if not n_schip_raw: continue
        n_schip_gekuist = re.sub(r'\s*\(d\)\s*$', '', n_schip_raw).strip()
        if n_schip_gekuist not in oude_dict: continue
        o_best = oude_dict[n_schip_gekuist]
        diff = {k: {'oud': o_best.get(k), 'nieuw': v} for k, v in n_best.items() if v != o_best.get(k)}
        if diff:
            if not n_best.get('Besteltijd', '').strip(): continue
            relevante = {'Besteltijd', 'ETA/ETD', 'Loods'}
            if not relevante.intersection(diff.keys()): continue
            rapporteer = True
            type_schip = n_best.get('Type')
            try:
                if type_schip == 'I':
                    if len(diff) == 1 and 'ETA/ETD' in diff: rapporteer = False
                    if rapporteer and o_best.get("Besteltijd") and datetime.strptime(o_best.get("Besteltijd"), "%d/%m/%y %H:%M") > (nu + timedelta(hours=8)): rapporteer = False
                elif type_schip == 'U':
                    if n_best.get("Besteltijd") and datetime.strptime(n_best.get("Besteltijd"), "%d/%m/%y %H:%M") > (nu + timedelta(hours=16)): rapporteer = False
            except (ValueError, TypeError): pass
            if rapporteer and 'zeebrugge' in n_best.get('Entry Point', '').lower(): rapporteer = False
            if rapporteer: wijzigingen.append({'Schip': n_schip_raw, 'wijzigingen': diff, 'nieuwe_bestelling': n_best})
    return wijzigingen

def format_wijzigingen(wijzigingen):
    formatted = defaultdict(list)
    for w in wijzigingen:
        s_naam = re.sub(r'\s*\(d\)\s*$', '', w.get('Schip', '')).strip()
        tekst = f"Wijziging voor '{s_naam}':\n"
        tekst += "\n".join([f"   - {k}: '{v['oud']}' -> '{v['nieuw']}'" for k, v in w['wijzigingen'].items()])
        type_map = {"U": "UITGAAND", "I": "INKOMEND", "V": "SHIFTING"}
        formatted[type_map.get(w['nieuwe_bestelling'].get('Type'), "ALGEMEEN")].append(tekst)
    return dict(formatted)

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

# --- DEFINITIEVE SCRAPER WORKER ---
def scraper_worker():
    global app_state
    session = requests.Session()
    oude_bestellingen = []
    is_logged_in = False
    
    while True:
        try:
            if not is_logged_in:
                with data_lock:
                    app_state["scraper_status"] = "Proberen in te loggen..."
                    app_state["laatste_update_timestamp"] = int(time.time())
                is_logged_in = login(session)

                if is_logged_in:
                    oude_bestellingen = haal_bestellingen_op(session)
                    with data_lock:
                        app_state["initiële_schepen_data"] = filter_initiële_schepen(oude_bestellingen)
                        count_i = len(app_state["initiële_schepen_data"].get('INKOMEND', []))
                        count_u = len(app_state["initiële_schepen_data"].get('UITGAAND', []))
                        app_state["scraper_status"] = f"Ingelogd. Start-snapshot: {count_i} inkomend, {count_u} uitgaand."
                        app_state["laatste_update"] = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
                        app_state["laatste_update_timestamp"] = int(time.time())
                else:
                    with data_lock:
                        app_state["scraper_status"] = "Inloggen mislukt. Volgende poging over 60s."
                    time.sleep(60)
                    continue

            time.sleep(60)
            nieuwe_bestellingen = haal_bestellingen_op(session)

            if not nieuwe_bestellingen:
                is_logged_in = False # Forceer her-login
                with data_lock: app_state["scraper_status"] = "Data ophalen mislukt, sessie mogelijk verlopen. Herstarten..."
                continue

            wijzigingen = vergelijk_bestellingen(oude_bestellingen, nieuwe_bestellingen)
            
            with data_lock:
                if wijzigingen:
                    if app_state["initiële_schepen_data"] is not None:
                        app_state["initiële_schepen_data"] = None # Verberg start-tabel
                    
                    app_state["wijzigingen_data"] = format_wijzigingen(wijzigingen)
                    app_state["scraper_status"] = f"{len(wijzigingen)} nieuwe wijziging(en) gevonden."
                else:
                    app_state["scraper_status"] = "Actief, geen nieuwe wijzigingen gevonden."
                
                app_state["laatste_update"] = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
                app_state["laatste_update_timestamp"] = int(time.time())

            oude_bestellingen = nieuwe_bestellingen

        except Exception as e:
            logging.error(f"FATALE FOUT in scraper_worker: {e}")
            is_logged_in = False
            with data_lock: app_state["scraper_status"] = "Ernstige fout opgetreden, herstarten..."
            time.sleep(30)

# --- START ---
scraper_thread = threading.Thread(target=scraper_worker, daemon=True)
scraper_thread.start()
