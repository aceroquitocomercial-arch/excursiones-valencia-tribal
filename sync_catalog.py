import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
import re
from datetime import datetime
import urllib.parse

# 1. Load credentials from .env
env_path = os.path.join(os.path.dirname(__file__), ".env")
env_vars = {}

if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip()

STORE_URL = os.environ.get("STORE_URL") or env_vars.get("STORE_URL", "https://valenciatribal.com")
WOO_CK = os.environ.get("WOO_CONSUMER_KEY") or env_vars.get("WOO_CONSUMER_KEY")
WOO_CS = os.environ.get("WOO_CONSUMER_SECRET") or env_vars.get("WOO_CONSUMER_SECRET")

if not WOO_CK or not WOO_CS:
    print("Error: WOO_CONSUMER_KEY or WOO_CONSUMER_SECRET not found in environment variables or .env file.")
    sys.exit(1)

auth = HTTPBasicAuth(WOO_CK, WOO_CS)

# Enable dry-run mode if passed as an argument
DRY_RUN = "--dry-run" in sys.argv or "-d" in sys.argv
if DRY_RUN:
    print("=== RUNNING IN DRY-RUN MODE (NO CHANGES WILL BE SAVED) ===")
else:
    print("=== RUNNING IN LIVE MODE ===")

# Keyword to WooCommerce Spanish Product ID mapping
# (Ordered from most specific to general to avoid partial keyword collisions)
KEYWORDS_MAPPING = {
    "cuevas de san jose": 48876,
    "cuevas de san josé": 48876,
    "san jose caves": 48876,
    "san josé caves": 48876,
    "alto de pipa": 48861,
    "pipa": 48861,
    "benidorm": 48877,
    "canon de mascarat": 48862,
    "cañon de mascarat": 48862,
    "cañón de mascarat": 48862,
    "cañón del mascarat": 48862,
    "mascarat canyon": 48862,
    "puenting": 48117,
    "bridge swing": 48117,
    "bungee": 48117,
    "algar & calpe": 48105,
    "algar and calpe": 48105,
    "algar y calpe": 48105,
    "algar & altea": 49314,
    "algar and altea": 49314,
    "algar y altea": 49314,
    "fuentes del algar": 49313,
    "algar waterfalls": 49313,
    "cueva de las calaveras": 47940,
    "calaveras": 47940,
    "denia boat": 48066,
    "dénia boat": 48066,
    "denia en barco": 48066,
    "dénia en barco": 48066,
    "montanejos": 47879,
    "tabarca": 47937,
    "cueva tallada": 48119,
    "tallada": 48119,
    "moraig": 48067,
    "moraira": 47941,
    "laguna rosa": 47939,
    "torrevieja": 47939,
    "pink lake": 47939,
    "guadalest": 47938,
    "altea": 47938,
    "sagunto": 47943,
    "peñiscola": 47943,
    "peñíscola": 47943,
    "peniscola": 47943,
    "calpe": 48104,
    "ifach": 48104,
    "pirineos franceses": 48123,
    "french pyrenees": 48123,
    "gavarnie": 48123,
    "pirineo catalan": 48121,
    "pirineo catalán": 48121,
    "catalan pyrenees": 48121,
    "aigüestortes": 48121,
    "aiguestortes": 48121,
    "aragonese pyrenees": 48125,
    "pirineo aragonés": 48125,
    "pirineo aragones": 48125,
    "ordesa": 48125,
    "javea": 48296,
    "jávea": 48296,
    "denia": 48296,
    "dénia": 48296,
    "canon rojo": 48860,
    "cañon rojo": 48860,
    "cañón rojo": 48860,
    "red canyon": 48860,
    "albarracin": 48859,
    "albarracín": 48859,
    "teruel": 48859,
    "murcia": 48875,
    "cartagena": 48875,
    "morocco": 48884,
    "marruecos": 48884,
    "chulilla": 47880,
    "albufera": 47881,
    "lavanda": 47944,
    "lavender": 47944,
    "ademuz": 47944,
    "fiordos": 47945,
    "fjords": 47945,
    "cofrentes": 47945,
    "xativa": 48110,
    "xàtiva": 48110,
    "bocairent": 48110,
    "andalucia": 48127,
    "andalucía": 48127,
    "andalusia": 48127,
    "catamaran": 48891,
    "catamarán": 48891,
    "boat party": 48891
}

# Emparejamiento 1 a 1 de fichas de producto: Español (ES) -> Inglés (EN)
# Nota: Erasmus Welcome Pack (49636) y Pub Crawl (49767) quedan excluidos para gestión manual.
PRODUCT_PAIRS_ES_EN = {
    47879: 49615,  # Montanejos
    47937: 49751,  # Isla de Tabarca
    48104: 49696,  # Calpe y Peñón de Ifach
    48119: 49686,  # Senderismo Cueva Tallada
    48067: 49702,  # Cala Moraig
    47941: 49726,  # Moraira
    47939: 49740,  # Laguna Rosa (Torrevieja) y Alicante
    47938: 49745,  # Altea y Guadalest
    47943: 49720,  # Peñíscola y Sagunto
    48123: 49786,  # Pirineos Franceses: Gavarnie y Cauterets
    48121: 49780,  # Pirineo Catalán: Aigüestortes
    48125: 49794,  # Pirineo Aragonés: Ordesa y Formigal
    48296: 49683,  # Jávea y Dénia
    48860: 49676,  # Senderismo Cañón Rojo de Teruel
    48859: 49680,  # Albarracín y Teruel
    48861: 49669,  # Senderismo Alto de Pipa
    48862: 49664,  # Senderismo Cañón del Mascarat
    48875: 49659,  # Murcia y Cartagena
    48876: 49652,  # Peñíscola y Cuevas de San José
    48877: 49647,  # Altea y Benidorm
    48884: 49804,  # Viaje a Marruecos 8 días
    47880: 49757,  # Chulilla
    47881: 49754,  # Albufera
    47940: 49733,  # Dénia y Cueva de las Calaveras
    47944: 49712,  # Campos de Lavanda en Ademuz
    47945: 49708,  # Crucero por los Fiordos Valencianos
    48066: 49705,  # Dénia en Barco
    48105: 49694,  # Fuentes del Algar y Calpe
    48110: 49691,  # Xàtiva y Bocairent
    48117: 49689,  # Puenting en el Mascarat
    48127: 49796,  # Ruta por Andalucía 4 días
    49313: 49629,  # Fuentes del Algar
    49314: 49625,  # Fuentes del Algar y Altea
    48891: 49642,  # Fiesta en Catamarán / Valencia Boat Party
}

ENGLISH_PRODUCT_IDS = set(PRODUCT_PAIRS_ES_EN.values())
MANAGED_ES_IDS = set(KEYWORDS_MAPPING.values())
MANAGED_EN_IDS = {PRODUCT_PAIRS_ES_EN[es_id] for es_id in MANAGED_ES_IDS if es_id in PRODUCT_PAIRS_ES_EN}
MANAGED_PRODUCT_IDS = MANAGED_ES_IDS | MANAGED_EN_IDS

# Date formatting helpers (locale-independent)
def format_date_spanish(dt):
    weekdays = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    months = [
        "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]
    return f"{weekdays[dt.weekday()]}, {dt.day} de {months[dt.month]} {dt.year}"

def format_date_english(dt):
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    months = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]
    return f"{weekdays[dt.weekday()]}, {dt.day} {months[dt.month]} {dt.year}"

# Call WooCommerce API
def api_call(endpoint, method="GET", body=None):
    url = f"{STORE_URL}/wp-json/wc/v3/{endpoint}"
    headers = {"Content-Type": "application/json"}
    
    if DRY_RUN and method in ["POST", "PUT", "DELETE"]:
        print(f"[DRY-RUN] Simulating {method} to {endpoint}")
        if "variations/batch" in endpoint and method == "POST":
            return {"create": [{"id": 99999 + i, "attributes": opt.get("attributes", [])} for i, opt in enumerate(body.get("create", []))]}
        return {}

    response = requests.request(
        method,
        url,
        auth=auth,
        headers=headers,
        json=body,
        timeout=45
    )
    if response.status_code not in [200, 201]:
        print(f"Error {response.status_code} calling {endpoint}: {response.text}")
        response.raise_for_status()
    return response.json()

def is_placeholder_date(date_str):
    if not date_str:
        return False
    lower = date_str.lower()
    return any(p in lower for p in ["próximamente", "proximamente", "coming soon"])

def parse_variation_date(date_str):
    """Parses both Spanish and English variation date strings (including multi-day ranges)."""
    if not date_str:
        return None
    months_es = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
    }
    months_en = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
    }

    # 1. Spanish date or range: e.g. "Sábado, 24 de Octubre 2026" or "9 al 16 de Octubre 2026"
    match_es = re.search(r'(?:(\d+)\s+al\s+)?(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+(\d{4})', date_str, re.I)
    if match_es:
        day = int(match_es.group(1) or match_es.group(2))
        month_name = match_es.group(3).lower()
        year = int(match_es.group(4))
        month = months_es.get(month_name)
        if month:
            try:
                return datetime(year, month, day).date()
            except ValueError:
                return None

    # 2. English range: e.g. "9–16 October 2026" or "9-16 October 2026"
    match_en_range = re.search(r'(\d+)\s*[–\-]\s*(\d+)\s+([a-zA-Z]+)\s+(\d{4})', date_str, re.I)
    if match_en_range:
        day = int(match_en_range.group(1))
        month_name = match_en_range.group(3).lower()
        year = int(match_en_range.group(4))
        month = months_en.get(month_name)
        if month:
            try:
                return datetime(year, month, day).date()
            except ValueError:
                return None

    # 3. English single date: e.g. "Saturday, 24 October 2026"
    match_en = re.search(r'(?:[a-zA-Z]+,\s+)?(\d+)\s+([a-zA-Z]+)\s+(\d{4})', date_str, re.I)
    if match_en:
        day = int(match_en.group(1))
        month_name = match_en.group(2).lower()
        year = int(match_en.group(3))
        month = months_en.get(month_name)
        if month:
            try:
                return datetime(year, month, day).date()
            except ValueError:
                return None

    return None

def run_sync():
    # 1. Scrape collaborator calendar via monthly-calendar page & AJAX endpoint
    collab_calendar_url = "https://valencialanguageexchange.com/monthly-calendar/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    print(f"Fetching MEC calendar configuration from: {collab_calendar_url}...")
    try:
        response = requests.get(collab_calendar_url, headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching collaborator calendar configuration: {e}")
        sys.exit(1)
        
    atts_match = re.search(r'atts:\s*"([^"]+)"', response.text)
    id_match = re.search(r'id:\s*"([^"]+)"', response.text)
    if not atts_match or not id_match:
        # Fallback to single quotes
        atts_match = re.search(r"atts:\s*'([^']+)'", response.text)
        id_match = re.search(r"id:\s*'([^']+)'", response.text)
        
    if not atts_match or not id_match:
        print("Error: Could not parse MEC attributes or calendar ID from the website.")
        sys.exit(1)
        
    atts = urllib.parse.unquote(atts_match.group(1))
    calendar_id = id_match.group(1)
    
    # Calculate current and next two months to fetch
    today = datetime.today().date()
    current_year = today.year
    current_month = today.month
    
    months_to_fetch = []
    year = current_year
    month = current_month
    for _ in range(3):
        months_to_fetch.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
            
    content = ""
    ajax_url = "https://valencialanguageexchange.com/wp-admin/admin-ajax.php"
    
    for y, m in months_to_fetch:
        month_str = f"{m:02d}"
        print(f"Fetching AJAX calendar for {y}-{month_str}...")
        payload = {
            "action": "mec_monthly_view_load_month",
            "id": calendar_id,
            "mec_year": str(y),
            "mec_month": month_str,
            "atts": atts
        }
        try:
            r = requests.post(ajax_url, data=payload, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            events_html = data.get("events_side", "")
            content += "\n" + events_html
        except Exception as e:
            print(f"Warning: Failed to fetch calendar for {y}-{month_str}: {e}")
            
    # Find all mec-calendar-events-sec elements using regex matches
    sections = list(re.finditer(r'class="mec-calendar-events-sec"\s+id="mec-calendar-events-sec-\d+-(\d{8})"', content))
    print(f"Found {len(sections)} calendar date sections across all months.")
    
    scraped_dates_by_product = {} # key: product_id (ES or EN), value: list of formatted date strings
    
    today = datetime.today().date()
    
    for sec in sections:
        date_str = sec.group(1)
        try:
            date_obj = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            continue
            
        weekday = date_obj.weekday()
        is_weekend = weekday in [5, 6]
        is_thursday = weekday == 3
        
        # Only process weekends or Thursdays (for multi-day excursions starting on Thursdays)
        if is_weekend or is_thursday:
            # Skip past dates
            if date_obj < today:
                continue
                
            start_pos = sec.start()
            snippet = content[start_pos:start_pos+15000]
            next_sec = re.search(r'class="mec-calendar-events-sec"', snippet[50:])
            block = snippet[:next_sec.start()+50] if next_sec else snippet
            
            sub_soup = BeautifulSoup(block, 'html.parser')
            links = sub_soup.find_all('a')
            
            for l in links:
                href = l.get('href')
                text = l.get_text(strip=True)
                title = l.get('title') or text
                
                if href and ("/tour/" in href or "/event/" in href or "/wexcursion/" in href or "/trip/" in href or "/trips/" in href):
                    title_lower = title.lower()
                    
                    # Match against keyword map (returns Spanish Product ID)
                    matched_es_pid = None
                    for kw, pid in KEYWORDS_MAPPING.items():
                        if kw in title_lower:
                            matched_es_pid = pid
                            break
                            
                    if matched_es_pid:
                        # 1. Spanish product date
                        formatted_es = format_date_spanish(date_obj)
                        if matched_es_pid not in scraped_dates_by_product:
                            scraped_dates_by_product[matched_es_pid] = []
                        if formatted_es not in scraped_dates_by_product[matched_es_pid]:
                            scraped_dates_by_product[matched_es_pid].append(formatted_es)
                            
                        # 2. Paired English product date
                        matched_en_pid = PRODUCT_PAIRS_ES_EN.get(matched_es_pid)
                        if matched_en_pid:
                            formatted_en = format_date_english(date_obj)
                            if matched_en_pid not in scraped_dates_by_product:
                                scraped_dates_by_product[matched_en_pid] = []
                            if formatted_en not in scraped_dates_by_product[matched_en_pid]:
                                scraped_dates_by_product[matched_en_pid].append(formatted_en)
                            
    print("\nScraped matched events (future only, ES + EN):")
    for pid, dates in scraped_dates_by_product.items():
        lang_tag = "EN" if pid in ENGLISH_PRODUCT_IDS else "ES"
        print(f"  - [{lang_tag}] Product ID {pid} matches calendar dates: {dates}")
        
    # 2. Retrieve WooCommerce products
    print("\nFetching WooCommerce catalog products...")
    products = api_call("products?per_page=100&status=any")
    print(f"Fetched {len(products)} products from WooCommerce.")
    
    sync_summary = {"matched": 0, "unmatched": 0}
    
    for p in products:
        pid = p["id"]
        p_name = p["name"]
        p_type = p["type"]
        p_price = p["price"] or "0.00"
        if not p["price"] and p["regular_price"]:
            p_price = p["regular_price"]
            
        # Check if this product (ES or EN) is one we manage
        if pid in MANAGED_PRODUCT_IDS:
            is_english = pid in ENGLISH_PRODUCT_IDS
            lang_tag = "EN" if is_english else "ES"
            date_attr_name = "Date" if is_english else "FECHA"
            placeholder_text = "Coming soon – new dates announced shortly" if is_english else "Próximamente - nuevas fechas en breve"
            
            scraped_dates = scraped_dates_by_product.get(pid, [])
            
            # Fetch existing variations if variable
            existing_variations = []
            existing_future_dates = []
            delete_ids = []
            
            if p_type == "variable":
                print(f"\n[MANAGE {lang_tag}] {p_name} (ID: {pid}) -> Fetching variations...")
                existing_variations = api_call(f"products/{pid}/variations?per_page=100")
                for var in existing_variations:
                    var_id = var["id"]
                    # Get FECHA / Date attribute value
                    fecha_opt = None
                    for attr in var.get("attributes", []):
                        if attr.get("name", "").upper() in ["FECHA", "DATE"]:
                            fecha_opt = attr.get("option")
                            break
                            
                    if fecha_opt:
                        parsed_date = parse_variation_date(fecha_opt)
                        if parsed_date:
                            if parsed_date < today:
                                delete_ids.append(var_id)
                                print(f"    - Found past variation: {fecha_opt} (ID: {var_id}) -> Will delete")
                            else:
                                existing_future_dates.append(fecha_opt)
                        elif is_placeholder_date(fecha_opt) and scraped_dates:
                            # Real dates are now available, remove placeholder variation
                            delete_ids.append(var_id)
                            print(f"    - Found placeholder variation while real dates exist: {fecha_opt} (ID: {var_id}) -> Will delete")
                        else:
                            print(f"    - Warning: Could not parse variation date: {fecha_opt} (ID: {var_id}) -> Keeping it")
                            existing_future_dates.append(fecha_opt)
                            
            # Delete past or obsolete placeholder variations
            if delete_ids:
                print(f"  Deleting {len(delete_ids)} past/placeholder variations...")
                api_call(f"products/{pid}/variations/batch", "POST", {"delete": delete_ids})
                
            # Filter scraped dates to only keep new ones not already in existing_future_dates
            new_dates_to_create = [d for d in scraped_dates if d not in existing_future_dates]
            
            # Total future variations remaining (existing future + newly to create)
            total_future_dates = existing_future_dates + new_dates_to_create
            
            if total_future_dates:
                # 1. Update parent product to variable type, visible, instock
                print(f"  Updating parent product status to 'variable' with available dates ({date_attr_name}): {total_future_dates}")
                update_payload = {
                    "type": "variable",
                    "status": "publish",
                    "stock_status": "instock",
                    "manage_stock": False,
                    "catalog_visibility": "visible",
                    "attributes": [
                        {
                            "name": date_attr_name,
                            "visible": True,
                            "variation": True,
                            "options": total_future_dates
                        }
                    ]
                }
                if p.get("attributes"):
                    other_attrs = [a for a in p["attributes"] if a["name"].upper() not in ["FECHA", "DATE"]]
                    for a in other_attrs:
                        a["variation"] = False
                    update_payload["attributes"].extend(other_attrs)
                    
                api_call(f"products/{pid}", "PUT", update_payload)
                
                # 2. Create new variations if needed
                if new_dates_to_create:
                    print(f"  Creating {len(new_dates_to_create)} new variations with tiered pricing...")
                    create_payloads = []
                    
                    try:
                        base_price = float(p_price)
                    except ValueError:
                        base_price = 0.0
                        
                    # Tiered pricing rules
                    p3 = max(0.0, base_price - 2.0)
                    p5 = max(0.0, base_price - 4.0)
                    p10 = max(0.0, base_price - 6.0)
                    
                    rules = {
                        "3": p3,
                        "5": p5,
                        "10": p10
                    }
                    
                    for d in new_dates_to_create:
                        create_payloads.append({
                            "regular_price": str(base_price),
                            "stock_status": "instock",
                            "manage_stock": False,
                            "attributes": [
                                {
                                    "name": date_attr_name,
                                    "option": d
                                }
                            ],
                            "tiered_pricing_type": "fixed",
                            "tiered_pricing_fixed_rules": rules
                        })
                        
                    batch_res = api_call(f"products/{pid}/variations/batch", "POST", {"create": create_payloads})
                    created_count = len(batch_res.get("create", [])) if not DRY_RUN else len(new_dates_to_create)
                    print(f"  Successfully created {created_count} new variations with tiered pricing rules.")
                else:
                    print("  No new variations need to be created.")
                    
                sync_summary["matched"] += 1
            else:
                # No future dates at all! -> Set simple, outofstock, hidden and published to prevent empty price crashes
                print(f"\n[UNMATCH / EXPIRED {lang_tag}] {p_name} (ID: {pid}) -> Setting to simple, outofstock, hidden and published")
                
                # Determine a fallback price to set on the simple product
                final_price = None
                if p_price and p_price not in ["0.00", "0", ""]:
                    final_price = p_price
                
                if not final_price and existing_variations:
                    for var in existing_variations:
                        v_price = var.get("price") or var.get("regular_price")
                        if v_price and v_price not in ["0.00", "0", ""]:
                            final_price = v_price
                            break
                
                update_payload = {
                    "type": "simple",
                    "stock_status": "outofstock",
                    "manage_stock": True,
                    "stock_quantity": 0,
                    "catalog_visibility": "visible",
                    "status": "publish",
                    "attributes": [
                        {
                            "name": date_attr_name,
                            "visible": True,
                            "variation": False,
                            "options": [placeholder_text]
                        }
                    ]
                }
                if final_price:
                    update_payload["regular_price"] = str(final_price)
                else:
                    print(f"  [WARNING] No price data found for {p_name} (ID: {pid}). Manual action required.")

                if p.get("attributes"):
                    other_attrs = [a for a in p["attributes"] if a["name"].upper() not in ["FECHA", "DATE"]]
                    for a in other_attrs:
                        a["variation"] = False
                    update_payload["attributes"].extend(other_attrs)

                api_call(f"products/{pid}", "PUT", update_payload)
                sync_summary["unmatched"] += 1
                
    print(f"\nSincronización finalizada. Resumen (ES + EN): Coincidentes/Futuros activos: {sync_summary['matched']} | Expirados/Ocultos: {sync_summary['unmatched']}")

if __name__ == "__main__":
    run_sync()
