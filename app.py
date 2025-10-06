import requests
from bs4 import BeautifulSoup
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
import re
import threading
from flask import Flask, render_template, jsonify

# --- CONFIGURATIE ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- GLOBALE VARIABELEN ---
wijzigingen_data = {}
initiële_schepen_data = None # NIEUW: Voor de start-tabel
laatste_update = "Nog niet uitgevoerd"
scraper_status = "Aan het opstarten..."
laatste_update_timestamp = 0
data_lock = threading.Lock()

# --- FLASK APPLICATIE ---
app = Flask(__name__)

@app.route('/')
def home():
    with data_lock:
        # AANGEPAST: Geef de initiële data nu ook mee
        return render_template('index.html', 
                               data=wijzigingen_data, 
                               initial_snapshot=initiële_schepen_data,
                               laatste_update=laatste_update, 
                               status=scraper_status, 
                               timestamp=laatste_update_timestamp)

@app.route('/api/updates')
def api_updates():
    with data_lock:
        # AANGEPAST: Geef de initiële data nu ook mee in de API
        return jsonify({
            'timestamp': laatste_update_timestamp,
            'data': wijzigingen_data,
            'initial_snapshot': initiële_schepen_data
        })

# --- SCRAPER CONFIGURATIE ---
# ... (deze sectie blijft ongewijzigd) ...
gebruikersnaam = "mscbel10"
wachtwoord = "Drieslotte27!"
login_url = "https://lis.loodswezen.be/Lis/Login.aspx"
bestellingen_url = "https://lis.loodswezen.be/Lis/Loodsbestellingen.aspx"
table_id = 'ctl00_ContentPlaceHolder1_ctl01_list_gv'

# --- SCRAPER FUNCTIES ---
# ... (alle functies van login tot format_wijzigingen blijven ongewijzigd) ...
def login(session):
    try:
        logging.info("Loginpoging gestart...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"}
        get_response = session.get(login_url, headers=headers)
        get_response.raise_for_status()
        soup = BeautifulSoup(get_response.text, 'html.parser')
        viewstate = soup.find('input', {'name': '__VIEWSTATE'})
        viewstategenerator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
        if not viewstate:
            logging.error("Kritieke fout: __VIEWSTATE niet gevonden op de loginpagina.")
            return False
        form_data = {
            '__EVENTTARGET': '','__EVENTARGUMENT': '','__LASTFOCUS': '',
            '__VIEWSTATE': viewstate['value'],
            '__VIEWSTATEGENERATOR': viewstategenerator['value'] if viewstategenerator else '',
            'ctl00$ContentPlaceHolder1$login$uname': gebruikersnaam,
            'ctl00$ContentPlaceHolder1$login$password': wachtwoord,
            'ctl00$ContentPlaceHolder1$login$btnInloggen': 'Inloggen'}
        logging.info("Dynamische formulierwaarden gevonden. Versturen van POST request...")
        login_response = session.post(login_url, data=form_data, headers=headers)
        login_response.raise_for_status()
        if "Login.aspx" not in login_response.url and "Loodsbestellingen" in login_response.text:
            logging.info("Succesvol ingelogd!")
            return True
        else:
            logging.error("Login mislukt. De server stuurde ons terug naar de loginpagina.")
            return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Fout tijdens de loginprocedure (netwerkprobleem): {e}")
        return False
    except Exception as e:
        logging.error(f"Onverwachte fout tijdens login: {e}")
        return False

def haal_bestellingen_op(session):
    try:
        response = session.get(bestellingen_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        table = soup.find('table', id=table_id)
        if table is None:
            logging.error(f"Tabel met bestellingen niet gevonden. Gezocht met id: {table_id}")
            if "Login.aspx" in response.url: logging.error("Fout: Sessie is verlopen. We zijn terug op de loginpagina.")
            return []
        kolom_indices = {"Type": 0, "Besteltijd": 5, "ETA/ETD": 6, "RTA": 7, "Loods": 10, "Schip": 11, "Entry Point": 20}
        bestellingen = []
        for row in table.find_all('tr')[1:]:
            kolom_data = row.find_all('td')
            if not kolom_data: continue
            bestelling = {}
            for kolom_naam, index in kolom_indices.items():
                if index < len(kolom_data):
                    cel_data = kolom_data[index]
                    bestelling[kolom_naam] = cel_data.get('title', '').strip() if kolom_naam == "RTA" else cel_data.text.strip()
                else: bestelling[kolom_naam] = ""
            bestellingen.append(bestelling)
        return bestellingen
    except requests.exceptions.RequestException as e:
        logging.error(f"Error bij ophalen bestellingen: {e}")
        return []
    except Exception as e:
        logging.error(f"Onverwachte error: {e}")
        return []

def filter_dubbele_schepen(bestellingen_lijst):
    schepen_gegroepeerd = defaultdict(list)
    for bestelling in bestellingen_lijst:
        schip_naam = bestelling.get('Schip')
        if schip_naam:
            schip_naam_gekuist = re.sub(r'\s*\(d\)\s*$', '', schip_naam).strip()
            schepen_gegroepeerd[schip_naam_gekuist].append(bestelling)
    
    gefilterde_lijst = []
    nu = datetime.now()

    for schip_naam_gekuist, dubbele_bestellingen in schepen_gegroepeerd.items():
        if len(dubbele_bestellingen) == 1:
            gefilterde_lijst.append(dubbele_bestellingen[0])
            continue

        toekomstige_orders = []
        # HIER IS DE FIX: De ':' is toegevoegd aan het einde van de volgende regel
        for bestelling in dubbele_bestellingen:
            try:
                besteltijd_str = bestelling.get("Besteltijd")
                if besteltijd_str:
                    parsed_tijd = datetime.strptime(besteltijd_str, "%d/%m/%y %H:%M")
                    if parsed_tijd >= nu:
                        toekomstige_orders.append((parsed_tijd, bestelling))
            except (ValueError, TypeError):
                continue
        
        if toekomstige_orders:
            toekomstige_orders.sort(key=lambda x: x[0])
            gefilterde_lijst.append(toekomstige_orders[0][1])
            
    return gefilterde_lijst
