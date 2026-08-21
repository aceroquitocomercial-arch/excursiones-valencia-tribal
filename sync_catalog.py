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

# Keyword to WooCommerce Product ID mapping
KEYWORDS_MAPPING = {
    "montanejos": 47879,
    "tabarca": 47937,
    "calpe": 48104,
    "cueva tallada": 48119,
    "tallada": 48119,
    "moraig": 48067,
    "moraira": 47941,
    "laguna rosa": 47939,
    "torrevieja": 47939,
    "altea": 47938,
    "guadalest": 47938,
    "peñiscola": 47943,
    "peniscola": 47943,
    "sagunto": 47943,
    "pirineos franceses": 48123,
    "french pyrenees": 48123,
    "pirineo catalan": 48121,
    "pirineo catalán": 48121,
    "catalan pyrenees": 48121,
    "aragonese pyrenees": 48125,
    "pirineo aragonés": 48125,
    "javea": 48296,
    "jávea": 48296,
    "denia": 48296,
    "dénia": 48296
}

# Date formatting helper in Spanish
def format_date_spanish(dt):
    weekdays = {
        "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
        "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo"
    }
    months = {
        "January": "Enero", "February": "Febrero", "March": "Marzo", "April": "Abril",
        "May": "Mayo", "June": "Junio", "July": "Julio", "August": "Agosto",
        "September": "Septiembre", "October": "Octubre", "November": "Noviembre", "December": "Diciembre"
    }
    eng_weekday = dt.strftime("%A")
    eng_month = dt.strftime("%B")
    return f"{weekdays.get(eng_weekday, eng_weekday)}, {dt.day} de {months.get(eng_month, eng_month)} {dt.year}"

# Call WooCommerce API
def api_call(endpoint, method="GET", body=None):
    url = f"{STORE_URL}/wp-json/wc/v3/{endpoint}"
    headers = {"Content-Type": "application/json"}
    
    if DRY_RUN and method in ["POST", "PUT", "DELETE"]:
        print(f"[DRY-RUN] Simulating {method} to {endpoint}")
        if "variations/batch" in endpoint and method == "POST":
            return {"create": [{"id": 99999 + i, "attributes": [{"name": "FECHA", "option": opt}]} for i, opt in enumerate(body.get("create", []))]}
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

def parse_spanish_date(date_str):
    match = re.search(r'(?:[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+,\s+)?(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+(\d{4})', date_str, re.I)
    if not match:
        return None
    day = int(match.group(1))
    month_name = match.group(2).lower()
    year = int(match.group(3))
    months = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
    }
    month = months.get(month_name)
    if not month:
        return None
    try:
        return datetime(year, month, day).date()
    except ValueError:
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
    
    scraped_dates_by_product = {} # key: product_id, value: list of formatted date strings
    
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
                    
                    # Match against keyword map
                    matched_pid = None
                    for kw, pid in KEYWORDS_MAPPING.items():
                        if kw in title_lower:
                            matched_pid = pid
                            break
                            
                    if matched_pid:
                        formatted_date = format_date_spanish(date_obj)
                        if matched_pid not in scraped_dates_by_product:
                            scraped_dates_by_product[matched_pid] = []
                        if formatted_date not in scraped_dates_by_product[matched_pid]:
                            scraped_dates_by_product[matched_pid].append(formatted_date)
                            
    print("\nScraped matched events (future only):")
    for pid, dates in scraped_dates_by_product.items():
        print(f"  - Product ID {pid} matches calendar dates: {dates}")
        
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
            
        # Check if this product is one we manage
        if pid in KEYWORDS_MAPPING.values():
            # MATCHED OR UNMATCHED MANAGED PRODUCT
            scraped_dates = scraped_dates_by_product.get(pid, [])
            
            # Fetch existing variations if variable
            existing_variations = []
            existing_future_dates = []
            delete_ids = []
            
            if p_type == "variable":
                print(f"\n[MANAGE] {p_name} (ID: {pid}) -> Fetching variations...")
                existing_variations = api_call(f"products/{pid}/variations?per_page=100")
                for var in existing_variations:
                    var_id = var["id"]
                    # Get FECHA attribute value
                    fecha_opt = None
                    for attr in var.get("attributes", []):
                        if attr.get("name") == "FECHA":
                            fecha_opt = attr.get("option")
                            break
                            
                    if fecha_opt:
                        parsed_date = parse_spanish_date(fecha_opt)
                        if parsed_date:
                            if parsed_date < today:
                                delete_ids.append(var_id)
                                print(f"    - Found past variation: {fecha_opt} (ID: {var_id}) -> Will delete")
                            else:
                                existing_future_dates.append(fecha_opt)
                        else:
                            print(f"    - Warning: Could not parse variation date: {fecha_opt} (ID: {var_id}) -> Keeping it")
                            existing_future_dates.append(fecha_opt)
                            
            # Delete past variations
            if delete_ids:
                print(f"  Deleting {len(delete_ids)} past variations...")
                api_call(f"products/{pid}/variations/batch", "POST", {"delete": delete_ids})
                
            # Filter scraped dates to only keep new ones not already in existing_future_dates
            new_dates_to_create = [d for d in scraped_dates if d not in existing_future_dates]
            
            # Total future variations remaining (existing future + newly to create)
            total_future_dates = existing_future_dates + new_dates_to_create
            
            if total_future_dates:
                # 1. Update parent product to variable type, visible, instock
                print(f"  Updating parent product status to 'variable' with available dates: {total_future_dates}")
                update_payload = {
                    "type": "variable",
                    "status": "publish",
                    "stock_status": "instock",
                    "catalog_visibility": "visible",
                    "attributes": [
                        {
                            "name": "FECHA",
                            "visible": True,
                            "variation": True,
                            "options": total_future_dates
                        }
                    ]
                }
                if p.get("attributes"):
                    other_attrs = [a for a in p["attributes"] if a["name"].upper() != "FECHA"]
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
                                    "name": "FECHA",
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
                # No future dates at all! -> Set simple, outofstock, hidden and private to prevent empty price crashes
                print(f"\n[UNMATCH / EXPIRED] {p_name} (ID: {pid}) -> Setting to simple, outofstock, hidden and private")
                update_payload = {
                    "type": "simple",
                    "stock_status": "outofstock",
                    "catalog_visibility": "hidden",
                    "status": "private",
                    "attributes": [
                        {
                            "name": "FECHA",
                            "visible": True,
                            "variation": False,
                            "options": ["Próximamente - nuevas fechas en breve"]
                        }
                    ]
                }
                if p.get("attributes"):
                    other_attrs = [a for a in p["attributes"] if a["name"].upper() != "FECHA"]
                    for a in other_attrs:
                        a["variation"] = False
                    update_payload["attributes"].extend(other_attrs)

                api_call(f"products/{pid}", "PUT", update_payload)
                sync_summary["unmatched"] += 1
                
    print(f"\nSincronización finalizada. Resumen: Coincidentes/Futuros activos: {sync_summary['matched']} | Expirados/Ocultos: {sync_summary['unmatched']}")

if __name__ == "__main__":
    run_sync()
