import os
import sys
import re
from datetime import datetime
import requests
from requests.auth import HTTPBasicAuth

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 1. Cargar credenciales desde variables de entorno o .env local
env_path = os.path.join(os.path.dirname(__file__), ".env")
env_vars = {}

if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip()

STORE_URL = (os.environ.get("STORE_URL") or env_vars.get("STORE_URL", "https://valenciatribal.com")).rstrip("/")
WOO_CK = os.environ.get("WOO_CONSUMER_KEY") or env_vars.get("WOO_CONSUMER_KEY")
WOO_CS = os.environ.get("WOO_CONSUMER_SECRET") or env_vars.get("WOO_CONSUMER_SECRET")

if not WOO_CK or not WOO_CS:
    print("Error: WOO_CONSUMER_KEY o WOO_CONSUMER_SECRET no encontrados en variables de entorno ni en .env.")
    sys.exit(1)

auth = HTTPBasicAuth(WOO_CK, WOO_CS)

# Modo dry-run por argumento (--dry-run / -d) o variable de entorno DRY_RUN=1
DRY_RUN = (
    "--dry-run" in sys.argv
    or "-d" in sys.argv
    or os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
)

if DRY_RUN:
    print("=== MODO DRY-RUN ACTIVADO (NO SE ESCRIBIRÁ NADA EN WOOCOMMERCE) ===")
else:
    print("=== MODO LIVE ACTIVADO ===")

# Categorías de fichas en inglés
ENGLISH_CATEGORIES = {
    "day trips from valencia",
    "multi-day trips from valencia",
    "nightlife in valencia",
}
ENGLISH_CATEGORY_SLUGS = {
    "day-trips-from-valencia",
    "multi-day-trips-from-valencia",
    "nightlife-in-valencia",
}

MONTH_NAMES_ES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]

MONTH_NAMES_EN = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

# Configuración de los dos atributos globales (ES y EN)
ATTR_CONFIG = {
    "ES": {
        "name": "Mes de salida",
        "slug": "mes-de-salida",
        "taxonomy": "pa_mes-de-salida",
        "placeholder": "Próximamente",
    },
    "EN": {
        "name": "Departure month",
        "slug": "departure-month",
        "taxonomy": "pa_departure-month",
        "placeholder": "Coming soon",
    },
}


def api_call(endpoint, method="GET", body=None, params=None):
    url = f"{STORE_URL}/wp-json/wc/v3/{endpoint}"
    headers = {"Content-Type": "application/json"}

    if DRY_RUN and method in ("POST", "PUT", "DELETE"):
        print(f"  [DRY-RUN] Simulando {method} {endpoint}")
        return {}

    response = requests.request(
        method,
        url,
        auth=auth,
        headers=headers,
        json=body,
        params=params,
        timeout=45,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"HTTP {response.status_code} en {method} {endpoint}: {response.text}")
    return response.json()


def parse_variation_date(date_str):
    """Parsea cadenas de fecha en español e inglés (incluyendo rangos de varios días)."""
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

    # 1. Fecha o rango en español en el mismo mes: "Sábado, 7 de Noviembre 2026" o "9 al 16 de Octubre 2026"
    match_es = re.search(r'(?:(\d+)\s+al\s+)?(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+(\d{4})', date_str, re.I)
    if match_es:
        # Si es un rango cruzado entre dos meses (ej. "28 de Octubre al 2 de Noviembre 2026"), priorizar fecha de inicio
        match_es_cross = re.search(
            r'(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)(?:\s+(\d{4}))?\s+al\s+\d+\s+de\s+[a-zA-ZáéíóúÁÉÍÓÚñÑ]+\s+(\d{4})',
            date_str,
            re.I,
        )
        if match_es_cross:
            day = int(match_es_cross.group(1))
            month_name = match_es_cross.group(2).lower()
            year = int(match_es_cross.group(3) or match_es_cross.group(4))
        else:
            day = int(match_es.group(1) or match_es.group(2))
            month_name = match_es.group(3).lower()
            year = int(match_es.group(4))
        month = months_es.get(month_name)
        if month:
            try:
                return datetime(year, month, day).date()
            except ValueError:
                return None

    # 2. Rango en inglés: "9–16 October 2026" o "9-16 October 2026"
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

    # 3. Fecha única en inglés: "Saturday, 7 November 2026"
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


def is_english_product(product):
    """Detecta si la ficha pertenece a las categorías en inglés."""
    for cat in product.get("categories", []):
        cat_name = (cat.get("name") or "").strip().lower()
        cat_slug = (cat.get("slug") or "").strip().lower()
        if cat_name in ENGLISH_CATEGORIES or cat_slug in ENGLISH_CATEGORY_SLUGS:
            return True
    return False


def fetch_all_published_products():
    """Obtiene todas las fichas con status='publish' usando paginación."""
    all_products = []
    page = 1
    per_page = 100
    while True:
        batch = api_call(
            "products",
            method="GET",
            params={"status": "publish", "per_page": per_page, "page": page},
        )
        if not batch:
            break
        all_products.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return all_products


def extract_date_options(product):
    """Lee las opciones del atributo FECHA / Date de la ficha (sin modificarlo)."""
    for attr in product.get("attributes", []):
        attr_name = (attr.get("name") or "").strip().upper()
        attr_slug = (attr.get("slug") or "").strip().lower()
        if attr_name in ("FECHA", "DATE") or attr_slug in ("fecha", "date", "pa_fecha", "pa_date"):
            return list(attr.get("options") or [])
    return []


def compute_departure_months(date_options, lang, today):
    """
    Calcula la lista ordenada de meses futuros (ej. ['Noviembre 2026', 'Diciembre 2026'])
    o ['Próximamente'] / ['Coming soon'] si no hay fechas futuras.
    """
    future_year_months = set()
    for opt in date_options:
        parsed = parse_variation_date(opt)
        if parsed and parsed >= today:
            future_year_months.add((parsed.year, parsed.month))

    if not future_year_months:
        return [ATTR_CONFIG[lang]["placeholder"]]

    sorted_ym = sorted(future_year_months)
    month_names = MONTH_NAMES_EN if lang == "EN" else MONTH_NAMES_ES
    return [f"{month_names[m]} {y}" for y, m in sorted_ym]


def ensure_global_attributes_and_terms(required_terms_by_lang):
    """
    Garantiza que existan los atributos globales 'Mes de salida' (pa_mes-de-salida)
    y 'Departure month' (pa_departure-month), así como los términos necesarios.
    Devuelve un diccionario {lang: attr_id}.
    """
    existing_attrs = api_call("products/attributes", method="GET")
    attr_ids = {}

    for lang, cfg in ATTR_CONFIG.items():
        found_id = None
        for a in existing_attrs:
            a_slug = (a.get("slug") or "").strip().lower()
            a_name = (a.get("name") or "").strip().lower()
            if a_slug in (cfg["taxonomy"], cfg["slug"]) or a_name == cfg["name"].lower():
                found_id = a["id"]
                break

        if found_id is None:
            if DRY_RUN:
                print(f"[DRY-RUN] Crearía atributo global '{cfg['name']}' ({cfg['taxonomy']})")
                found_id = -1 if lang == "ES" else -2
            else:
                print(f"Creando atributo global '{cfg['name']}' ({cfg['taxonomy']})...")
                created = api_call(
                    "products/attributes",
                    method="POST",
                    body={
                        "name": cfg["name"],
                        "slug": cfg["slug"],
                        "type": "select",
                        "order_by": "menu_order",
                        "has_archives": False,
                    },
                )
                found_id = created["id"]

        attr_ids[lang] = found_id

        # Comprobar y crear términos que falten
        needed_terms = required_terms_by_lang.get(lang, set())
        if not needed_terms:
            continue

        existing_term_names = set()
        if found_id > 0:
            page = 1
            while True:
                terms_batch = api_call(
                    f"products/attributes/{found_id}/terms",
                    method="GET",
                    params={"per_page": 100, "page": page},
                )
                if not terms_batch:
                    break
                for t in terms_batch:
                    existing_term_names.add((t.get("name") or "").strip())
                if len(terms_batch) < 100:
                    break
                page += 1

        for term_name in sorted(needed_terms):
            if term_name not in existing_term_names:
                if DRY_RUN:
                    print(f"[DRY-RUN] Crearía término '{term_name}' en '{cfg['name']}'")
                else:
                    print(f"Creando término '{term_name}' en '{cfg['name']}'...")
                    api_call(
                        f"products/attributes/{found_id}/terms",
                        method="POST",
                        body={"name": term_name},
                    )

    return attr_ids


def is_target_attribute(attr, lang, attr_id):
    """Identifica si un atributo de una ficha corresponde al atributo objetivo (ES o EN)."""
    cfg = ATTR_CONFIG[lang]
    a_id = attr.get("id")
    a_name = (attr.get("name") or "").strip().lower()
    a_slug = (attr.get("slug") or "").strip().lower()
    if attr_id > 0 and a_id == attr_id:
        return True
    if a_slug in (cfg["taxonomy"], cfg["slug"]) or a_name == cfg["name"].lower():
        return True
    return False


def run():
    today = datetime.today().date()
    print(f"Fecha de referencia: {today.isoformat()}\n")

    print("Leyendo fichas publicadas de WooCommerce...")
    products = fetch_all_published_products()
    print(f"Total de fichas publicadas leídas: {len(products)}\n")

    # 1. Calcular para cada ficha su idioma y los meses de salida correspondientes
    plan = []
    required_terms = {"ES": set(), "EN": set()}

    for p in products:
        lang = "EN" if is_english_product(p) else "ES"
        date_options = extract_date_options(p)
        desired_months = compute_departure_months(date_options, lang, today)
        required_terms[lang].update(desired_months)
        plan.append((p, lang, date_options, desired_months))

    # 2. Garantizar que existan los atributos globales y sus términos
    attr_ids = ensure_global_attributes_and_terms(required_terms)
    print()

    # 3. Revisar cada ficha y actualizar solo si el valor ha cambiado
    updated_count = 0
    unchanged_count = 0
    error_count = 0

    for p, lang, date_options, desired_months in plan:
        pid = p["id"]
        pname = p.get("name", f"Product {pid}")
        cfg = ATTR_CONFIG[lang]
        target_attr_id = attr_ids[lang]

        try:
            current_attrs = p.get("attributes", [])
            existing_target = None
            for a in current_attrs:
                if is_target_attribute(a, lang, target_attr_id):
                    existing_target = a
                    break

            current_options = list(existing_target.get("options") or []) if existing_target else []
            already_up_to_date = (
                existing_target is not None
                and (target_attr_id < 0 or existing_target.get("id") == target_attr_id)
                and existing_target.get("visible") is True
                and existing_target.get("variation") is False
                and current_options == desired_months
            )

            if already_up_to_date:
                unchanged_count += 1
                print(f"[{lang}] ID {pid} | {pname} -> SIN CAMBIOS ({cfg['name']}: {desired_months})")
                continue

            # Construir lista completa de atributos preservando intactos todos los existentes
            new_attributes_payload = []
            replaced = False

            new_attr_entry = {
                "id": target_attr_id,
                "name": cfg["name"],
                "visible": True,
                "variation": False,
                "options": desired_months,
            }

            for a in current_attrs:
                if is_target_attribute(a, lang, target_attr_id):
                    if not replaced:
                        new_attributes_payload.append(new_attr_entry)
                        replaced = True
                else:
                    # Reenviar exactamente como estaba (id, name, position, visible, variation, options)
                    preserved = {
                        "id": a.get("id", 0),
                        "name": a.get("name"),
                        "position": a.get("position", 0),
                        "visible": a.get("visible", True),
                        "variation": a.get("variation", False),
                        "options": a.get("options", []),
                    }
                    new_attributes_payload.append(preserved)

            if not replaced:
                new_attributes_payload.append(new_attr_entry)

            prev_str = f" (antes: {current_options})" if existing_target else " (nuevo atributo en ficha)"
            if DRY_RUN:
                print(f"[DRY-RUN] [{lang}] ID {pid} | {pname} -> {cfg['name']}: {desired_months}{prev_str} | FECHA leída: {date_options}")
            else:
                print(f"[ACTUALIZANDO] [{lang}] ID {pid} | {pname} -> {cfg['name']}: {desired_months}{prev_str}")
                api_call(f"products/{pid}", method="PUT", body={"attributes": new_attributes_payload})

            updated_count += 1

        except Exception as e:
            error_count += 1
            print(f"[ERROR] [{lang}] ID {pid} | {pname}: {e}")

    print(
        f"\nResumen: {'Actualizaría' if DRY_RUN else 'Actualizadas'}: {updated_count} | "
        f"Sin cambios: {unchanged_count} | Errores: {error_count} | Total leídas: {len(products)}"
    )


if __name__ == "__main__":
    run()
