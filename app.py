import requests
from bs4 import BeautifulSoup
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
import re
import threading
from flask import Flask, render_template

# --- GLOBALE VARIABELEN VOOR DE WEBPAGINA ---
wijzigingen_data = {}
laatste_update = "Nog niet uitgevoerd"
scraper_status = "Aan het opstarten..."
data_lock = threading.Lock()

# --- FLASK APPLICATIE ---
app = Flask(__name__)

@app.route('/')
def home():
    """Deze functie wordt aangeroepen als iemand de webpagina bezoekt."""
    with data_lock:
        return render_template('index.html', data=wijzigingen_data, laatste_update=laatste_update, status=scraper_status)

# --- SCRAPER CONFIGURATIE ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
gebruikersnaam = "mscbel10"
wachtwoord = "Drieslotte27!"
login_url = "https://lis.loodswezen.be/Lis/Login.aspx"
bestellingen_url = "https://lis.loodswezen.be/Lis/Loodsbestellingen.aspx"
table_id = 'ctl00_ContentPlaceHolder1_ctl01_list_gv'

# --- SCRAPER FUNCTIES (login, haal_bestellingen_op, etc.) ---
# Deze functies blijven ongewijzigd en worden hier voor de duidelijkheid weggelaten.
# Plak hier al je functies van 'login' tot en met 'format_wijzigingen'.
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
        for bestelling in dubbele_bestellingen:
            try:
                besteltijd_str = bestelling.get("Besteltijd")
                if besteltijd_str:
                    parsed_tijd = datetime.strptime(besteltijd_str, "%d/%m/%y %H:%M")
                    if parsed_tijd >= nu: toekomstige_orders.append((parsed_tijd, bestelling))
            except (ValueError, TypeError): continue
        if toekomstige_orders:
            toekomstige_orders.sort(key=lambda x: x[0])
            gefilterde_lijst.append(toekomstige_orders[0][1])
    return gefilterde_lijst

def vergelijk_bestellingen(oude_bestellingen, nieuwe_bestellingen):
    oude_bestellingen_uniek = filter_dubbele_schepen(oude_bestellingen)
    nieuwe_bestellingen_uniek = filter_dubbele_schepen(nieuwe_bestellingen)
    wijzigingen = []
    nu = datetime.now()
    oude_dict = {re.sub(r'\s*\(d\)\s*$', '', b.get('Schip', '')).strip(): b for b in oude_bestellingen_uniek if b.get('Schip')}
    for nieuwe_bestelling_val in nieuwe_bestellingen_uniek:
        schip_naam_raw = nieuwe_bestelling_val.get('Schip')
        if not schip_naam_raw: continue
        schip_naam_gekuist = re.sub(r'\s*\(d\)\s*$', '', schip_naam_raw).strip()
        if schip_naam_gekuist not in oude_dict: continue
        oude_bestelling_val = oude_dict[schip_naam_gekuist]
        gewijzigde_velden_details = {}
        is_effectief_gewijzigd = False
        for key, nieuwe_waarde in nieuwe_bestelling_val.items():
            oude_waarde = oude_bestelling_val.get(key)
            if key == 'Schip':
                if re.sub(r'\s*\(d\)\s*$', '', nieuwe_waarde).strip() != re.sub(r'\s*\(d\)\s*$', '', oude_waarde or '').strip():
                    is_effectief_gewijzigd = True
                    gewijzigde_velden_details[key] = {'oud': oude_waarde or '(leeg)', 'nieuw': nieuwe_waarde}
            elif nieuwe_waarde != oude_waarde:
                is_effectief_gewijzigd = True
                gewijzigde_velden_details[key] = {'oud': oude_waarde or '(leeg)', 'nieuw': nieuwe_waarde}
        if is_effectief_gewijzigd:
            if not nieuwe_bestelling_val.get('Besteltijd', '').strip(): continue
            relevante_velden = {'Besteltijd', 'ETA/ETD', 'Loods'}
            if not relevante_velden.intersection(set(gewijzigde_velden_details.keys())): continue
            rapporteer_wijziging = True
            type_schip = nieuwe_bestelling_val.get('Type')
            if type_schip == 'I':
                if len(gewijzigde_velden_details) == 1 and 'ETA/ETD' in gewijzigde_velden_details: rapporteer_wijziging = False
                if rapporteer_wijziging and oude_bestelling_val.get("Besteltijd"):
                    try:
                        if datetime.strptime(oude_bestelling_val.get("Besteltijd"), "%d/%m/%y %H:%M") > (nu + timedelta(hours=8)): rapporteer_wijziging = False
                    except (ValueError, TypeError): pass
            elif type_schip == 'U':
                if nieuwe_bestelling_val.get("Besteltijd"):
                    try:
                        if datetime.strptime(nieuwe_bestelling_val.get("Besteltijd"), "%d/%m/%y %H:%M") > (nu + timedelta(hours=16)): rapporteer_wijziging = False
                    except (ValueError, TypeError): pass
            if rapporteer_wijziging and 'zeebrugge' in nieuwe_bestelling_val.get('Entry Point', '').lower(): rapporteer_wijziging = False
            if rapporteer_wijziging:
                wijzigingen.append({'Schip': schip_naam_raw, 'Schip_gekuist': schip_naam_gekuist, 'Type': type_schip, 'wijzigingen': gewijzigde_velden_details, 'oude_bestelling': oude_bestelling_val, 'nieuwe_bestelling': nieuwe_bestelling_val})
    return wijzigingen

def format_wijzigingen(wijzigingen):
    formatted_wijzigingen = defaultdict(list)
    kolom_volgorde_weergave = ["Schip", "Besteltijd", "Entry Point", "ETA/ETD", "RTA", "Loods"]
    for wijziging in wijzigingen:
        schip_naam_display = re.sub(r'\s*\(d\)\s*$', '', wijziging.get('Schip', 'Onbekend Schip')).strip()
        type_schip = wijziging.get('Type', '')
        nieuwe_details = wijziging['nieuwe_bestelling']
        current_formatted_string = f"Voor schip '{schip_naam_display}' zijn deze wijzigingen gevonden:\n"
        specifieke_wijzigingen_tekst = [f"   {veld_key}: '{waarden['oud']}' -> '{waarden['nieuw']}'" for veld_key, waarden in wijziging['wijzigingen'].items()]
        current_formatted_string += "\n".join(specifieke_wijzigingen_tekst) + "\n\n   Volledige details na wijziging:\n"
        for key in kolom_volgorde_weergave:
            value = nieuwe_details.get(key, "")
            if key == "Schip": value = re.sub(r'\s*\(d\)\s*$', '', value).strip()
            current_formatted_string += f"     {key}: {value}\n"
        type_map = {"U": "UITGAAND", "I": "INKOMEND", "V": "SHIFTING"}
        formatted_wijzigingen[type_map.get(type_schip, "ALGEMEEN")].append(current_formatted_string)
    return dict(formatted_wijzigingen)
    
# --- HERSCHREVEN SCRAPER WORKER ---
def scraper_worker():
    """Deze functie draait continu in de achtergrond en is nu robuuster."""
    global wijzigingen_data, laatste_update, scraper_status
    
    session = requests.Session()
    oude_bestellingen = []
    is_logged_in = False
    wachttijd_seconden = 60

    while True:
        try:
            if not is_logged_in:
                with data_lock:
                    scraper_status = "Proberen in te loggen..."
                logging.info("Scraper: Poging tot inloggen...")
                is_logged_in = login(session)
                if not is_logged_in:
                    with data_lock:
                        scraper_status = "Inloggen mislukt. Volgende poging over 60s."
                    logging.error("Scraper: Inloggen mislukt, wachten...")
                    time.sleep(wachttijd_seconden)
                    continue # Ga naar de volgende iteratie van de loop
                
                # Na succesvolle login, haal de eerste data op als basislijn
                oude_bestellingen = haal_bestellingen_op(session)
                logging.info(f"Scraper: Eerste set van {len(oude_bestellingen)} bestellingen geladen.")
                with data_lock:
                    scraper_status = f"Ingelogd. Monitoren van {len(oude_bestellingen)} bestellingen."
            
            # Wacht voor de volgende controle
            logging.info(f"Scraper: Wachten voor {wachttijd_seconden} seconden...")
            time.sleep(wachttijd_seconden)

            # Haal nieuwe data op
            with data_lock:
                scraper_status = "Nieuwe data ophalen..."
            nieuwe_bestellingen = haal_bestellingen_op(session)

            # Controleer of de sessie verlopen is
            if not nieuwe_bestellingen and 'login' in session.get(bestellingen_url, timeout=10).url.lower():
                logging.warning("Scraper: Sessie lijkt verlopen. Zal opnieuw proberen in te loggen.")
                is_logged_in = False
                with data_lock:
                    scraper_status = "Sessie verlopen. Opnieuw inloggen..."
                continue

            if nieuwe_bestellingen:
                wijzigingen = vergelijk_bestellingen(oude_bestellingen, nieuwe_bestellingen)
                
                with data_lock:
                    if wijzigingen:
                        logging.info(f"Scraper: {len(wijzigingen)} relevante wijziging(en) gedetecteerd.")
                        wijzigingen_data = format_wijzigingen(wijzigingen)
                    else:
                        logging.info("Scraper: Geen relevante wijzigingen gevonden.")
                        wijzigingen_data = {} # Leeg de data als er geen wijzigingen meer zijn
                    
                    laatste_update = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
                    scraper_status = f"Actief. Laatste check: {laatste_update}. {len(wijzigingen)} wijzigingen gevonden."
                
                oude_bestellingen = nieuwe_bestellingen
            else:
                logging.warning("Scraper: Geen nieuwe bestellingen opgehaald in deze ronde.")

        except Exception as e:
            logging.error(f"Scraper: Onverwachte fout in de hoofdloop: {e}")
            is_logged_in = False # Forceer nieuwe loginpoging na een crash
            with data_lock:
                scraper_status = f"Fout opgetreden: {e}. Herstarten..."
            time.sleep(30) # Wacht even na een onverwachte fout

# --- START DE ACHTERGROND SCRAPER ---
logging.info("Starten van de scraper in de achtergrond...")
scraper_thread = threading.Thread(target=scraper_worker, daemon=True)
scraper_thread.start()
