import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timezone
import urllib.parse

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Attempt to import curl_cffi for ultra-fast Cloudflare bypass
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = requests
    HAS_CURL_CFFI = False

import time

def fetch_url(url, timeout=20, retries=2):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    last_res = None
    for attempt in range(retries + 1):
        try:
            if HAS_CURL_CFFI:
                r = cffi_requests.get(url, impersonate="chrome124", timeout=timeout)
            else:
                r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r
            last_res = r
        except Exception:
            if attempt == retries:
                raise
        time.sleep(1.0)
    return last_res


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
    print("=== RUNNING IN DRY-RUN MODE (NO CHANGES WILL BE SAVED TO WOOCOMMERCE) ===")
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

# Call WooCommerce API
def api_call(endpoint, method="GET", body=None):
    url = f"{STORE_URL}/wp-json/wc/v3/{endpoint}"
    headers = {"Content-Type": "application/json"}
    
    if DRY_RUN and method in ["POST", "PUT", "DELETE"]:
        print(f"[DRY-RUN] Simulating {method} to {endpoint}")
        if body and "update" in body:
            print(f"         Updates: {body['update']}")
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

def get_active_tours():
    collab_calendar_url = "https://valencialanguageexchange.com/monthly-calendar/"
    headers_web = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    print(f"Fetching calendar page from: {collab_calendar_url}...")
    try:
        response = requests.get(collab_calendar_url, headers=headers_web, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching calendar page: {e}")
        return {}
    
    atts_match = re.search(r'atts:\s*"([^"]+)"', response.text)
    id_match = re.search(r'id:\s*"([^"]+)"', response.text)
    if not atts_match or not id_match:
        atts_match = re.search(r"atts:\s*'([^']+)'", response.text)
        id_match = re.search(r"id:\s*'([^']+)'", response.text)
        
    if not atts_match or not id_match:
        print("Error parsing calendar configuration")
        return {}
        
    atts = urllib.parse.unquote(atts_match.group(1))
    calendar_id = id_match.group(1)
    
    today = datetime.today().date()
    months_to_fetch = []
    year = today.year
    month = today.month
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
            r = requests.post(ajax_url, data=payload, headers=headers_web, timeout=30)
            r.raise_for_status()
            content += "\n" + r.json().get("events_side", "")
        except Exception as e:
            print(f"Warning: Failed to fetch calendar for {y}-{month_str}: {e}")
            
    soup = BeautifulSoup(content, 'html.parser')
    links = soup.find_all('a')
    tours = {}
    for l in links:
        href = l.get('href')
        text = l.get_text(strip=True)
        title = l.get('title') or text
        if href and ("/tour/" in href or "/event/" in href or "/wexcursion/" in href or "/trip/" in href or "/trips/" in href):
            if href not in tours:
                tours[href] = title
                
    return tours

# ==================== ADAPTER 1: TICKET TAILOR ====================
def extract_ticket_tailor_prices(tour_url, tour_html=None):
    """
    Extracts dynamic prices from Ticket Tailor widget/occurrences.
    Returns: {date_obj: float(price)}
    """
    prices_by_date = {}
    try:
        if not tour_html:
            r = fetch_url(tour_url, timeout=15)
            if r.status_code != 200:
                return {}
            tour_html = r.text
            
        tt_links = re.findall(r'https?://(?:www\.)?tickettailor\.com/events/valencialanguageexchange/\d+/[^\s"\'<>]+', tour_html)
        if not tt_links:
            tt_links = re.findall(r'https?://(?:www\.)?tickettailor\.com/events/valencialanguageexchange/\d+', tour_html)
            
        if not tt_links:
            return {}
            
        tt_url = list(set(tt_links))[0]
        if "select-date" not in tt_url:
            tt_url = tt_url.rstrip('/') + "/select-date"
            
        print(f"  [Ticket Tailor] Connecting to: {tt_url}")
        r_tt = fetch_url(tt_url, timeout=15)
        if r_tt.status_code != 200:
            print(f"  [Ticket Tailor] Returned HTTP status {r_tt.status_code}")
            return {}
            
        soup = BeautifulSoup(r_tt.text, 'html.parser')
        dates = soup.find_all(class_="date")
        
        for d in dates:
            occ = d.find(class_="occurrence")
            date_id = occ.get("id") if occ else ""
            match_date = re.search(r'(\d{4}-\d{2}-\d{2})', date_id)
            if not match_date:
                continue
            date_str = match_date.group(1)
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
                
            link = d.find("a")
            chk_url = link.get("href") if link else ""
            if not chk_url or "/checkout/" not in chk_url:
                continue
                
            full_chk = f"https://www.tickettailor.com{chk_url}"
            r_chk = fetch_url(full_chk, timeout=15)
            if r_chk.status_code != 200:
                continue
                
            soup_chk = BeautifulSoup(r_chk.text, 'html.parser')
            ticket_types = soup_chk.find_all(class_="ticket_type")
            candidate_prices = []
            
            for t in ticket_types:
                label = t.find("label")
                title_var = label.find("var") if label else None
                t_title = title_var.get_text(strip=True).lower() if title_var else ""
                if not t_title:
                    name_el = t.find(class_=re.compile(r'name|title', re.I))
                    t_title = name_el.get_text(strip=True).lower() if name_el else ""
                    
                price_el = t.find(class_=re.compile(r'price', re.I))
                t_price_text = price_el.get_text(strip=True) if price_el else ""
                
                amounts = re.findall(r'(\d+(?:[\.,]\d{2})?)', t_price_text)
                if not amounts:
                    continue
                try:
                    total_t_price = sum(float(a.replace(',', '.')) for a in amounts)
                except ValueError:
                    continue
                    
                if total_t_price <= 0:
                    continue
                    
                # Preference scoring based on title
                score = 1
                if any(w in t_title for w in ["normal", "standard", "general"]):
                    score += 5
                if any(w in t_title for w in ["hiking", "hike"]):
                    score += 4
                if any(w in t_title for w in ["with boat", "con barco", "con ticket"]):
                    score += 3
                if any(w in t_title for w in ["no hike", "sin senderismo"]):
                    score -= 2
                if any(w in t_title for w in ["rafting", "kayak", "canoa", "vip"]):
                    score -= 10
                if any(w in t_title for w in ["entrance only", "only entrance", "solo entrada", "entrada solo"]):
                    score = 0  # skip add-ons
                    
                if score > 0:
                    candidate_prices.append((score, total_t_price, t_title))
                    
            if candidate_prices:
                candidate_prices.sort(key=lambda x: x[0], reverse=True)
                best_price = candidate_prices[0][1]
                prices_by_date[date_obj] = best_price
                
    except Exception as e:
        print(f"  [Ticket Tailor] Extraction warning: {e}")
        
    return prices_by_date

# ==================== ADAPTER 2: BÓKUN ====================
def extract_bokun_widget(tour_url, tour_html=None):
    try:
        if not tour_html:
            r = requests.get(tour_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            tour_html = r.text
        soup = BeautifulSoup(tour_html, 'html.parser')
        widget_div = soup.find(class_="bokunWidget")
        if widget_div and widget_div.get("data-src"):
            data_src = widget_div.get("data-src")
            match = re.search(r'/online-sales/([a-f0-9\-]+)/experience-calendar/(\d+)', data_src)
            if match:
                return {"channel_uuid": match.group(1), "experience_id": match.group(2)}
        
        match_fallback = re.search(r'widgets\.bokun\.io/online-sales/([a-f0-9\-]+)/experience-calendar/(\d+)', tour_html)
        if match_fallback:
            return {"channel_uuid": match_fallback.group(1), "experience_id": match_fallback.group(2)}
    except Exception as e:
        print(f"Error fetching Bókun widget for {tour_url}: {e}")
    return None

def fetch_bokun_prices(channel_uuid, experience_id):
    headers_bokun = {
        "User-Agent": "Mozilla/5.0",
        "X-Bokun-Fetch": "true",
        "Content-Type": "application/json"
    }
    options_url = f"https://widgets.bokun.io/widgets/{channel_uuid}/activity/{experience_id}/options"
    try:
        r = requests.get(options_url, headers=headers_bokun, timeout=20)
        r.raise_for_status()
        opts = r.json().get("activityBookingOptions", {})
        categories = opts.get("pricingCategories", [])
        if not categories:
            return {}
            
        adult_cat = None
        for c in categories:
            if "adult" in c.get("title", "").lower():
                adult_cat = c
                break
        if not adult_cat:
            adult_cat = categories[0]
            
        adult_id = adult_cat.get("id")
        
        today = datetime.today().date()
        months_to_query = []
        year = today.year
        month = today.month
        for _ in range(3):
            months_to_query.append((year, month))
            month += 1
            if month > 12:
                month = 1
                year += 1
                
        prices_by_date = {}
        for y, m in months_to_query:
            month_str = f"{m:02d}"
            cal_url = f"https://widgets.bokun.io/widgets/{channel_uuid}/activity/{experience_id}/{y}/{month_str}"
            payload = {
                "pricingCategories": [{"id": adult_id, "quantity": 1}],
                "giftCard": False
            }
            r_cal = requests.post(cal_url, headers=headers_bokun, json=payload, timeout=20)
            if r_cal.status_code == 200:
                weeks = r_cal.json().get("calendar", {}).get("weeks", [])
                for week in weeks:
                    for day in week.get("days", []):
                        if day.get("activityAvailabilityStatus") == "AVAILABLE":
                            date_millis = day.get("dateObj")
                            date_obj = datetime.fromtimestamp(date_millis / 1000.0, tz=timezone.utc).date()
                            rates = day.get("totalPriceByRate", [])
                            if rates:
                                price_details = rates[0].get("priceDetails", {})
                                amount = price_details.get("amount")
                                if amount is not None:
                                    prices_by_date[date_obj] = float(amount)
                                    
        return prices_by_date
    except Exception as e:
        print(f"Error querying Bokun API for experience {experience_id}: {e}")
        return {}

# ==================== MAIN SYNC WITH SAFEGUARD ====================
def run_sync():
    tours = get_active_tours()
    print(f"\nFound {len(tours)} active tours on collaborator's site.")
    
    dynamic_prices_by_woo_id = {}
    
    for url, title in tours.items():
        title_lower = title.lower()
        matched_pid = None
        for kw, pid in KEYWORDS_MAPPING.items():
            if kw in title_lower:
                matched_pid = pid
                break
                
        if matched_pid:
            print(f"\n--- Excursion: {title} (ID: {matched_pid}) ---")
            try:
                r_tour = fetch_url(url, timeout=20)
                tour_html = r_tour.text if r_tour.status_code == 200 else ""
            except Exception as e:
                print(f"Error loading tour page {url}: {e}")
                tour_html = ""
                
            prices = {}
            # 1. Try Ticket Tailor
            if "tickettailor" in tour_html:
                prices = extract_ticket_tailor_prices(url, tour_html)
                if prices:
                    print(f"  [Ticket Tailor] Extracted {len(prices)} dynamic date prices: {prices}")
                    
            # 2. Try Bókun (if Ticket Tailor not found or empty)
            if not prices and ("bokun" in tour_html or "bokunWidget" in tour_html):
                widget = extract_bokun_widget(url, tour_html)
                if widget:
                    print(f"  [Bókun] Found experience ID: {widget['experience_id']}")
                    prices = fetch_bokun_prices(widget["channel_uuid"], widget["experience_id"])
                    if prices:
                        print(f"  [Bókun] Extracted {len(prices)} dynamic date prices.")
                        
            if prices:
                if matched_pid not in dynamic_prices_by_woo_id:
                    dynamic_prices_by_woo_id[matched_pid] = {}
                dynamic_prices_by_woo_id[matched_pid].update(prices)
            else:
                print(f"  [AVISO] No se encontraron tarifas dinámicas en la página del colaborador. Se activará la salvaguarda.")

    print("\nFetching WooCommerce catalog products...")
    products = api_call("products?per_page=100&status=any")
    print(f"Fetched {len(products)} products from WooCommerce.")
    
    updated_count = 0
    total_compared = 0
    safeguard_applied_count = 0
    
    for p in products:
        pid = p["id"]
        p_name = p["name"]
        p_type = p["type"]
        
        # Determine parent product's base price
        parent_base_price = None
        raw_price = p.get("price") or p.get("regular_price")
        if raw_price:
            try:
                parent_base_price = float(raw_price)
            except ValueError:
                parent_base_price = None
                
        if not parent_base_price or parent_base_price <= 0:
            parent_base_price = 35.00 # Standard fallback
            
        if pid in KEYWORDS_MAPPING.values():
            if p_type != "variable":
                continue
                
            dynamic_dates_prices = dynamic_prices_by_woo_id.get(pid, {})
            print(f"\n[SYNC PRODUCT] {p_name} (ID: {pid}) | Base WooCommerce Price: {parent_base_price:.2f} €")
            
            # Ensure parent product has manage_stock: False and stock_status: instock
            parent_update = {}
            if p.get("manage_stock") or p.get("stock_status") != "instock":
                parent_update["manage_stock"] = False
                parent_update["stock_status"] = "instock"
            if dynamic_dates_prices:
                known_dynamic_price = list(dynamic_dates_prices.values())[0]
                if abs(parent_base_price - known_dynamic_price) > 0.01:
                    parent_update["regular_price"] = f"{known_dynamic_price:.2f}"
            if parent_update:
                print(f"  Asegurando configuración del producto padre: {parent_update}")
                api_call(f"products/{pid}", "PUT", parent_update)
            
            variations = api_call(f"products/{pid}/variations?per_page=100")
            variations_to_update = []
            
            for var in variations:
                var_id = var["id"]
                fecha_opt = None
                for attr in var.get("attributes", []):
                    if attr.get("name") == "FECHA":
                        fecha_opt = attr.get("option")
                        break
                        
                if not fecha_opt:
                    continue
                    
                var_date = parse_spanish_date(fecha_opt)
                if not var_date:
                    continue
                    
                total_compared += 1
                current_price = float(var.get("regular_price") or 0.0)
                current_stock = var.get("stock_status", "")
                
                # Check if we have a dynamic price, an inherited dynamic price, or must use Safeguard
                dynamic_price = dynamic_dates_prices.get(var_date)
                
                if dynamic_price is not None and dynamic_price > 0:
                    target_price = dynamic_price
                    source_label = "DINAMICO"
                elif dynamic_dates_prices:
                    # Inherit from known dynamic price of this excursion (e.g. 36.00 € for Montanejos)
                    target_price = list(dynamic_dates_prices.values())[0]
                    source_label = "DINAMICO_HEREDADO"
                else:
                    # SALVAGUARDA: Fallback to parent product base price
                    target_price = parent_base_price
                    source_label = "SALVAGUARDA"
                    safeguard_applied_count += 1
                    
                p3 = max(0.0, target_price - 2.0)
                p5 = max(0.0, target_price - 4.0)
                p10 = max(0.0, target_price - 6.0)
                
                expected_rules = {
                    "3": f"{p3:.2f}",
                    "5": f"{p5:.2f}",
                    "10": f"{p10:.2f}"
                }
                
                current_rules = var.get("tiered_pricing_fixed_rules", {})
                current_rules_normalized = {k: f"{float(v):.2f}" for k, v in current_rules.items() if v}
                
                price_differs = abs(current_price - target_price) > 0.01
                rules_differ = current_rules_normalized != expected_rules
                stock_needs_fixing = current_stock != "instock"
                
                if price_differs or rules_differ or stock_needs_fixing:
                    print(f"  - [{source_label}] {fecha_opt} (Var ID: {var_id}):")
                    if price_differs:
                        print(f"      Precio: actual {current_price:.2f} € -> nuevo {target_price:.2f} €")
                    if rules_differ:
                        print(f"      Descuentos grupo: {current_rules_normalized} -> {expected_rules}")
                    if stock_needs_fixing:
                        print(f"      Estado stock: {current_stock} -> instock")
                        
                    variations_to_update.append({
                        "id": var_id,
                        "regular_price": f"{target_price:.2f}",
                        "stock_status": "instock",
                        "manage_stock": False,
                        "tiered_pricing_type": "fixed",
                        "tiered_pricing_fixed_rules": expected_rules
                    })
                    
            if variations_to_update:
                print(f"  Enviando actualización de {len(variations_to_update)} variaciones a WooCommerce...")
                batch_res = api_call(f"products/{pid}/variations/batch", "POST", {"update": variations_to_update})
                updated_count += len(variations_to_update)
            else:
                print("  Todas las variaciones están actualizadas y correctas.")
                
    print(f"\n============================================================")
    print(f"Sincronización de Precios Finalizada:")
    print(f"  - Variaciones analizadas: {total_compared}")
    print(f"  - Variaciones actualizadas: {updated_count}")
    print(f"  - Salvaguardas aplicadas (precio base): {safeguard_applied_count}")
    print(f"============================================================")

if __name__ == "__main__":
    run_sync()

