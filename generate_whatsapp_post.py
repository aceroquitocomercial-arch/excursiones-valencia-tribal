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

# Custom formatting mapping for known excursions and activities
# Key can be product ID or slug substring
EXCURSIONS_METADATA_BY_ID = {
    47879: {
        "title": "MONTANEJOS",
        "emoji": "🏞️",
        "subtitle": "Día completo de río y termas con taller de bachata",
        "extra_info": "🎫 Entrada a las termas y seguro de viaje incluido"
    },
    47938: {
        "title": "ALTEA Y GUADALEST",
        "emoji": "🏰",
        "subtitle": "Visita al pueblo blanco de Altea y el castillo medieval de Guadalest",
        "extra_info": ""
    },
    48877: {
        "title": "ALTEA Y BENIDORM",
        "emoji": "🏛️",
        "subtitle": "Casco antiguo, cúpulas azules y tarde de playa mediterránea",
        "extra_info": ""
    },
    47943: {
        "title": "PEÑÍSCOLA Y SAGUNTO",
        "emoji": "🏰",
        "subtitle": "Historia, castillos y encanto mediterráneo",
        "extra_info": ""
    },
    48876: {
        "title": "PEÑÍSCOLA Y CUEVAS DE SAN JOSÉ",
        "emoji": "🛶",
        "subtitle": "Paseo en barca por el río subterráneo y castillo del Papa Luna",
        "extra_info": "🎫 Entrada a las Cuevas de San José (paseo en barca) incluida"
    },
    48104: {
        "title": "CALPE Y PEÑÓN DE IFACH",
        "emoji": "🪨",
        "subtitle": "Costa Blanca, calas cristalinas y vistas al Peñón",
        "extra_info": ""
    },
    48859: {
        "title": "ALBARRACÍN Y TERUEL",
        "emoji": "🏰",
        "subtitle": "Pueblo medieval amurallado y arquitectura mudéjar",
        "extra_info": ""
    },
    48860: {
        "title": "SENDERISMO CAÑÓN ROJO DE TERUEL",
        "emoji": "🏜️",
        "subtitle": "El lejano oeste de Aragón y cañones de arcilla roja",
        "extra_info": ""
    },
    48861: {
        "title": "SENDERISMO ALTO DE PIPA (VALL D'UIXÓ)",
        "emoji": "⛰️",
        "subtitle": "Sierra de Espadán y panorámicas espectaculares",
        "extra_info": ""
    },
    48862: {
        "title": "SENDERISMO CAÑÓN DEL MASCARAT",
        "emoji": "🥾",
        "subtitle": "Garganta profunda y puentes históricos entre Altea y Calpe",
        "extra_info": ""
    },
    48875: {
        "title": "MURCIA Y CARTAGENA",
        "emoji": "🏛️",
        "subtitle": "Teatro Romano, puerto histórico y Catedral en un día",
        "extra_info": ""
    },
    48884: {
        "title": "VIAJE A MARRUECOS (8 DÍAS)",
        "emoji": "🐪",
        "subtitle": "Desierto de Merzouga, Marrakech, Fez y Chefchaouen",
        "extra_info": "🐪 Alojamiento, transporte y excursión al desierto incluidos"
    },
    48891: {
        "title": "FIESTA EN CATAMARÁN",
        "emoji": "⛵",
        "subtitle": "Boat party con DJ en mar abierto y tardeo",
        "extra_info": ""
    },
    47937: {
        "title": "ISLA DE TABARCA",
        "emoji": "🌊",
        "subtitle": "Día completo de playa, snorkel y calas de ensueño",
        "extra_info": "🚢 Barco ida y vuelta y seguro de viaje incluido"
    },
    48067: {
        "title": "CALA MORAIG",
        "emoji": "🌊",
        "subtitle": "Senderismo de los acantilados y snorkel en aguas turquesas",
        "extra_info": ""
    },
    47941: {
        "title": "MORAIRA",
        "emoji": "🏖️",
        "subtitle": "Ruta de acantilados y calas espectaculares",
        "extra_info": ""
    },
    47939: {
        "title": "LAGUNA ROSA Y TORREVIEJA",
        "emoji": "🌸",
        "subtitle": "Naturaleza, salinas rosadas e historia de Alicante",
        "extra_info": ""
    },
    48121: {
        "title": "ESCAPADA PIRINEO CATALÁN: AIGÜESTORTES",
        "emoji": "🏔️",
        "subtitle": "4 días de naturaleza, alta montaña y lagos glaciares",
        "extra_info": ""
    },
    48123: {
        "title": "ESCAPADA PIRINEOS FRANCESES: GAVARNIE Y CAUTERETS",
        "emoji": "🏔️",
        "subtitle": "Alta montaña y frontera francesa",
        "extra_info": ""
    },
    48125: {
        "title": "ESCAPADA PIRINEO ARAGONÉS: ORDESA Y FORMIGAL",
        "emoji": "🏔️",
        "subtitle": "4 días de montaña y valles glaciares",
        "extra_info": ""
    },
    48119: {
        "title": "SENDERISMO CUEVA TALLADA",
        "emoji": "🌊",
        "subtitle": "Cueva excavada en el acantilado y vistas al Mediterráneo",
        "extra_info": ""
    },
    48110: {
        "title": "XÀTIVA Y BOCAIRENT",
        "emoji": "🏰",
        "subtitle": "Castillo medieval, casco histórico y Covetes dels Moros",
        "extra_info": ""
    },
    48105: {
        "title": "FUENTES DEL ALGAR Y CALPE",
        "emoji": "💧",
        "subtitle": "Cascadas de agua cristalina, naturaleza y playa",
        "extra_info": ""
    },
    48066: {
        "title": "DÉNIA EN BARCO",
        "emoji": "⛵",
        "subtitle": "Mejillones, barra libre y baño en alta mar",
        "extra_info": ""
    },
    47945: {
        "title": "CRUCERO POR LOS FIORDOS VALENCIANOS",
        "emoji": "🚢",
        "subtitle": "Cañón del Júcar y paisajes fluviales impresionantes",
        "extra_info": ""
    },
    47940: {
        "title": "DÉNIA Y CUEVA DE LAS CALAVERAS",
        "emoji": "🦇",
        "subtitle": "Cueva prehistórica, castillo y paseo marítimo",
        "extra_info": ""
    },
    47881: {
        "title": "ATARDECER EN LA ALBUFERA",
        "emoji": "🌅",
        "subtitle": "Paseo tradicional en barca y paella a leña",
        "extra_info": ""
    },
    47880: {
        "title": "SENDERISMO PUENTES COLGANTES DE CHULILLA",
        "emoji": "🌉",
        "subtitle": "Cañón del Turia, puentes colgantes y Charco Azul",
        "extra_info": ""
    },
    48296: {
        "title": "JÁVEA Y DÉNIA",
        "emoji": "🏖️",
        "subtitle": "Playas, calas, castillo árabe y casco antiguo",
        "extra_info": ""
    },
    48127: {
        "title": "RUTA POR ANDALUCÍA (4 DÍAS)",
        "emoji": "💃",
        "subtitle": "Sevilla, Granada, Córdoba y Málaga",
        "extra_info": ""
    }
}

def clean_no_prices(text):
    if not text:
        return ""
    # Strip any price patterns like "por 14 €", "por 85 €", "14 €", "14€", "14 euros"
    cleaned = re.sub(r'\s*(?:por\s+)?\d+(?:[\.,]\d+)?\s*(?:€|euros?)\b', '', text, flags=re.I)
    cleaned = re.sub(r'—\s*$', '', cleaned).strip()
    return cleaned

def parse_spanish_date(date_str):
    if not date_str:
        return None
    # Check range like "9 al 16 de Octubre 2026"
    range_match = re.search(r'(\d+)\s+al\s+\d+\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)(?:\s+(\d{4}))?', date_str, re.I)
    months = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
    }
    current_year = datetime.now().year
    
    if range_match:
        day = int(range_match.group(1))
        month_name = range_match.group(2).lower()
        year = int(range_match.group(3)) if range_match.group(3) else current_year
        month = months.get(month_name)
        if month:
            try:
                return datetime(year, month, day).date()
            except ValueError:
                pass

    # Match something like "Sábado, 15 de Agosto 2026" or "15 de Agosto"
    match = re.search(r'(?:[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+,\s+)?(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)(?:\s+(\d{4}))?', date_str, re.I)
    if not match:
        # Check "Válido hasta el 31 de Enero 2027"
        valido_match = re.search(r'(\d+)\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+(\d{4})', date_str, re.I)
        if valido_match:
            day = int(valido_match.group(1))
            month_name = valido_match.group(2).lower()
            year = int(valido_match.group(3))
            month = months.get(month_name)
            if month:
                try:
                    return datetime(year, month, day).date()
                except ValueError:
                    pass
        return None
        
    day = int(match.group(1))
    month_name = match.group(2).lower()
    year = int(match.group(3)) if match.group(3) else current_year
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
        p_name = clean_no_prices(p["name"])
        p_type = p["type"]
        
        # Match metadata template by ID first, then fallback
        meta = EXCURSIONS_METADATA_BY_ID.get(p_id)
        if not meta:
            # Fallback metadata
            clean_title = p_name.split("desde")[0].replace("Excursión a", "").replace("Excursión", "").replace("Senderismo", "").strip().upper()
            meta = {
                "title": clean_title,
                "emoji": "🚌",
                "subtitle": p_name,
                "extra_info": ""
            }
        else:
            # Ensure titles and subtitles never contain prices
            meta = dict(meta)
            meta["title"] = clean_no_prices(meta["title"])
            meta["subtitle"] = clean_no_prices(meta["subtitle"])
            meta["extra_info"] = clean_no_prices(meta.get("extra_info", ""))
            
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
                    elif not parsed_dt and "válido" in date_val.lower():
                        dates.append((datetime(2099, 1, 1).date(), date_val))
        else:
            # Simple product, look at attributes or regular dates
            for attr in p.get("attributes", []):
                if attr.get("name").upper() == "FECHA":
                    for opt in attr.get("options", []):
                        parsed_dt = parse_spanish_date(opt)
                        if parsed_dt and parsed_dt >= today:
                            if (parsed_dt, opt) not in dates:
                                dates.append((parsed_dt, opt))
                        elif not parsed_dt and "válido" in opt.lower():
                            dates.append((datetime(2099, 1, 1).date(), opt))
                                
        if dates:
            # Sort dates chronologically
            dates.sort(key=lambda x: x[0])
            active_excursions.append({
                "id": p_id,
                "meta": meta,
                "dates": [d[1] for d in dates],
                "earliest_date": dates[0][0],
                "link": p.get("permalink", "")
            })
            
    if not active_excursions:
        print("No se encontraron excursiones activas con fechas futuras.")
        return
        
    # Sort excursions chronologically by earliest date
    active_excursions.sort(key=lambda x: x["earliest_date"])
    
    # Generate the post text
    now = datetime.now()
    month_names = {
        8: "AGOSTO", 9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
        1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL", 5: "MAYO", 6: "JUNIO", 7: "JULIO"
    }
    current_month_str = month_names.get(now.month, "OCTUBRE")
    next_month_str = month_names.get((now.month % 12) + 1, "NOVIEMBRE")
    
    post_lines = []
    post_lines.append(f"🔥 *CALENDARIO DE {current_month_str} / {next_month_str}*")
    post_lines.append("🚌 *EXCURSIONES Y VIAJES DESDE VALENCIA*")
    post_lines.append("")
    post_lines.append("🌍 *AQUÍ puedes ver toda la info, horarios, reservas y pagos de todas nuestras actividades 👇:*")
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
            
        if meta.get("extra_info") and meta["extra_info"].strip():
            post_lines.append(meta["extra_info"].strip())
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
