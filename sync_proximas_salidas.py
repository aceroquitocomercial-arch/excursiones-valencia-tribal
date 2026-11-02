import os
import sys
import re
import html
from datetime import datetime, timedelta
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
WP_USER = os.environ.get("WP_USER") or env_vars.get("WP_USER") or env_vars.get("USER")
WP_APP_PASS = os.environ.get("WP_APP_PASS") or env_vars.get("WP_APP_PASS") or env_vars.get("PASSWORD")

if not WOO_CK or not WOO_CS:
    print("Error: WOO_CONSUMER_KEY o WOO_CONSUMER_SECRET no encontrados en variables de entorno ni en .env.")
    sys.exit(1)

woo_auth = HTTPBasicAuth(WOO_CK, WOO_CS)
wp_auth = HTTPBasicAuth(WP_USER, WP_APP_PASS) if (WP_USER and WP_APP_PASS) else None

# Modo dry-run por argumento (--dry-run / -d) o variable de entorno DRY_RUN=1
DRY_RUN = (
    "--dry-run" in sys.argv
    or "-d" in sys.argv
    or os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
)

if DRY_RUN:
    print("=== MODO DRY-RUN ACTIVADO (NO SE ESCRIBIRÁ NADA EN WORDPRESS NI WOOCOMMERCE) ===")
else:
    print("=== MODO LIVE ACTIVADO ===")

# ID del post del calendario en WordPress y horizonte de días
CALENDAR_POST_ID = 48903
CALENDAR_HORIZON_DAYS = 70
MAX_SHORT_DESC_DATES = 4

# Fichas excluidas manualmente (bonos/packs sin fecha de salida real)
EXCLUDED_PRODUCT_IDS = [49636]

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
WEEKDAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
WEEKDAYS_SHORT_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

CAL_START_MARKER = "<!-- CALENDARIO:INICIO -->"
CAL_END_MARKER = "<!-- CALENDARIO:FIN -->"
CALENDAR_SHORTCODE_TAG = "[vt_calendario_completo"
PROX_START_MARKER = "<!-- PROXIMAS-SALIDAS:INICIO -->"
PROX_END_MARKER = "<!-- PROXIMAS-SALIDAS:FIN -->"


def woo_api_call(endpoint, method="GET", body=None, params=None):
    url = f"{STORE_URL}/wp-json/wc/v3/{endpoint}"
    headers = {"Content-Type": "application/json", "User-Agent": "ValenciaTribe-Sync/1.0"}

    if DRY_RUN and method in ("POST", "PUT", "DELETE"):
        print(f"  [DRY-RUN] Simulando WooCommerce {method} {endpoint}")
        return {}

    response = requests.request(
        method,
        url,
        auth=woo_auth,
        headers=headers,
        json=body,
        params=params,
        timeout=45,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"HTTP {response.status_code} en WooCommerce {method} {endpoint}: {response.text}")
    return response.json()


def wp_api_call(endpoint, method="GET", body=None, params=None):
    url = f"{STORE_URL}/wp-json/wp/v2/{endpoint}"
    headers = {"Content-Type": "application/json", "User-Agent": "ValenciaTribe-Sync/1.0"}

    if DRY_RUN and method in ("POST", "PUT", "DELETE"):
        print(f"  [DRY-RUN] Simulando WordPress {method} {endpoint}")
        return {}

    response = requests.request(
        method,
        url,
        auth=wp_auth,
        headers=headers,
        json=body,
        params=params,
        timeout=45,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"HTTP {response.status_code} en WordPress {method} {endpoint}: {response.text}")
    return response.json()


def is_placeholder_date(date_str):
    if not date_str:
        return False
    lower = date_str.lower()
    return any(p in lower for p in ("próximamente", "proximamente", "coming soon"))


def is_validity_option(opt_str):
    """Detecta opciones de caducidad de bonos ('Valid until...', 'Válido hasta...') que no son fechas de salida."""
    if not opt_str:
        return False
    lower = opt_str.lower()
    return any(k in lower for k in ("valid until", "válido hasta", "valido hasta"))


def parse_variation_date_details(date_str):
    """
    Parsea cadenas de fecha en español e inglés (incluyendo rangos de varios días).
    Devuelve un dict:
      {
        "start": date,
        "end": date,
        "is_range": bool,
        "raw": str
      }
    o None si no es una fecha válida.
    """
    if not date_str or is_placeholder_date(date_str) or is_validity_option(date_str):
        return None

    months_es = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
    }
    months_en = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
    }

    # 1a. Rango en español entre dos meses: "28 de Octubre al 2 de Noviembre 2026"
    match_es_cross = re.search(
        r'(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)(?:\s+(\d{4}))?\s+al\s+(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+(\d{4})',
        date_str,
        re.I,
    )
    if match_es_cross:
        d1 = int(match_es_cross.group(1))
        m1 = months_es.get(match_es_cross.group(2).lower())
        y2 = int(match_es_cross.group(6))
        y1 = int(match_es_cross.group(3)) if match_es_cross.group(3) else y2
        d2 = int(match_es_cross.group(4))
        m2 = months_es.get(match_es_cross.group(5).lower())
        if m1 and m2:
            try:
                start_d = datetime(y1, m1, d1).date()
                end_d = datetime(y2, m2, d2).date()
                return {"start": start_d, "end": end_d, "is_range": True, "raw": date_str}
            except ValueError:
                return None

    # 1b. Fecha o rango en español en el mismo mes: "Sábado, 7 de Noviembre 2026" o "9 al 16 de Octubre 2026"
    match_es = re.search(r'(?:(\d+)\s+al\s+)?(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+(\d{4})', date_str, re.I)
    if match_es:
        d1_str, d2_str, month_name, year_str = match_es.groups()
        month = months_es.get(month_name.lower())
        year = int(year_str)
        if month:
            try:
                if d1_str:
                    start_d = datetime(year, month, int(d1_str)).date()
                    end_d = datetime(year, month, int(d2_str)).date()
                    return {"start": start_d, "end": end_d, "is_range": True, "raw": date_str}
                else:
                    single_d = datetime(year, month, int(d2_str)).date()
                    return {"start": single_d, "end": single_d, "is_range": False, "raw": date_str}
            except ValueError:
                return None

    # 2. Rango en inglés: "9–16 October 2026" o "9-16 October 2026"
    match_en_range = re.search(r'(\d+)\s*[–\-]\s*(\d+)\s+([a-zA-Z]+)\s+(\d{4})', date_str, re.I)
    if match_en_range:
        d1 = int(match_en_range.group(1))
        d2 = int(match_en_range.group(2))
        month = months_en.get(match_en_range.group(3).lower())
        year = int(match_en_range.group(4))
        if month:
            try:
                start_d = datetime(year, month, d1).date()
                end_d = datetime(year, month, d2).date()
                return {"start": start_d, "end": end_d, "is_range": True, "raw": date_str}
            except ValueError:
                return None

    # 3. Fecha única en inglés: "Saturday, 7 November 2026"
    match_en = re.search(r'(?:[a-zA-Z]+,\s+)?(\d+)\s+([a-zA-Z]+)\s+(\d{4})', date_str, re.I)
    if match_en:
        day = int(match_en.group(1))
        month = months_en.get(match_en.group(2).lower())
        year = int(match_en.group(3))
        if month:
            try:
                single_d = datetime(year, month, day).date()
                return {"start": single_d, "end": single_d, "is_range": False, "raw": date_str}
            except ValueError:
                return None

    return None


def parse_variation_date(date_str):
    """Misma interfaz que sync_mes_salida.py: devuelve la fecha de inicio o None."""
    details = parse_variation_date_details(date_str)
    return details["start"] if details else None


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
        batch = woo_api_call(
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


def extract_future_date_items(date_options, today):
    """
    Devuelve una lista ordenada cronológicamente de dicts de fecha ({start, end, is_range, raw})
    para todas las opciones con fecha > today, sin duplicados por (start, end).
    """
    seen = set()
    items = []
    for opt in date_options:
        details = parse_variation_date_details(opt)
        if details and details["start"] > today:
            key = (details["start"], details["end"])
            if key not in seen:
                seen.add(key)
                items.append(details)
    items.sort(key=lambda x: (x["start"], x["end"]))
    return items


def build_calendar_title(es_products_with_dates, today):
    """
    Construye el título actualizado para el post 48903 según el rango de meses
    cubierto por las salidas dentro del horizonte de CALENDAR_HORIZON_DAYS días.
    """
    horizon_end = today + timedelta(days=CALENDAR_HORIZON_DAYS)

    future_starts = []
    for product, future_items in es_products_with_dates:
        for item in future_items:
            if today < item["start"] <= horizon_end:
                future_starts.append(item["start"])

    if not future_starts:
        return None

    months_set = {(d.year, d.month) for d in future_starts}
    sorted_yms = sorted(months_set)

    (y1, m1) = sorted_yms[0]
    (y2, m2) = sorted_yms[-1]
    m1_name = MONTH_NAMES_ES[m1].lower()
    m2_name = MONTH_NAMES_ES[m2].lower()

    if (y1, m1) == (y2, m2):
        return f"Excursiones de fin de semana desde Valencia: calendario de {m1_name} {y1}"
    elif y1 == y2:
        return f"Excursiones de fin de semana desde Valencia: calendario de {m1_name} a {m2_name} {y1}"
    else:
        return f"Excursiones de fin de semana desde Valencia: calendario de {m1_name} {y1} a {m2_name} {y2}"


def format_single_date_for_short_desc(item, lang):
    """Formatea una fecha (o rango) para la línea de Próximas salidas / Next departures."""
    start_d = item["start"]
    end_d = item["end"]
    if lang == "ES":
        m1 = MONTH_NAMES_ES[start_d.month].lower()
        if item["is_range"]:
            m2 = MONTH_NAMES_ES[end_d.month].lower()
            if start_d.month == end_d.month:
                return f"del {start_d.day} al {end_d.day} de {m1}"
            return f"del {start_d.day} de {m1} al {end_d.day} de {m2}"
        wd = WEEKDAYS_ES[start_d.weekday()].lower()
        return f"{wd} {start_d.day} de {m1}"
    else:
        m1 = MONTH_NAMES_EN[start_d.month]
        if item["is_range"]:
            m2 = MONTH_NAMES_EN[end_d.month]
            if start_d.month == end_d.month:
                return f"{start_d.day}–{end_d.day} {m1}"
            return f"{start_d.day} {m1} – {end_d.day} {m2}"
        wd = WEEKDAYS_EN[start_d.weekday()]
        return f"{wd} {start_d.day} {m1}"


def join_natural_list(items, lang):
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    conj = " y " if lang == "ES" else " and "
    return ", ".join(items[:-1]) + conj + items[-1]


def build_proximas_salidas_line(future_items, lang):
    """
    Construye el párrafo HTML <p><strong>Próximas salidas:</strong> ...</p>
    con un máximo de 4 fechas ordenadas cronológicamente.
    """
    top_items = future_items[:MAX_SHORT_DESC_DATES]
    if lang == "ES":
        if top_items:
            formatted = [format_single_date_for_short_desc(i, "ES") for i in top_items]
            return f"<p><strong>Próximas salidas:</strong> {join_natural_list(formatted, 'ES')}.</p>"
        return "<p><strong>Próximas salidas:</strong> nuevas fechas en breve, apúntate a la lista de espera.</p>"
    else:
        if top_items:
            formatted = [format_single_date_for_short_desc(i, "EN") for i in top_items]
            return f"<p><strong>Next departures:</strong> {join_natural_list(formatted, 'EN')}.</p>"
        return "<p><strong>Next departures:</strong> new dates coming soon, join the waiting list.</p>"


def extract_between_markers(text, start_marker, end_marker):
    """Extrae el contenido interior entre start_marker y end_marker (ignorando <p> envolventes de wpautop)."""
    pattern = (
        re.escape(start_marker)
        + r"(?:\s*</p>)?(.*?)(?:<p>\s*)?"
        + re.escape(end_marker)
    )
    m = re.search(pattern, text, flags=re.DOTALL)
    return m.group(1).strip() if m else None


def replace_or_prepend_marked_block(current_text, start_marker, end_marker, inner_html, prepend_if_missing=False):
    """
    Reemplaza el bloque entre start_marker y end_marker.
    Si no existe y prepend_if_missing=True, lo inserta al principio.
    """
    marked_block = f"{start_marker}\n{inner_html}\n{end_marker}"
    if start_marker in current_text and end_marker in current_text:
        pattern = (
            r"(?:<p>\s*)?"
            + re.escape(start_marker)
            + r"(?:\s*</p>)?.*?(?:<p>\s*)?"
            + re.escape(end_marker)
            + r"(?:\s*</p>)?"
        )
        return re.sub(pattern, lambda _: marked_block, current_text, count=1, flags=re.DOTALL)
    elif prepend_if_missing:
        stripped = (current_text or "").lstrip()
        return f"{marked_block}\n{stripped}" if stripped else marked_block
    else:
        return None


def update_calendar_post(es_products_with_dates, today):
    """TAREA 1: Actualiza solo el TÍTULO del post 48903 en WordPress (el contenido lo gestiona el shortcode)."""
    print("=" * 72)
    print(f"TAREA 1 — TÍTULO DEL POST {CALENDAR_POST_ID} (CALENDARIO EN SHORTCODE)")
    print("=" * 72)

    new_title = build_calendar_title(es_products_with_dates, today)

    try:
        if wp_auth:
            post_data = wp_api_call(f"posts/{CALENDAR_POST_ID}", method="GET", params={"context": "edit"})
            current_content = post_data.get("content", {}).get("raw") or post_data.get("content", {}).get("rendered", "")
            current_title = post_data.get("title", {}).get("raw") or html.unescape(
                post_data.get("title", {}).get("rendered", "")
            )
        else:
            if not DRY_RUN:
                raise RuntimeError("Faltan credenciales WP_USER / WP_APP_PASS para editar el post en WordPress.")
            post_data = wp_api_call(f"posts/{CALENDAR_POST_ID}", method="GET")
            current_content = post_data.get("content", {}).get("rendered", "")
            current_title = html.unescape(post_data.get("title", {}).get("rendered", ""))
    except Exception as e:
        print(f"[ERROR] No se pudo leer el post {CALENDAR_POST_ID}: {e}")
        return False

    # Salvaguarda: comprobar shortcode y marcas
    has_shortcode = CALENDAR_SHORTCODE_TAG in current_content
    if has_shortcode:
        print(f"[SALVAGUARDA ACTIVA] El post contiene '{CALENDAR_SHORTCODE_TAG}'. El contenido está blindado y NO se toca.")
    else:
        print(f"[SALVAGUARDA] El script NO modifica el contenido del post (reservado para el shortcode).")

    if CAL_START_MARKER not in current_content or CAL_END_MARKER not in current_content:
        print(
            f"[AVISO] El post {CALENDAR_POST_ID} no contiene las dos marcas "
            f"{CAL_START_MARKER} y {CAL_END_MARKER}."
        )

    final_title = new_title or current_title
    title_changed = (current_title.strip() != final_title.strip())

    print(f"Título actual : {current_title}")
    print(f"Título nuevo  : {final_title}")
    print(f"¿Cambió título? {'SÍ' if title_changed else 'NO'}")

    if not title_changed:
        print(f"Post {CALENDAR_POST_ID}: SIN CAMBIOS en el título (no es necesario actualizar).\n")
        return True

    # El payload NUNCA incluye "content", solo actualiza el título
    payload = {"title": final_title}

    if DRY_RUN:
        print(f"[DRY-RUN] Actualizaría solo el título del post {CALENDAR_POST_ID} con: {payload}\n")
    else:
        try:
            wp_api_call(f"posts/{CALENDAR_POST_ID}", method="POST", body=payload)
            print(f"[ACTUALIZADO] Título del post {CALENDAR_POST_ID} actualizado correctamente.\n")
        except Exception as e:
            print(f"[ERROR] Falló la actualización del título del post {CALENDAR_POST_ID}: {e}\n")
            return False

    return True


def update_products_short_descriptions(all_products_plan):
    """TAREA 2: Actualiza la línea 'Próximas salidas' / 'Next departures' en cada ficha."""
    print("=" * 72)
    print("TAREA 2 — LÍNEA 'PRÓXIMAS SALIDAS' EN CADA FICHA (ES + EN)")
    print("=" * 72)

    updated_count = 0
    unchanged_count = 0
    error_count = 0

    for product, lang, date_options, future_items in all_products_plan:
        pid = product["id"]
        pname = product.get("name", f"Product {pid}")

        try:
            line_html = build_proximas_salidas_line(future_items, lang)
            current_short = product.get("short_description") or ""

            # Si una sola de las dos marcas está presente (corrupción manual), registrar error y saltar
            has_start = PROX_START_MARKER in current_short
            has_end = PROX_END_MARKER in current_short
            if has_start != has_end:
                raise RuntimeError(
                    f"Marcas incompletas en short_description (inicio={has_start}, fin={has_end}). Se omite por seguridad."
                )

            current_inner = extract_between_markers(current_short, PROX_START_MARKER, PROX_END_MARKER)
            if has_start and has_end and current_inner == line_html.strip():
                unchanged_count += 1
                print(f"[{lang}] ID {pid} | {pname} -> SIN CAMBIOS | {line_html}")
                continue

            new_short = replace_or_prepend_marked_block(
                current_short,
                PROX_START_MARKER,
                PROX_END_MARKER,
                line_html,
                prepend_if_missing=True,
            )

            action_tag = "reemplazando marcas" if (has_start and has_end) else "insertando al inicio"
            if DRY_RUN:
                print(f"[DRY-RUN] [{lang}] ID {pid} | {pname} ({action_tag}) -> {line_html}")
            else:
                print(f"[ACTUALIZANDO] [{lang}] ID {pid} | {pname} ({action_tag}) -> {line_html}")
                woo_api_call(f"products/{pid}", method="PUT", body={"short_description": new_short})

            updated_count += 1

        except Exception as e:
            error_count += 1
            print(f"[ERROR] [{lang}] ID {pid} | {pname}: {e}")

    return updated_count, unchanged_count, error_count


def run():
    today = datetime.today().date()
    print(f"Fecha de referencia: {today.isoformat()}\n")

    print("Leyendo fichas publicadas de WooCommerce...")
    products = fetch_all_published_products()
    print(f"Total de fichas publicadas leídas: {len(products)}\n")

    all_products_plan = []
    es_products_with_dates = []
    skipped_count = 0

    for p in products:
        pid = p["id"]
        pname = p.get("name", f"Product {pid}")
        lang = "EN" if is_english_product(p) else "ES"
        date_options = extract_date_options(p)
        non_validity_options = [opt for opt in date_options if not is_validity_option(opt)]

        if pid in EXCLUDED_PRODUCT_IDS or (date_options and not non_validity_options):
            skipped_count += 1
            print(f"[EXCLUIDA] [{lang}] ID {pid} | {pname} | FECHA leída: {date_options}")
            continue

        future_items = extract_future_date_items(non_validity_options, today)
        all_products_plan.append((p, lang, date_options, future_items))
        if lang == "ES":
            es_products_with_dates.append((p, future_items))

    print()

    # TAREA 1: Título en el post 48903 (solo fichas en español; contenido gestionado por shortcode)
    cal_ok = update_calendar_post(es_products_with_dates, today)

    # TAREA 2: Línea "Próximas salidas" en la descripción corta de cada ficha (ES y EN)
    updated_count, unchanged_count, error_count = update_products_short_descriptions(all_products_plan)

    print(
        f"\nResumen final: Post {CALENDAR_POST_ID} (título): {'OK' if cal_ok else 'ERROR'} | "
        f"Fichas {'que actualizaría' if DRY_RUN else 'actualizadas'}: {updated_count} | "
        f"Sin cambios: {unchanged_count} | Excluidas: {skipped_count} | "
        f"Errores en fichas: {error_count} | Total leídas: {len(products)}"
    )


if __name__ == "__main__":
    run()
