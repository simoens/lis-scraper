import requests
from bs4 import BeautifulSoup
import logging
import time
from collections import OrderedDict, defaultdict
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONFIGURATIE ---
gebruikersnaam = "mscbel10"   # Vervang door je echte gebruikersnaam
wachtwoord = "Drieslotte27!"   # Vervang door je echte wachtwoord
# Gebruik de 'schone' login URL zonder extra parameters
login_url = "https://lis.loodswezen.be/Lis/Login.aspx"
BASE_URL = "https://lis.loodswezen.be/Lis/"
bestellingen_url = "https://lis.loodswezen.be/Lis/Loodsbestellingen.aspx"
table_id = 'ctl00_ContentPlaceHolder1_ctl01_list_gv'   # Correcte tabel ID

# E-mail configuratie
smtp_server = "smtp.gmail.com"   # Vervang door je SMTP-server
smtp_poort = 587   # Vervang door de poort van je SMTP-server
email_gebruiker = "simoens@gmail.com"   # Vervang door je e-mailadres
email_wachtwoord = "ntuo qwwr syme dqht"   # Vervang door je e-mailwachtwoord
ontvanger_email = "autolisupdate@gmail.com"   # Vervang door het e-mailadres van de ontvanger
# --- EINDE CONFIGURATIE ---


def verstuur_email(onderwerp, inhoud):
    """Verstuurt een e-mail."""
    try:
        msg = MIMEMultipart()
        msg['From'] = email_gebruiker
        msg['To'] = ontvanger_email
        msg['Subject'] = onderwerp
        msg.attach(MIMEText(inhoud, 'plain'))
        server = smtplib.SMTP(smtp_server, smtp_poort)
        server.starttls()
        server.login(email_gebruiker, email_wachtwoord)
        server.sendmail(email_gebruiker, ontvanger_email, msg.as_string())
        server.quit()
        logging.info(f"E-mail verzonden naar {ontvanger_email} met onderwerp '{onderwerp}'")
    except smtplib.SMTPAuthenticationError:
        logging.error("E-mail versturen mislukt: SMTP Authentication Error - Controleer gebruikersnaam en wachtwoord.")
    except smtplib.SMTPServerDisconnected:
        logging.error("E-mail versturen mislukt: Verbinding met de server verbroken.")
    except Exception as e:
        logging.error(f"E-mail versturen mislukt: {e}")


def login(session):
    """Logt in op de Loodswezen website door eerst de __VIEWSTATE op te halen."""
    try:
        logging.info("Loginpoging gestart...")
        
        # Headers om een echte browser na te bootsen
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        }

        # 1. Haal de loginpagina op om de dynamische formulier-waarden te krijgen
        logging.info(f"Ophalen van de loginpagina via GET: {login_url}")
        get_response = session.get(login_url, headers=headers)
        get_response.raise_for_status()
        soup = BeautifulSoup(get_response.text, 'html.parser')

        # 2. Zoek de vereiste __VIEWSTATE en andere verborgen velden
        viewstate = soup.find('input', {'name': '__VIEWSTATE'})
        viewstategenerator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})

        if not viewstate:
            logging.error("Kritieke fout: __VIEWSTATE niet gevonden op de loginpagina.")
            return False

        # 3. Stel de payload samen met de dynamische waarden
        form_data = {
            '__EVENTTARGET': '',
            '__EVENTARGUMENT': '',
            '__LASTFOCUS': '',
            '__VIEWSTATE': viewstate['value'],
            '__VIEWSTATEGENERATOR': viewstategenerator['value'] if viewstategenerator else '',
            'ctl00$ContentPlaceHolder1$login$uname': gebruikersnaam,
            'ctl00$ContentPlaceHolder1$login$password': wachtwoord,
            'ctl00$ContentPlaceHolder1$login$btnInloggen': 'Inloggen'
        }
        
        logging.info("Dynamische formulierwaarden gevonden. Versturen van POST request...")

        # 4. Voer het POST-verzoek uit om daadwerkelijk in te loggen
        login_response = session.post(login_url, data=form_data, headers=headers)
        login_response.raise_for_status()

        # 5. Controleer of de login succesvol was
        # Een succesvolle login leidt meestal naar een andere pagina.
        if "Login.aspx" not in login_response.url and "Loodsbestellingen" in login_response.text:
            logging.info("Succesvol ingelogd! Doorverwezen naar de juiste pagina.")
            return True
        else:
            logging.error("Login mislukt. De server stuurde ons terug naar de loginpagina.")
            # Optioneel: log de response text voor debugging, maar wees voorzichtig met gevoelige info
            # logging.debug(f"Response text na mislukte login: {login_response.text[:500]}")
            return False

    except requests.exceptions.RequestException as e:
        logging.error(f"Fout tijdens de loginprocedure (netwerkprobleem): {e}")
        return False
    except Exception as e:
        logging.error(f"Onverwachte fout tijdens login: {e}")
        return False


def haal_bestellingen_op(session):
    """Haalt bestellingen op van de Loodswezen website."""
    try:
        response = session.get(bestellingen_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        table = soup.find('table', id=table_id)
        if table is None:
            logging.error(f"Tabel met bestellingen niet gevonden. Gezocht met id: {table_id}")
            # Controleer of we nog steeds op de loginpagina zijn
            if "Login.aspx" in response.url:
                logging.error("Fout: Sessie is verlopen. We zijn terug op de loginpagina.")
            return []

        kolom_indices = {
            "Type": 0, "Besteltijd": 5, "ETA/ETD": 6, "RTA": 7, "Loods": 10,
            "Schip": 11, "Entry Point": 20,
        }

        bestellingen = []
        for row in table.find_all('tr')[1:]:
            kolom_data = row.find_all('td')
            if not kolom_data:
                continue

            bestelling = {}
            for kolom_naam, index in kolom_indices.items():
                if index < len(kolom_data):
                    cel_data = kolom_data[index]
                    if kolom_naam == "RTA":
                        bestelling[kolom_naam] = cel_data.get('title', '').strip()
                    else:
                        bestelling[kolom_naam] = cel_data.text.strip()
                else:
                    bestelling[kolom_naam] = ""
            bestellingen.append(bestelling)
        return bestellingen
    except requests.exceptions.RequestException as e:
        logging.error(f"Error bij ophalen bestellingen: {e}")
        return []
    except AttributeError as e:
        logging.error(f"AttributeError: {e}. Controleer tabel-ID en kolomindices.")
        return []
    except Exception as e:
        logging.error(f"Onverwachte error: {e}")
        return []

def filter_dubbele_schepen(bestellingen_lijst):
    """Filtert dubbele schepen en houdt de meest relevante toekomstige bestelling."""
    schepen_gegroepeerd = defaultdict(list)
    for bestelling in bestellingen_lijst:
        schip_naam = bestelling.get('Schip')
        if schip_naam:
            # Verwijder "(d)" uit de scheepsnaam voor groepering
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
                    if parsed_tijd >= nu:
                        toekomstige_orders.append((parsed_tijd, bestelling))
            except (ValueError, TypeError):
                logging.warning(f"Ongeldige Besteltijd-notatie voor {schip_naam_gekuist}: '{bestelling.get('Besteltijd')}' - wordt genegeerd in duplicaat-check.")
                continue

        if toekomstige_orders:
            toekomstige_orders.sort(key=lambda x: x[0])
            beste_keuze = toekomstige_orders[0][1]
            gefilterde_lijst.append(beste_keuze)
            logging.info(f"Duplicaat gevonden voor '{schip_naam_gekuist}'. Gekozen bestelling: {beste_keuze.get('Besteltijd')}")

    return gefilterde_lijst

def vergelijk_bestellingen(oude_bestellingen, nieuwe_bestellingen):
    """
    Vergelijkt twee lijsten van bestellingen en rapporteert wijzigingen
    op basis van specifieke filters.
    """
    oude_bestellingen_uniek = filter_dubbele_schepen(oude_bestellingen)
    nieuwe_bestellingen_uniek = filter_dubbele_schepen(nieuwe_bestellingen)

    wijzigingen = []
    nu = datetime.now()

    # Gebruik de 'gekuiste' scheepsnaam als sleutel voor de dicts
    oude_dict = {re.sub(r'\s*\(d\)\s*$', '', b.get('Schip', '')).strip(): b for b in oude_bestellingen_uniek if b.get('Schip')}

    for nieuwe_bestelling_val in nieuwe_bestellingen_uniek:
        schip_naam_raw = nieuwe_bestelling_val.get('Schip')
        if not schip_naam_raw:
            continue
        schip_naam_gekuist = re.sub(r'\s*\(d\)\s*$', '', schip_naam_raw).strip()

        if schip_naam_gekuist not in oude_dict:
            continue

        oude_bestelling_val = oude_dict[schip_naam_gekuist]
        gewijzigde_velden_details = {}
        is_effectief_gewijzigd = False
        for key, nieuwe_waarde in nieuwe_bestelling_val.items():
            oude_waarde = oude_bestelling_val.get(key)
            # Specifiek voor 'Schip' veld: vergelijk ook de gekuiste namen
            if key == 'Schip':
                nieuwe_waarde_gekuist = re.sub(r'\s*\(d\)\s*$', '', nieuwe_waarde).strip()
                oude_waarde_gekuist = re.sub(r'\s*\(d\)\s*$', '', oude_waarde or '').strip()
                if nieuwe_waarde_gekuist != oude_waarde_gekuist:
                    is_effectief_gewijzigd = True
                    gewijzigde_velden_details[key] = {'oud': oude_waarde or '(leeg)', 'nieuw': nieuwe_waarde}
            elif nieuwe_waarde != oude_waarde:
                is_effectief_gewijzigd = True
                gewijzigde_velden_details[key] = {'oud': oude_waarde or '(leeg)', 'nieuw': nieuwe_waarde}

        if is_effectief_gewijzigd:
            # --- ALGEMEEN FILTER: Alleen wijzigingen waar 'Besteltijd' is ingevuld ---
            if not nieuwe_bestelling_val.get('Besteltijd', '').strip():
                logging.info(f"Wijziging voor schip '{schip_naam_gekuist}' genegeerd: 'Besteltijd' is leeg in de nieuwe bestelling.")
                continue

            # --- FILTER 1: Controleer of een van de relevante velden is gewijzigd ---
            relevante_velden = {'Besteltijd', 'ETA/ETD', 'Loods'}
            gewijzigde_sleutels = set(gewijzigde_velden_details.keys())

            if not relevante_velden.intersection(gewijzigde_sleutels):
                logging.info(f"Wijziging voor schip '{schip_naam_gekuist}' genegeerd: geen van de relevante velden ({', '.join(relevante_velden)}) is gewijzigd.")
                continue

            rapporteer_wijziging = True
            type_schip = nieuwe_bestelling_val.get('Type')

            # --- FILTER 2: TIJDFILTERS PER SCHEEPSTYPE ---
            if type_schip == 'I':   # INKOMEND
                # Nieuwe filter: GEEN mails voor wijzigingen van ALLEEN "ETA/ETD"
                if len(gewijzigde_velden_details) == 1 and 'ETA/ETD' in gewijzigde_velden_details:
                    rapporteer_wijziging = False
                    logging.info(f"Wijziging voor INKOMEND schip '{schip_naam_gekuist}' genegeerd: Alleen ETA/ETD is gewijzigd.")

                # Bestaande filter voor INKOMEND (Besteltijd > 8u in de toekomst)
                if rapporteer_wijziging:
                    oude_besteltijd_str = oude_bestelling_val.get("Besteltijd")
                    if oude_besteltijd_str:
                        try:
                            parsed_oude_besteltijd = datetime.strptime(oude_besteltijd_str, "%d/%m/%y %H:%M")
                            if parsed_oude_besteltijd > (nu + timedelta(hours=8)):
                                rapporteer_wijziging = False
                                logging.info(f"Wijziging voor INKOMEND schip '{schip_naam_gekuist}' genegeerd: oude Besteltijd > 8u in de toekomst.")
                        except (ValueError, TypeError):
                            logging.warning(f"Kon OUDE Besteltijd '{oude_besteltijd_str}' niet parsen. Wijziging wordt meegenomen.")

            elif type_schip == 'U':   # UITGAAND
                nieuwe_besteltijd_str = nieuwe_bestelling_val.get("Besteltijd")
                if nieuwe_besteltijd_str:
                    try:
                        parsed_nieuwe_besteltijd = datetime.strptime(nieuwe_besteltijd_str, "%d/%m/%y %H:%M")
                        if parsed_nieuwe_besteltijd > (nu + timedelta(hours=16)):
                            rapporteer_wijziging = False
                            logging.info(f"Wijziging voor UITGAAND schip '{schip_naam_gekuist}' genegeerd: nieuwe Besteltijd > 16u in de toekomst.")
                    except (ValueError, TypeError):
                        logging.warning(f"Kon NIEUWE Besteltijd '{nieuwe_besteltijd_str}' niet parsen. Wijziging wordt meegenomen.")

            else:   # FALLBACK voor andere types (V, etc.) - 24u-regel
                besteltijd_str = nieuwe_bestelling_val.get("Besteltijd")
                if besteltijd_str:
                    try:
                        if datetime.strptime(besteltijd_str, "%d/%m/%y %H:%M") > (nu + timedelta(hours=24)):
                            rapporteer_wijziging = False
                            logging.info(f"Wijziging voor schip '{schip_naam_gekuist}' (Type {type_schip}) genegeerd: Besteltijd > 24u.")
                    except (ValueError, TypeError):
                        logging.warning(f"Kon Besteltijd '{besteltijd_str}' niet parsen voor fallback-check. Wijziging wordt meegenomen.")

            # --- FILTER 3: OVERIGE FILTERS ---
            if rapporteer_wijziging:
                entry_point_value = nieuwe_bestelling_val.get('Entry Point', '').lower()
                if 'zeebrugge' in entry_point_value:
                    rapporteer_wijziging = False
                    logging.info(f"Wijziging voor schip '{schip_naam_gekuist}' genegeerd: Entry Point bevat 'Zeebrugge'.")

                if rapporteer_wijziging:
                    wijzigingen.append({
                        'Schip': schip_naam_raw, # Bewaar hier de originele naam voor weergave in de mailinhoud
                        'Schip_gekuist': schip_naam_gekuist, # Gekuiste naam voor vergelijkingen
                        'Type': type_schip,
                        'wijzigingen': gewijzigde_velden_details,
                        'oude_bestelling': oude_bestelling_val,
                        'nieuwe_bestelling': nieuwe_bestelling_val
                    })
    return wijzigingen

def format_wijzigingen(wijzigingen):
    """Formatteert de wijzigingen voor weergave in de e-mail."""
    formatted_wijzigingen = {"UITGAAND": [], "INKOMEND": [], "SHIFTING": [], "ALGEMEEN": []}
    kolom_volgorde_weergave = ["Schip", "Besteltijd", "Entry Point", "ETA/ETD", "RTA", "Loods"]

    for wijziging in wijzigingen:
        schip_naam_origineel = wijziging.get('Schip', 'Onbekend Schip')
        schip_naam_display = re.sub(r'\s*\(d\)\s*$', '', schip_naam_origineel).strip() # Verwijder (d) voor weergave
        type_schip = wijziging.get('Type', '')
        nieuwe_details = wijziging['nieuwe_bestelling']

        current_formatted_string = f"Voor schip '{schip_naam_display}' zijn deze wijzigingen gevonden in het LIS:\n"

        specifieke_wijzigingen_tekst = []
        veld_wijzigingen = wijziging['wijzigingen']
        for veld_key, waarden in veld_wijzigingen.items():
            # Voor het 'Schip' veld, toon de gekuiste namen in de details als ze verschillen
            if veld_key == 'Schip':
                oud_display = re.sub(r'\s*\(d\)\s*$', '', waarden['oud']).strip() if waarden['oud'] != '(leeg)' else '(leeg)'
                nieuw_display = re.sub(r'\s*\(d\)\s*$', '', waarden['nieuw']).strip()
                specifieke_wijzigingen_tekst.append(f"   {veld_key}: Oude waarde: '{oud_display}' -> Nieuwe waarde: '{nieuw_display}'")
            else:
                specifieke_wijzigingen_tekst.append(f"   {veld_key}: Oude waarde: '{waarden['oud']}' -> Nieuwe waarde: '{waarden['nieuw']}'")


        if specifieke_wijzigingen_tekst:
            current_formatted_string += "\n".join(specifieke_wijzigingen_tekst) + "\n\n"

        current_formatted_string += "   Volledige details na wijziging:\n"
        for key in kolom_volgorde_weergave:
            value = nieuwe_details.get(key, "")
            # Verwijder "(d)" uit de scheepsnaam in de volledige details
            if key == "Schip":
                value = re.sub(r'\s*\(d\)\s*$', '', value).strip()
            current_formatted_string += f"     {key}: {value}\n"
        current_formatted_string += "\n"

        if type_schip == "U":
            formatted_wijzigingen["UITGAAND"].append(current_formatted_string)
        elif type_schip == "I":
            formatted_wijzigingen["INKOMEND"].append(current_formatted_string)
        elif type_schip == "V":
            formatted_wijzigingen["SHIFTING"].append(current_formatted_string)
        else:
            formatted_wijzigingen["ALGEMEEN"].append(current_formatted_string)

    return formatted_wijzigingen

def main():
    """Hoofdfunctie van het script."""
    session = requests.Session()
    if not login(session):
        logging.error("Inloggen mislukt, programma wordt gestopt.")
        session.close()
        return

    oude_bestellingen = []
    logging.info("Script gestart. Eerste set bestellingen ophalen als basislijn...")
    oude_bestellingen = haal_bestellingen_op(session)
    if not oude_bestellingen:
        logging.warning("Geen bestellingen gevonden bij de eerste ophaalronde.")
    else:
        logging.info(f"{len(oude_bestellingen)} bestellingen geladen als initiële basislijn.")

    wachttijd_seconden = 60
    logging.info(f"Wachten voor {wachttijd_seconden} seconden voor de eerste vergelijkingsronde...")
    time.sleep(wachttijd_seconden)

    while True:
        logging.info("Nieuwe ronde: bestellingen ophalen...")
        nieuwe_bestellingen = haal_bestellingen_op(session)
        
        # Extra controle: als ophalen mislukt, probeer opnieuw in te loggen
        if not nieuwe_bestellingen and 'login' in session.get(bestellingen_url).url.lower():
            logging.warning("Sessie lijkt verlopen. Poging tot opnieuw inloggen...")
            if not login(session):
                logging.error("Opnieuw inloggen mislukt. Programma stopt.")
                break
            # Probeer na succesvolle login nogmaals data op te halen
            nieuwe_bestellingen = haal_bestellingen_op(session)


        if not nieuwe_bestellingen:
            logging.info("Geen bestellingen opgehaald in deze ronde.")
        elif not oude_bestellingen and nieuwe_bestellingen:
            logging.info("Eerste succesvolle set bestellingen opgehaald. Wordt gebruikt als basislijn.")
            oude_bestellingen = nieuwe_bestellingen
        else:
            logging.info(f"Vergelijken van {len(oude_bestellingen)} oude met {len(nieuwe_bestellingen)} nieuwe bestellingen.")
            wijzigingen = vergelijk_bestellingen(oude_bestellingen, nieuwe_bestellingen)

            if wijzigingen:
                logging.info(f"{len(wijzigingen)} relevante wijziging(en) gedetecteerd na filtering.")
                formatted_wijzigingen = format_wijzigingen(wijzigingen)

                inhoud = ""
                if formatted_wijzigingen.get("UITGAAND"):
                    inhoud += "--- UITGAANDE SCHEPEN ---\n" + "\n".join(formatted_wijzigingen["UITGAAND"]) + "\n"
                if formatted_wijzigingen.get("INKOMEND"):
                    inhoud += "--- INKOMENDE SCHEPEN ---\n" + "\n".join(formatted_wijzigingen["INKOMEND"]) + "\n"
                if formatted_wijzigingen.get("SHIFTING"):
                    inhoud += "--- SHIFTING SCHEPEN ---\n" + "\n".join(formatted_wijzigingen["SHIFTING"]) + "\n"
                if formatted_wijzigingen.get("ALGEMEEN"):
                    inhoud += "--- OVERIGE WIJZIGINGEN ---\n" + "\n".join(formatted_wijzigingen["ALGEMEEN"]) + "\n"

                if inhoud.strip():
                    onderwerp = f"LIS UPDATE Algemene melding ({len(wijzigingen)} wijzigingen)"

                    if len(wijzigingen) == 1:
                        enkele_wijziging = wijzigingen[0]
                        # Gebruik de 'gekuiste' scheepsnaam voor het onderwerp
                        schip_naam_display_onderwerp = enkele_wijziging.get('Schip_gekuist', 'Onbekend')
                        type_schip_raw = enkele_wijziging.get('Type', '') # Originele type, bijv. "I", "U", "V"

                        # Convergeer het type naar de gewenste format
                        type_schip_display = ""
                        if type_schip_raw == "I":
                            type_schip_display = "[IN]"
                        elif type_schip_raw == "U":
                            type_schip_display = "[UIT]"
                        elif type_schip_raw == "V":
                            type_schip_display = "[SHIFT]"
                        else:
                            type_schip_display = f"[{type_schip_raw}]" # Fallback voor onbekende types

                        gewijzigde_velden = enkele_wijziging.get('wijzigingen', {})

                        # SPECIFIEKE REGEL VOOR 'LOODS' WIJZIGING VAN LEEG NAAR WAARDE
                        if 'Loods' in gewijzigde_velden and \
                           (not gewijzigde_velden['Loods'].get('oud', '').strip()) and \
                           gewijzigde_velden['Loods'].get('nieuw', '').strip():
                            onderwerp = f"LIS UPDATE : Loods toegewezen voor {schip_naam_display_onderwerp} {type_schip_display}"
                        # EINDE SPECIFIEKE REGEL
                        elif len(gewijzigde_velden) == 1 and 'ETA/ETD' in gewijzigde_velden:
                            nieuwe_waarde = gewijzigde_velden['ETA/ETD'].get('nieuw', '')
                            onderwerp = f"LIS UPDATE : {schip_naam_display_onderwerp} {type_schip_display} ETA/ETD -> {nieuwe_waarde}"

                        elif len(gewijzigde_velden) == 1:
                            veld_naam = list(gewijzigde_velden.keys())[0]
                            waarde_display = gewijzigde_velden[veld_naam].get('nieuw', '')
                            onderwerp = f"LIS UPDATE : {schip_naam_display_onderwerp} {type_schip_display} {veld_naam} -> {waarde_display}"

                        else:
                            onderwerp = f"LIS UPDATE : {schip_naam_display_onderwerp} {type_schip_display} Details gewijzigd"

                    elif len(wijzigingen) > 1:
                        # Haal de gekuiste naam van het eerste schip op voor het onderwerp
                        eerste_schip_naam_gekuist = wijzigingen[0].get('Schip_gekuist', 'onbekend')
                        onderwerp = f"LIS UPDATE {len(wijzigingen)} wijzigingen (o.a. {eerste_schip_naam_gekuist})"

                    logging.info(f"E-mail onderwerp wordt: {onderwerp}")
                    verstuur_email(onderwerp, inhoud)
                else:
                    logging.info("Wijzigingen gedetecteerd, maar resulteerde in lege e-mail na formattering.")
            else:
                logging.info("Geen relevante wijzigingen gevonden na filtering.")

        if nieuwe_bestellingen:
            oude_bestellingen = nieuwe_bestellingen

        logging.info(f"Wachten voor {wachttijd_seconden} seconden voor de volgende controle...")
        time.sleep(wachttijd_seconden)

if __name__ == "__main__":
    main()
