import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timezone
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

def extract_bokun_widget(tour_url):
    headers_web = {
        "User-Agent": "Mozilla/5.0"
    }
    try:
        r = requests.get(tour_url, headers=headers_web, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        widget_div = soup.find(class_="bokunWidget")
        if widget_div and widget_div.get("data-src"):
            data_src = widget_div.get("data-src")
            match = re.search(r'/online-sales/([a-f0-9\-]+)/experience-calendar/(\d+)', data_src)
            if match:
                return {"channel_uuid": match.group(1), "experience_id": match.group(2)}
        
        match_fallback = re.search(r'widgets\.bokun\.io/online-sales/([a-f0-9\-]+)/experience-calendar/(\d+)', r.text)
        if match_fallback:
            return {"channel_uuid": match_fallback.group(1), "experience_id": match_fallback.group(2)}
    except Exception as e:
        print(f"Error fetching tour details for {tour_url}: {e}")
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

def run_sync():
    tours = get_active_tours()
    print(f"\nFound {len(tours)} active tours on collaborator's site.")
    
    bokun_prices_by_woo_id = {}
    
    for url, title in tours.items():
        title_lower = title.lower()
        matched_pid = None
        for kw, pid in KEYWORDS_MAPPING.items():
            if kw in title_lower:
                matched_pid = pid
                break
                
        if matched_pid:
            print(f"\nExcursion: {title} (ID: {matched_pid})")
            widget = extract_bokun_widget(url)
            if widget:
                print(f"  Found Bókun experience ID: {widget['experience_id']}")
                prices = fetch_bokun_prices(widget["channel_uuid"], widget["experience_id"])
                print(f"  Scraped {len(prices)} available dates from Bókun.")
                if matched_pid not in bokun_prices_by_woo_id:
                    bokun_prices_by_woo_id[matched_pid] = {}
                bokun_prices_by_woo_id[matched_pid].update(prices)
            else:
                print("  No Bókun widget found on page.")
                
    print("\nFetching WooCommerce catalog products...")
    products = api_call("products?per_page=100&status=any")
    print(f"Fetched {len(products)} products from WooCommerce.")
    
    updated_count = 0
    total_compared = 0
    
    for p in products:
        pid = p["id"]
        p_name = p["name"]
        p_type = p["type"]
        
        if pid in KEYWORDS_MAPPING.values() and pid in bokun_prices_by_woo_id:
            bokun_dates_prices = bokun_prices_by_woo_id[pid]
            if not bokun_dates_prices:
                continue
                
            print(f"\n[SYNC] Checking: {p_name} (ID: {pid})")
            
            if p_type == "variable":
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
                        
                    bokun_price = bokun_dates_prices.get(var_date)
                    if bokun_price is not None:
                        total_compared += 1
                        current_price = float(var.get("regular_price") or 0.0)
                        
                        p3 = max(0.0, bokun_price - 2.0)
                        p5 = max(0.0, bokun_price - 4.0)
                        p10 = max(0.0, bokun_price - 6.0)
                        
                        expected_rules = {
                            "3": f"{p3:.2f}",
                            "5": f"{p5:.2f}",
                            "10": f"{p10:.2f}"
                        }
                        
                        current_rules = var.get("tiered_pricing_fixed_rules", {})
                        current_rules_normalized = {k: f"{float(v):.2f}" for k, v in current_rules.items() if v}
                        
                        price_differs = abs(current_price - bokun_price) > 0.01
                        rules_differ = current_rules_normalized != expected_rules
                        
                        if price_differs or rules_differ:
                            print(f"  - Variation {fecha_opt} (ID: {var_id}):")
                            if price_differs:
                                print(f"    Price differs: WooCommerce {current_price:.2f} € vs Bókun {bokun_price:.2f} €")
                            if rules_differ:
                                print(f"    Tiered rules differ: current {current_rules_normalized} vs expected {expected_rules}")
                                
                            variations_to_update.append({
                                "id": var_id,
                                "regular_price": f"{bokun_price:.2f}",
                                "tiered_pricing_type": "fixed",
                                "tiered_pricing_fixed_rules": expected_rules
                            })
                            
                if variations_to_update:
                    print(f"  Updating {len(variations_to_update)} variations in WooCommerce...")
                    batch_res = api_call(f"products/{pid}/variations/batch", "POST", {"update": variations_to_update})
                    updated_count += len(variations_to_update)
                else:
                    print("  All variations are up-to-date with Bókun pricing.")
                    
    print(f"\nPrice sync completed. Total compared variations: {total_compared} | Updated: {updated_count}")

if __name__ == "__main__":
    run_sync()
