import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth
import re
from datetime import datetime

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
    print("Error: WOO_CONSUMER_KEY or WOO_CONSUMER_SECRET not found in .env file.")
    sys.exit(1)

auth = HTTPBasicAuth(WOO_CK, WOO_CS)

# Custom formatting mapping for known excursions to make them look premium and match user's design style
EXCURSIONS_METADATA = {
    "montanejos": {
        "title": "MONTANEJOS",
        "emoji": "🏞️",
        "subtitle": "Día completo de río y termas con taller de bachata",
        "extra_info": "💵 Precio: 39€ (Descuentos para grupos en la web)\n🎫 Entrada a las termas y seguro de viaje incluido"
    },
    "tabarca": {
        "title": "ISLA DE TABARCA",
        "emoji": "🌊",
        "subtitle": "Día completo de playa, snorkel y calas de ensueño",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)\n🚢 Barco ida y vuelta y seguro de viaje incluido"
    },
    "moraig": {
        "title": "CALA MORAIG",
        "emoji": "🌊",
        "subtitle": "Senderismo de los acantilados y snorkel en aguas turquesas",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "moraira": {
        "title": "MORAIRA",
        "emoji": "🏖️",
        "subtitle": "Ruta de acantilados y calas espectaculares",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "laguna rosa": {
        "title": "LAGUNA ROSA Y TORREVIEJA",
        "emoji": "🌸",
        "subtitle": "Naturaleza, salinas rosadas e historia de Alicante",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "torrevieja": {
        "title": "LAGUNA ROSA Y TORREVIEJA",
        "emoji": "🌸",
        "subtitle": "Naturaleza, salinas rosadas e historia de Alicante",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "altea": {
        "title": "ALTEA Y GUADALEST",
        "emoji": "🏰",
        "subtitle": "Visita al pueblo blanco de Altea y el castillo medieval de Guadalest",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "guadalest": {
        "title": "ALTEA Y GUADALEST",
        "emoji": "🏰",
        "subtitle": "Visita al pueblo blanco de Altea y el castillo medieval de Guadalest",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "peñiscola": {
        "title": "PEÑÍSCOLA Y SAGUNTO",
        "emoji": "🏰",
        "subtitle": "Historia, castillos y encanto mediterráneo",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "sagunto": {
        "title": "PEÑÍSCOLA Y SAGUNTO",
        "emoji": "🏰",
        "subtitle": "Historia, castillos y encanto mediterráneo",
        "extra_info": "💵 Precio: 35€ (Descuentos para grupos en la web)"
    },
    "pirineo catalan": {
        "title": "ESCAPADA PIRINEO CATALÁN: AIGÜESTORTES",
        "emoji": "🏔️",
        "subtitle": "4 días de naturaleza, alta montaña y lagos glaciares",
        "extra_info": "💵 Consulta precio y condiciones en la web"
    },
    "aigüestortes": {
        "title": "ESCAPADA PIRINEO CATALÁN: AIGÜESTORTES",
        "emoji": "🏔️",
        "subtitle": "4 días de naturaleza, alta montaña y lagos glaciares",
        "extra_info": "💵 Consulta precio y condiciones en la web"
    }
}

def parse_spanish_date(date_str):
    # Match something like "Sábado, 15 de Agosto 2026"
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

def api_call(endpoint):
    url = f"{STORE_URL}/wp-json/wc/v3/{endpoint}"
    response = requests.get(url, auth=auth, timeout=30)
    response.raise_for_status()
    return response.json()

def main():
    print("Obteniendo excursiones vigentes desde WooCommerce...")
    try:
        products = api_call("products?per_page=100&status=publish")
    except Exception as e:
        print(f"Error al conectar con WooCommerce: {e}")
        sys.exit(1)
        
    today = datetime.today().date()
    active_excursions = []
    
    for p in products:
        # We only consider visible, in-stock products
        if p.get("catalog_visibility") != "visible" or p.get("stock_status") != "instock":
            continue
            
        p_id = p["id"]
        p_name = p["name"]
        p_type = p["type"]
        p_price = p.get("price") or p.get("regular_price") or "35"
        
        # Match metadata template
        meta = None
        for kw, info in EXCURSIONS_METADATA.items():
            if kw in p_name.lower():
                meta = info
                break
                
        if not meta:
            # Fallback metadata if not matches predefined list
            meta = {
                "title": p_name.split("desde")[0].replace("Excursión a", "").replace("Excursión", "").strip().upper(),
                "emoji": "🚌",
                "subtitle": p_name,
                "extra_info": f"💵 Precio: {p_price}€ (Descuentos para grupos en la web)"
            }
            
        # Get variations for dates
        dates = []
        if p_type == "variable":
            try:
                variations = api_call(f"products/{p_id}/variations?per_page=100")
            except Exception as e:
                print(f"Error al obtener variaciones del producto {p_name}: {e}")
                continue
                
            for v in variations:
                # Find date attribute
                date_val = None
                for attr in v.get("attributes", []):
                    if attr.get("name").upper() == "FECHA":
                        date_val = attr.get("option")
                        break
                if date_val:
                    parsed_dt = parse_spanish_date(date_val)
                    if parsed_dt and parsed_dt >= today:
                        if (parsed_dt, date_val) not in dates:
                            dates.append((parsed_dt, date_val))
        else:
            # Simple product, look at attributes or regular dates
            for attr in p.get("attributes", []):
                if attr.get("name").upper() == "FECHA":
                    for opt in attr.get("options", []):
                        parsed_dt = parse_spanish_date(opt)
                        if parsed_dt and parsed_dt >= today:
                            if (parsed_dt, opt) not in dates:
                                dates.append((parsed_dt, opt))
                                
        if dates:
            # Sort dates chronologically
            dates.sort(key=lambda x: x[0])
            active_excursions.append({
                "meta": meta,
                "dates": [d[1] for d in dates],
                "price": p_price,
                "link": p.get("permalink", "")
            })
            
    if not active_excursions:
        print("No se encontraron excursiones activas con fechas futuras.")
        return
        
    # Generate the post text
    now = datetime.now()
    month_names = {
        8: "AGOSTO", 9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
        1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL", 5: "MAYO", 6: "JUNIO", 7: "JULIO"
    }
    current_month_str = month_names.get(now.month, "AGOSTO")
    next_month_str = month_names.get((now.month % 12) + 1, "SEPTIEMBRE")
    
    post_lines = []
    post_lines.append(f"🔥 *CALENDARIO DE {current_month_str}/{next_month_str}*")
    post_lines.append("🚌 *EXCURSIONES DESDE VALENCIA*")
    post_lines.append("")
    post_lines.append("🌍 *AQUÍ puedes ver toda info, horarios, precios, reservas y pagos de todas nuestras actividades 👇:*")
    post_lines.append("https://valenciatribal.com/todos-los-tours/")
    post_lines.append("")
    post_lines.append("‼️ *IMPORTANTE:* Recuerda hacer clic en *LEER MÁS* para poder visualizar 👀 todo el mensaje completo 👇")
    post_lines.append("")
    post_lines.append("~")
    post_lines.append("")
    
    for exc in active_excursions:
        meta = exc["meta"]
        post_lines.append(f"✅ *{meta['title']}* 🚌: {meta['subtitle']} {meta['emoji']}")
        for d in exc["dates"]:
            display_date = d
            if " 2026" in display_date:
                display_date = display_date.replace(" 2026", "")
            post_lines.append(f"📆 {display_date}")
            
        post_lines.append(meta["extra_info"])
        if exc.get("link"):
            post_lines.append(f"🔗 Info y Reservas: {exc['link']}")
        post_lines.append("~")
        post_lines.append("")
        
    post_content = "\n".join(post_lines)
    
    # Save to file
    out_file = os.path.join(os.path.dirname(__file__), "whatsapp_post.txt")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(post_content)
        
    try:
        print(f"\n¡Mensaje de WhatsApp generado exitosamente y guardado en:\n{out_file}\n")
    except UnicodeEncodeError:
        sys.stdout.buffer.write(f"\n¡Mensaje de WhatsApp generado exitosamente y guardado en:\n{out_file}\n\n".encode('utf-8'))

    print("=" * 60)
    try:
        print(post_content)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(post_content.encode('utf-8'))
        print()
    print("=" * 60)

if __name__ == "__main__":
    main()
