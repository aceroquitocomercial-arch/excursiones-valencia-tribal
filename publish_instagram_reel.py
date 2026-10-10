#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Automated Daily Instagram Reels Publisher using Cloudflare R2
Project: Valencia Tribal (valenciatribal.com)

Features:
- Connects to Cloudflare R2 (S3 API).
- Checks queue/ for the next video and its companion caption file (.txt).
- Resolves a public HTTPS URL (via custom/public domain or presigned S3 URL).
- Creates an Instagram Reels container via Meta Graph API v21.0.
- Polls container processing status until ready.
- Publishes the Reel and retrieves the live Instagram permalink.
- Moves the processed video & caption from queue/ to published/ in R2.
- Supports --dry-run mode for safe local testing.
"""

import os
import sys
import time
import datetime
import json
import argparse
import logging
from typing import Optional, Tuple

# Asegurar codificación UTF-8 en consola de Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import re
from urllib.parse import urlparse
import requests
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("reels_publisher")

# -----------------------------------------------------------------------------
# Configuration & Environment Loading
# -----------------------------------------------------------------------------
def load_env():
    """Load variables from .env if present (for local testing)."""
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            logger.debug(f"Loaded environment variables from {env_file}")
        except ImportError:
            # Fallback manual parser
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() not in os.environ:
                            os.environ[k.strip()] = v.strip().strip("\"'")

load_env()

# Cloudflare R2 Settings
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_PUBLIC_DOMAIN = os.getenv("R2_PUBLIC_DOMAIN", "").strip()

# Instagram Graph API Settings
INSTAGRAM_USER_ID = os.getenv("INSTAGRAM_USER_ID")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")

def get_graph_api_base(token: Optional[str] = None) -> str:
    host = os.getenv("GRAPH_API_HOST")
    if host:
        return f"https://{host.replace('https://', '').strip('/')}/{GRAPH_API_VERSION}"
    if token and (token.startswith("IG") or "instagram" in token.lower()):
        return f"https://graph.instagram.com/{GRAPH_API_VERSION}"
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}"

GRAPH_API_BASE = get_graph_api_base(INSTAGRAM_ACCESS_TOKEN)

# YouTube Data API Settings (Shorts)
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")

# Folder prefixes in R2
QUEUE_PREFIX = os.getenv("R2_QUEUE_PREFIX", "queue/")
PUBLISHED_PREFIX = os.getenv("R2_PUBLISHED_PREFIX", "published/")

# -----------------------------------------------------------------------------
# Control de Horarios y Cambio Estacional (España: Península / Madrid)
# -----------------------------------------------------------------------------
def get_spain_datetime(dt_utc: Optional[datetime.datetime] = None) -> Tuple[datetime.datetime, bool]:
    """
    Calcula la fecha/hora en España (Europa/Madrid) y si rige horario de verano (CEST) o invierno (CET).
    Regla oficial de la Unión Europea (Directiva 2000/84/CE):
    - Verano (CEST, UTC+2): Desde el último domingo de marzo (01:00 UTC) hasta el último domingo de octubre (01:00 UTC).
    - Invierno (CET, UTC+1): Resto del año.
    """
    if dt_utc is None:
        dt_utc = datetime.datetime.now(datetime.timezone.utc)
    elif dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=datetime.timezone.utc)

    year = dt_utc.year
    # Último domingo de marzo (01:00 UTC)
    last_sun_mar = 31 - (datetime.date(year, 3, 31).weekday() + 1) % 7
    start_dst = datetime.datetime(year, 3, last_sun_mar, 1, 0, tzinfo=datetime.timezone.utc)

    # Último domingo de octubre (01:00 UTC)
    last_sun_oct = 31 - (datetime.date(year, 10, 31).weekday() + 1) % 7
    end_dst = datetime.datetime(year, 10, last_sun_oct, 1, 0, tzinfo=datetime.timezone.utc)

    is_summer = start_dst <= dt_utc < end_dst
    offset = datetime.timedelta(hours=2 if is_summer else 1)
    spain_dt = dt_utc + offset
    return spain_dt, is_summer


def get_last_published_timestamp(s3_client=None) -> Optional[datetime.datetime]:
    """
    Retorna el datetime UTC del reel más recientemente publicado en R2
    (revisando tanto los objetos en published/ como los registros .status.json en queue/).
    """
    try:
        if s3_client is None:
            s3_client = get_r2_client()
        paginator = s3_client.get_paginator("list_objects_v2")
        latest_dt = None
        valid_extensions = (".mp4", ".mov", ".m4v")

        # 1. Revisar objetos transferidos a published/
        pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=PUBLISHED_PREFIX)
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.lower().endswith(valid_extensions):
                    dt = obj["LastModified"]
                    if latest_dt is None or dt > latest_dt:
                        latest_dt = dt

        # 2. Revisar archivos .status.json en queue/ para detectar publicaciones recientes
        q_pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=QUEUE_PREFIX)
        for page in q_pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.lower().endswith(".status.json"):
                    try:
                        resp = s3_client.get_object(Bucket=R2_BUCKET_NAME, Key=key)
                        data = json.loads(resp["Body"].read().decode("utf-8"))
                        for plat in ("instagram", "facebook", "youtube"):
                            pub_at = data.get(plat, {}).get("published_at")
                            if pub_at:
                                dt = datetime.datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                                if latest_dt is None or dt > latest_dt:
                                    latest_dt = dt
                    except Exception:
                        pass

        return latest_dt
    except Exception as e:
        logger.debug(f"Aviso al consultar timestamp del último reel publicado en R2: {e}")
        return None


def check_publication_schedule(cron_minute: int = 20, min_interval_minutes: int = 90, tolerance_minutes: int = 120) -> Tuple[bool, Optional[int], bool, bool, bool]:
    """
    Comprueba si la hora actual en España corresponde a los horarios programados:
    - Instagram:
      - Verano (CEST, UTC+2): 5 Reels al día -> [07:20, 10:20, 13:20, 16:20, 18:20] España
      - Invierno (CET, UTC+1): 6 Reels al día -> [07:20, 09:20, 12:20, 14:20, 16:20, 18:20] España
    - Facebook (2 Reels al día en horas pico de máxima audiencia en España):
      - Verano: [13:20, 18:20] España
      - Invierno: [14:20, 18:20] España
    - YouTube Shorts (2 Shorts al día en horas pico para maximizar audiencia y cuidar cuota de API):
      - Verano: [13:20, 18:20] España
      - Invierno: [14:20, 18:20] España

    Retorna: (is_valid_slot, matched_spain_hour, is_summer, is_fb_slot, is_yt_slot)
    """
    dt_utc = datetime.datetime.now(datetime.timezone.utc)
    spain_dt, is_summer = get_spain_datetime(dt_utc)

    if is_summer:
        season = "Verano (CEST, UTC+2)"
        target_hours = [7, 10, 13, 16, 18]
        active_utc_hours = [5, 8, 11, 14, 16]
        fb_target_hours = [13, 18]
        yt_target_hours = [13, 18]
        expected_reels = 5
    else:
        season = "Invierno (CET, UTC+1)"
        target_hours = [7, 9, 12, 14, 16, 18]
        active_utc_hours = [6, 8, 11, 13, 15, 17]
        fb_target_hours = [14, 18]
        yt_target_hours = [14, 18]
        expected_reels = 6

    formatted_time = spain_dt.strftime('%Y-%m-%d %H:%M:%S')
    schedule_str = ", ".join([f"{h:02d}:{cron_minute:02d}" for h in target_hours])
    fb_schedule_str = ", ".join([f"{h:02d}:{cron_minute:02d}" for h in fb_target_hours])
    yt_schedule_str = ", ".join([f"{h:02d}:{cron_minute:02d}" for h in yt_target_hours])
    logger.info(f"🕒 Hora en España: {formatted_time} ({season})")
    logger.info(f"📅 Horarios activos Instagram ({expected_reels} Reels/día): [{schedule_str}]")
    logger.info(f"📅 Horarios activos Facebook (2 Reels/día en horas pico): [{fb_schedule_str}]")
    logger.info(f"📅 Horarios activos YouTube Shorts (2 Shorts/día en horas pico): [{yt_schedule_str}]")

    # 1. Cooldown contra ejecuciones repetidas
    last_pub = get_last_published_timestamp()
    if last_pub is not None:
        elapsed = (dt_utc - last_pub).total_seconds() / 60.0
        if elapsed < min_interval_minutes:
            logger.info(f"⏸️ Cooldown activo: La última publicación fue hace {int(elapsed)} min (< {min_interval_minutes} min). Omitiendo.")
            return False, None, is_summer, False, False

    # 2. Tolerancia flexible de disparo
    matched_target = None
    min_diff = 9999
    for uh in active_utc_hours:
        scheduled_utc = dt_utc.replace(hour=uh, minute=cron_minute, second=0, microsecond=0)
        diff_minutes = (dt_utc - scheduled_utc).total_seconds() / 60.0
        if -15 <= diff_minutes <= tolerance_minutes:
            matched_target = uh
            min_diff = diff_minutes
            break

    if matched_target is not None:
        target_spain = matched_target + (2 if is_summer else 1)
        is_fb_slot = target_spain in fb_target_hours
        is_yt_slot = target_spain in yt_target_hours
        status_parts = ["Instagram"]
        if is_fb_slot:
            status_parts.append("Facebook")
        if is_yt_slot:
            status_parts.append("YouTube Shorts")
        status_txt = " + ".join(status_parts) + " ACTIVOS"
        logger.info(f"✅ Franja válida: Slot {target_spain:02d}:{cron_minute:02d} España (Trigger UTC {matched_target:02d}:{cron_minute:02d}, desfase: {min_diff:.1f}m) -> 🔥 {status_txt}.")
        return True, target_spain, is_summer, is_fb_slot, is_yt_slot
    else:
        logger.info(f"⏸️ La hora actual no coincide con ningún disparador activo de {season}. Finalizando sin cambios.")
        return False, None, is_summer, False, False


def is_scheduled_publication_time(cron_minute: int = 20, min_interval_minutes: int = 90, tolerance_minutes: int = 120) -> bool:
    """Wrapper de retrocompatibilidad para verificar si es hora de publicación."""
    is_valid, _, _, _, _ = check_publication_schedule(cron_minute, min_interval_minutes, tolerance_minutes)
    return is_valid



# -----------------------------------------------------------------------------
# Configuración de Copywriting y SEO
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# Configuración de Copywriting y SEO
# -----------------------------------------------------------------------------
SEO_HOOKS = [
    "🌊 CALAS Y AGUAS CRISTALINAS | Escapadas de 1 día desde Valencia 🏖️✨",
    "☀️ RINCONES PARADISÍACOS | Los mejores destinos desde Valencia 🏖️✨",
    "🎒 ¿QUÉ HACER ESTE FIN DE SEMANA? | Escapadas organizadas desde Valencia 🚌🌿",
    "🏖️ LAS MEJORES CALAS DE LA COMUNIDAD VALENCIANA | Excursiones de 1 día 🌊✨",
    "🌊 RUTAS Y CALAS SECRETAS | Paraísos naturales saliendo desde Valencia 🏖️✨",
    "✨ DESTINOS DE AGUAS TURQUESAS | Viajes en autobús desde Valencia 🏖️💦",
    "🚌 ESCAPADAS DE 1 DÍA EN GRUPO | Salidas cada fin de semana desde Valencia 🎒✨",
    "📍 LUGARES MÁGICOS QUE DEBES CONOCER | Escapadas con Valencia Tribal 🌲🌊",
    "🌊 EL PARAÍSO MEDITERRÁNEO A UN PASO | Desconecta este fin de semana 🏖️☀️",
    "🏞️ AGUAS TERMALES Y NATURALEZA PURA | Excursiones organizadas desde Valencia 💦🌿",
    "📸 RINCONES DE POSTAL EN EL MEDITERRÁNEO | Tours de fin de semana desde Valencia 🚌💙",
    "🏖️ PLAYAS SECRETAS Y ACANTILADOS | Descubre la costa valenciana y alicantina 🌊✨",
]

DESTINATION_CONFIG = {
    "ALICANTE": {
        "title": "Excursión a las Mejores Calas de Alicante desde Valencia | Valencia Tribal",
        "hashtags": "#calasdealicante #costablanca #turismoalicante #alicantegram #planesalicante #escapadasalicante",
        "url": "https://valenciatribal.com/todos-los-tours/",
        "tagline": "Calas vírgenes, acantilados de aguas cristalinas y pueblos costeros con encanto a un paso de casa.",
    },
    "BENIDORM": {
        "title": "Excursión a Benidorm de 1 Día desde Valencia | Miradores y Playas - Valencia Tribal",
        "hashtags": "#benidorm #visitbenidorm #turismobenidorm #planesbenidorm #costablanca #miradordebenidorm #benidormfans",
        "url": "https://valenciatribal.com/todos-los-tours/",
        "tagline": "El skyline del Mediterráneo, playas de arena dorada y el famoso Balcón del Mediterráneo a un paso de casa.",
    },
    "MONTANEJOS": {
        "title": "Excursión a Montanejos y Aguas Termales desde Valencia | Valencia Tribal",
        "hashtags": "#montanejos #fuentedelosbaños #aguastermales #turismocastellon #senderismocastellon #altomijares",
        "url": "https://valenciatribal.com/product/excursion-montanejos-termal-taller-de-bachata/",
        "tagline": "Piscinas termales naturales a 25°C todo el año, cañones fluviales y naturaleza viva a un paso de casa.",
    },
    "MASCLETA": {
        "title": "Pólvora, Tradición y Emoción en Valencia | Fiestas y Planes - Valencia Tribal",
        "hashtags": "#valencia #valenciagram #turismovalencia #fallas #fallasvalencia #mascleta #planesvalencia",
        "url": "https://valenciatribal.com/todos-los-tours/",
        "tagline": "La emoción y la esencia de las grandes tradiciones valencianas en su máxima expresión.",
    },
}

GENERAL_CONFIG = {
    "title": "Excursiones Organizadas de 1 Día desde Valencia | Valencia Tribal",
    "hashtags": "#comunidadvalenciana #turismoruralvalencia #planesvalencia",
    "url": "https://valenciatribal.com/todos-los-tours/",
    "tagline": "Rincones únicos, calas de ensueño, aguas termales y pueblos con encanto a un paso de casa.",
}

COMMON_SEO_HASHTAGS = "#excursionesvalencia #escapadasvalencia #valenciatribal #quehacerenvalencia #senderismovalencia #planesvalencia #viajesvalencia"


def get_destination_key(video_key: str) -> str:
    key_upper = video_key.upper()
    for dest in DESTINATION_CONFIG:
        if dest in key_upper:
            return dest
    return "GENERAL"


def resolve_location(video_key: str) -> str:
    """
    Extracts the location name directly from the video filename,
    removing sequence numbers (e.g. 'ALICANTE_008.mp4' -> 'Alicante').
    """
    filename = os.path.basename(video_key)
    base = os.path.splitext(filename)[0]
    # Remove digits and sequence separators (_008, -008, etc.)
    clean_name = re.sub(r"[\d_-]+", " ", base).strip()

    if not clean_name:
        clean_name = "Alicante"

    return f"{clean_name.title()} 🏖️"


def build_seo_title(video_key: str) -> str:
    """Generates an SEO-keyword rich title for Facebook Reels indexing."""
    dest_key = get_destination_key(video_key)
    cfg = DESTINATION_CONFIG.get(dest_key, GENERAL_CONFIG)
    location_clean = resolve_location(video_key).replace(" 🏖️", "").strip()
    if dest_key == "GENERAL":
        return f"Excursión a {location_clean} desde Valencia | Escapadas de 1 Día - Valencia Tribal"
    return cfg["title"]


def build_youtube_title(video_key: str) -> str:
    """Generates an SEO-optimized title for YouTube Shorts (<= 100 characters) including #Shorts."""
    raw = build_seo_title(video_key)
    if "#Shorts" not in raw and "#shorts" not in raw:
        candidate = f"{raw} #Shorts"
    else:
        candidate = raw
    if len(candidate) > 100:
        candidate = candidate[:91].rstrip() + " | #Shorts"
    return candidate


def build_caption(video_key: str, platform: str = "instagram", custom_text: Optional[str] = None) -> str:
    """Generates the high-converting caption with SEO hook, location, semantic body and platform CTA."""
    if custom_text and custom_text.strip():
        txt = custom_text.strip()
        if platform in ("facebook", "youtube") and "valenciatribal.com" not in txt:
            txt = f"{txt}\n\n👉 Info y reservas: https://valenciatribal.com"
        if "+34603225337" not in txt:
            txt = f"{txt}\n📲 Reservar por WhatsApp: https://wa.me/34603225337 (+34 603 22 53 37)"
        if platform == "youtube" and "#Shorts" not in txt and "#shorts" not in txt:
            txt = f"{txt}\n\n#Shorts #YouTubeShorts #ValenciaTribal"
        return txt

    num_match = re.search(r"\d+", os.path.basename(video_key))
    num = int(num_match.group()) if num_match else abs(hash(video_key))

    hook = SEO_HOOKS[num % len(SEO_HOOKS)]
    location = resolve_location(video_key)
    dest_key = get_destination_key(video_key)
    cfg = DESTINATION_CONFIG.get(dest_key, GENERAL_CONFIG)

    body = (
        f"{cfg['tagline']}\n\n"
        "🚌 Viajes organizados de 1 día en autobús privado ida y vuelta desde Valencia\n"
        "🏖️ Salidas en grupo cada sábado y domingo con la mejor energía\n"
        "🎫 Plazas reducidas y seguras para garantizar la mejor experiencia\n"
        "📍 Seguro de viaje y coordinación incluida en todas las salidas"
    )

    if platform == "facebook":
        cta = (
            f"👉 Consulta todas las próximas fechas y asegura tu plaza online antes de que se agoten en:\n"
            f"🔗 {cfg['url']}\n\n"
            "📲 Atención personalizada y reservas por WhatsApp: https://wa.me/34603225337 (+34 603 22 53 37)"
        )
    elif platform == "youtube":
        cta = (
            f"👉 Consulta todas las próximas fechas y asegura tu plaza online antes de que se agoten en:\n"
            f"🔗 {cfg['url']}\n\n"
            "📲 Atención personalizada y reservas por WhatsApp: https://wa.me/34603225337 (+34 603 22 53 37)\n"
            "🔔 ¡Suscríbete al canal para descubrir más excursiones y escapadas desde Valencia!"
        )
    else:
        cta = (
            "👉 Consulta fechas y reserva tu plaza antes de que se agoten en valenciatribal.com (enlace directo en la bio @valenciatribal)\n"
            "👉 Síguenos en @valenciatribal para descubrir más escapadas cada semana 🔔\n\n"
            "📲 Dudas y reservas directas por WhatsApp: +34 603 22 53 37"
        )

    all_hashtags = f"{cfg['hashtags']} {COMMON_SEO_HASHTAGS}"
    if platform == "youtube":
        all_hashtags = f"#Shorts #YouTubeShorts {all_hashtags}"

    return f"{hook}\n📍 Lugar del video: {location}\n\n{body}\n\n{cta}\n\n{all_hashtags}"




# -----------------------------------------------------------------------------
# Cloudflare R2 Client Factory
# -----------------------------------------------------------------------------
def get_r2_client():
    """Initializes and returns a boto3 S3 client configured for Cloudflare R2."""
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]):
        missing = [
            var for var, val in [
                ("R2_ACCOUNT_ID", R2_ACCOUNT_ID),
                ("R2_ACCESS_KEY_ID", R2_ACCESS_KEY_ID),
                ("R2_SECRET_ACCESS_KEY", R2_SECRET_ACCESS_KEY),
                ("R2_BUCKET_NAME", R2_BUCKET_NAME),
            ] if not val
        ]
        raise ValueError(f"Faltan variables de entorno requeridas para R2: {', '.join(missing)}")

    endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"}
        ),
    )
    return s3_client


# -----------------------------------------------------------------------------
# Queue Management & State Tracking (Cloudflare R2)
# -----------------------------------------------------------------------------
def get_video_status(s3_client, video_key: str) -> dict:
    """Lee el estado de publicación del video desde su archivo .status.json en R2."""
    base_no_ext, _ = os.path.splitext(video_key)
    status_key = f"{base_no_ext}.status.json"
    default_status = {
        "instagram": {"published": False, "published_at": None, "media_id": None, "permalink": None},
        "facebook": {"published": False, "published_at": None, "video_id": None, "permalink": None},
        "youtube": {"published": False, "published_at": None, "video_id": None, "permalink": None},
    }
    try:
        resp = s3_client.get_object(Bucket=R2_BUCKET_NAME, Key=status_key)
        data = json.loads(resp["Body"].read().decode("utf-8"))
        if not isinstance(data, dict):
            return default_status
        if "instagram" not in data or not isinstance(data["instagram"], dict):
            data["instagram"] = default_status["instagram"]
        if "facebook" not in data or not isinstance(data["facebook"], dict):
            data["facebook"] = default_status["facebook"]
        if "youtube" not in data or not isinstance(data["youtube"], dict):
            data["youtube"] = default_status["youtube"]
        return data
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return default_status
        logger.warning(f"Aviso al leer estado de {status_key}: {e}")
        return default_status
    except Exception as e:
        logger.warning(f"Aviso al parsear JSON de {status_key}: {e}")
        return default_status


def save_video_status(s3_client, video_key: str, status_data: dict) -> None:
    """Guarda el estado de publicación del video como archivo .status.json en R2."""
    base_no_ext, _ = os.path.splitext(video_key)
    status_key = f"{base_no_ext}.status.json"
    try:
        s3_client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=status_key,
            Body=json.dumps(status_data, indent=2, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        logger.debug(f"Estado de publicación actualizado en R2: {status_key}")
    except Exception as e:
        logger.error(f"Error al guardar estado de publicación en {status_key}: {e}")


def is_video_ready_to_archive(status_data: dict, require_facebook: bool = True, require_youtube: bool = False) -> bool:
    """
    Determina si un video ya fue publicado en todas las plataformas requeridas
    (Instagram, y opcionalmente Facebook y YouTube si están configuradas).
    """
    ig_done = status_data.get("instagram", {}).get("published", False)
    fb_done = status_data.get("facebook", {}).get("published", False) if require_facebook else True
    yt_done = status_data.get("youtube", {}).get("published", False) if require_youtube else True
    return bool(ig_done and fb_done and yt_done)


def get_next_video_from_queue(s3_client, platform: str = "instagram") -> Optional[Tuple[str, Optional[str], dict]]:
    """
    Busca en R2 el siguiente video pendiente de publicación para la plataforma indicada ('instagram' o 'facebook').
    Retorna (video_key, custom_caption, status_dict) o None si no hay videos pendientes para esa plataforma.
    """
    logger.info(f"Buscando videos pendientes para [{platform.upper()}] en '{R2_BUCKET_NAME}/{QUEUE_PREFIX}'...")
    
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=QUEUE_PREFIX)

    video_keys = []
    valid_extensions = (".mp4", ".mov", ".m4v")

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(valid_extensions) and not key.endswith("/"):
                video_keys.append(key)

    # Si no se encontraron videos bajo el prefijo 'queue/' o el prefijo es vacío, buscar en la raíz del bucket
    if not video_keys:
        logger.debug("Buscando videos en la raíz del bucket...")
        pages_root = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix="")
        for page in pages_root:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Ignorar cualquier archivo dentro de published/ o subcarpetas de archivo
                if key.startswith(PUBLISHED_PREFIX) or key.startswith("published/"):
                    continue
                if key.lower().endswith(valid_extensions) and not key.endswith("/"):
                    video_keys.append(key)

    if not video_keys:
        logger.info(f"ℹ️ No hay archivos de video disponibles en el bucket '{R2_BUCKET_NAME}'.")
        return None

    # Orden cronológico/alfabético
    video_keys.sort()

    for chosen_video in video_keys:
        status = get_video_status(s3_client, chosen_video)
        if status.get(platform, {}).get("published", False):
            continue

        logger.info(f"🎯 Próximo video a publicar seleccionado para [{platform.upper()}]: {chosen_video}")

        # Buscar caption personalizado (.txt)
        base_no_ext, _ = os.path.splitext(chosen_video)
        caption_txt_key = f"{base_no_ext}.txt"
        custom_caption = None

        try:
            txt_obj = s3_client.get_object(Bucket=R2_BUCKET_NAME, Key=caption_txt_key)
            caption_content = txt_obj["Body"].read().decode("utf-8").strip()
            if caption_content:
                custom_caption = caption_content
                logger.info(f"📝 Se encontró pie de foto personalizado en: {caption_txt_key}")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                logger.info("ℹ️ Sin archivo .txt personalizado. Se aplicará plantilla de alta conversión.")
            else:
                logger.warning(f"Aviso al buscar {caption_txt_key}: {e}.")

        return chosen_video, custom_caption, status

    logger.info(f"ℹ️ Todos los videos en cola ya fueron publicados en [{platform.upper()}].")
    return None


def get_public_video_url(s3_client, video_key: str) -> str:
    """
    Resolves a public HTTPS URL accessible by Meta crawlers.
    Uses R2_PUBLIC_DOMAIN if configured, or generates a presigned S3 URL.
    """
    if R2_PUBLIC_DOMAIN:
        domain = R2_PUBLIC_DOMAIN
        if not domain.startswith("http://") and not domain.startswith("https://"):
            domain = f"https://{domain}"
        domain = domain.rstrip("/")
        # If public domain points directly to bucket root:
        clean_key = video_key.lstrip("/")
        url = f"{domain}/{clean_key}"
        logger.info(f"🔗 URL pública de video (vía dominio R2): {url}")
        return url

    # Generate Presigned URL (1 hour expiry)
    logger.info("Generando URL prefirmada (Presigned URL) de S3 con 1 hora de validez...")
    presigned_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET_NAME, "Key": video_key},
        ExpiresIn=3600,
    )
    logger.info(f"🔗 URL prefirmada generada exitosamente.")
    return presigned_url


def move_video_to_published(s3_client, video_key: str):
    """Mueve video, companion .txt y companion .status.json de queue/ a published/ en R2."""
    filename = os.path.basename(video_key)
    dest_video_key = f"{PUBLISHED_PREFIX.rstrip('/')}/{filename}"

    logger.info(f"📦 Moviendo {video_key} -> {dest_video_key}...")
    
    # 1. Copiar y eliminar video
    s3_client.copy_object(
        Bucket=R2_BUCKET_NAME,
        CopySource={"Bucket": R2_BUCKET_NAME, "Key": video_key},
        Key=dest_video_key,
    )
    s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=video_key)

    base_no_ext, _ = os.path.splitext(video_key)

    # 2. Copiar y eliminar companion .txt si existe
    txt_key = f"{base_no_ext}.txt"
    dest_txt_key = f"{PUBLISHED_PREFIX.rstrip('/')}/{os.path.basename(txt_key)}"
    try:
        s3_client.head_object(Bucket=R2_BUCKET_NAME, Key=txt_key)
        s3_client.copy_object(
            Bucket=R2_BUCKET_NAME,
            CopySource={"Bucket": R2_BUCKET_NAME, "Key": txt_key},
            Key=dest_txt_key,
        )
        s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=txt_key)
        logger.info(f"📦 Moviendo archivo de texto {txt_key} -> {dest_txt_key}...")
    except ClientError:
        pass  # No txt file existed

    # 3. Copiar y eliminar companion .status.json si existe
    status_key = f"{base_no_ext}.status.json"
    dest_status_key = f"{PUBLISHED_PREFIX.rstrip('/')}/{os.path.basename(status_key)}"
    try:
        s3_client.head_object(Bucket=R2_BUCKET_NAME, Key=status_key)
        s3_client.copy_object(
            Bucket=R2_BUCKET_NAME,
            CopySource={"Bucket": R2_BUCKET_NAME, "Key": status_key},
            Key=dest_status_key,
        )
        s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=status_key)
        logger.info(f"📦 Moviendo archivo de estado {status_key} -> {dest_status_key}...")
    except ClientError:
        pass

    logger.info(f"✅ Archivos de {filename} transferidos completamente a '{PUBLISHED_PREFIX}'.")


def check_and_archive_fully_published(s3_client, video_key: str, require_facebook: bool = True, require_youtube: bool = False) -> bool:
    """Verifica si el video está completamente publicado en todas las plataformas requeridas y lo archiva en published/ si corresponde."""
    status = get_video_status(s3_client, video_key)
    if is_video_ready_to_archive(status, require_facebook=require_facebook, require_youtube=require_youtube):
        move_video_to_published(s3_client, video_key)
        logger.info(f"🎉 ¡{video_key} completó su ciclo en todas las plataformas requeridas y se archivó en '{PUBLISHED_PREFIX}'!")
        return True
    else:
        parts = []
        ig_status = "✅ Sí" if status.get("instagram", {}).get("published") else "⏳ Pendiente"
        parts.append(f"Instagram: {ig_status}")
        if require_facebook:
            fb_status = "✅ Sí" if status.get("facebook", {}).get("published") else "⏳ Pendiente"
            parts.append(f"Facebook: {fb_status}")
        if require_youtube:
            yt_status = "✅ Sí" if status.get("youtube", {}).get("published") else "⏳ Pendiente"
            parts.append(f"YouTube: {yt_status}")
        logger.info(f"📌 {video_key} permanece en '{QUEUE_PREFIX}' ({' | '.join(parts)}).")
        return False


# -----------------------------------------------------------------------------
# Instagram Graph API Logic
# -----------------------------------------------------------------------------
def get_effective_user_id() -> str:
    """Returns the effective Instagram User ID, querying /me if needed."""
    if INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCESS_TOKEN.startswith("IG"):
        try:
            resp = requests.get(
                f"{GRAPH_API_BASE}/me",
                params={"fields": "id", "access_token": INSTAGRAM_ACCESS_TOKEN},
                timeout=10,
            )
            if resp.status_code == 200 and "id" in resp.json():
                return resp.json()["id"]
        except Exception:
            pass
    return INSTAGRAM_USER_ID or "me"


def check_instagram_credentials():
    """Validates Instagram environment variables."""
    if not INSTAGRAM_ACCESS_TOKEN:
        raise ValueError("Falta la variable de entorno requerida: INSTAGRAM_ACCESS_TOKEN.")


def create_reels_container(video_url: str, caption: str) -> str:
    """
    Step 1: Initializes the Reels container in Instagram Graph API.
    POST /{ig_user_id}/media
    """
    check_instagram_credentials()
    user_id = get_effective_user_id()
    url = f"{GRAPH_API_BASE}/{user_id}/media"
    logger.info(f"Usando ID de cuenta de Instagram: {user_id}")

    
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": INSTAGRAM_ACCESS_TOKEN,
    }

    logger.info("🚀 [Paso 1/3] Creando contenedor de Reel en Meta Graph API...")
    response = requests.post(url, data=payload, timeout=30)
    data = response.json()

    if response.status_code != 200 or "id" not in data:
        error_msg = data.get("error", {}).get("message", response.text)
        error_type = data.get("error", {}).get("type", "")
        error_code = data.get("error", {}).get("code", "")
        raise RuntimeError(
            f"Error al crear contenedor de Reel en Instagram: {error_msg} (Tipo: {error_type}, Código: {error_code})"
        )

    container_id = data["id"]
    logger.info(f"✅ Contenedor de Reel creado con ID: {container_id}")
    return container_id


def wait_for_container_processing(container_id: str, timeout_seconds: int = 600) -> None:
    """
    Step 2: Polls Instagram container status until FINISHED or ERROR.
    GET /{container_id}?fields=status_code,status
    """
    logger.info("⏳ [Paso 2/3] Esperando a que Meta descargue y procese el video...")
    url = f"{GRAPH_API_BASE}/{container_id}"
    params = {
        "fields": "status_code,status",
        "access_token": INSTAGRAM_ACCESS_TOKEN,
    }

    start_time = time.time()
    poll_interval = 7  # seconds between status checks

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise TimeoutError(f"Se agotó el tiempo de espera ({timeout_seconds}s) procesando el video {container_id}.")

        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        if response.status_code != 200:
            error_msg = data.get("error", {}).get("message", response.text)
            raise RuntimeError(f"Error consultando estado del contenedor: {error_msg}")

        status_code = data.get("status_code")
        logger.info(f"   Estado actual del procesamiento: {status_code} ({int(elapsed)}s transcurridos)")

        if status_code == "FINISHED":
            logger.info("🎉 ¡Video procesado exitosamente por Meta y listo para publicación!")
            return
        elif status_code == "ERROR":
            error_details = data.get("status", "Sin detalles de error")
            raise RuntimeError(f"Meta reportó un fallo procesando el video: {error_details}")
        elif status_code == "EXPIRED":
            raise RuntimeError(f"El contenedor de video expiró antes de poder ser publicado.")
        elif status_code in ("IN_PROGRESS", None):
            time.sleep(poll_interval)
        else:
            logger.warning(f"Estado no reconocido '{status_code}'. Esperando...")
            time.sleep(poll_interval)


def publish_reels_container(container_id: str) -> Tuple[str, str]:
    """
    Step 3: Publishes the ready container.
    POST /{ig_user_id}/media_publish
    Returns (media_id, permalink)
    """
    user_id = get_effective_user_id()
    url = f"{GRAPH_API_BASE}/{user_id}/media_publish"
    payload = {
        "creation_id": container_id,
        "access_token": INSTAGRAM_ACCESS_TOKEN,
    }

    logger.info("🚀 [Paso 3/3] Publicando Reel en el feed de Instagram...")
    response = requests.post(url, data=payload, timeout=30)
    data = response.json()

    if response.status_code != 200 or "id" not in data:
        error_msg = data.get("error", {}).get("message", response.text)
        raise RuntimeError(f"Error al publicar el Reel en Instagram: {error_msg}")

    media_id = data["id"]
    logger.info(f"🌟 ¡Reel publicado exitosamente! Media ID: {media_id}")

    # Fetch permalink
    permalink = ""
    try:
        info_url = f"{GRAPH_API_BASE}/{media_id}"
        info_resp = requests.get(
            info_url,
            params={"fields": "permalink,shortcode", "access_token": INSTAGRAM_ACCESS_TOKEN},
            timeout=15,
        )
        if info_resp.status_code == 200:
            permalink = info_resp.json().get("permalink", "")
            if permalink:
                logger.info(f"🔗 Enlace directo al Reel: {permalink}")
    except Exception as e:
        logger.warning(f"No se pudo obtener el permalink: {e}")

    return media_id, permalink


# -----------------------------------------------------------------------------
# Facebook Pages Reels Logic (Publicación Simultánea)
# -----------------------------------------------------------------------------
def publish_facebook_reel(video_url: str, caption: str, title: Optional[str] = None) -> Optional[str]:
    """Publishes a Reel directly to the Facebook Page using the Facebook Reels API with SEO title and caption."""
    fb_page_id = os.getenv("FACEBOOK_PAGE_ID")
    fb_page_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")

    if not fb_page_id or not fb_page_token:
        logger.info("ℹ️ Publicación simultánea en Facebook Page no configurada. Se omite.")
        return None

    logger.info("🚀 [Facebook] Iniciando publicación de Reel en la Página de Facebook...")
    init_url = f"https://graph.facebook.com/v21.0/{fb_page_id}/video_reels"

    try:
        # Step 1: Start upload session
        init_resp = requests.post(
            init_url,
            data={"upload_phase": "start", "access_token": fb_page_token},
            timeout=30,
        )
        init_data = init_resp.json()
        if init_resp.status_code != 200 or "video_id" not in init_data:
            err = init_data.get("error", {}).get("message", init_resp.text)
            logger.error(f"❌ [Facebook] Error al iniciar sesión de Reel: {err}")
            return None

        video_id = init_data["video_id"]
        upload_url = init_data.get("upload_url", f"https://rupload.facebook.com/video-upload/{video_id}")
        logger.info(f"✅ [Facebook] Sesión iniciada con Video ID: {video_id}")

        # Step 2: Transfer video via R2 public/presigned URL (with binary stream fallback)
        logger.info("⏳ [Facebook] Transfiriendo video a los servidores de Facebook...")
        upload_headers = {
            "Authorization": f"OAuth {fb_page_token}",
            "file_url": video_url,
        }
        upload_resp = requests.post(upload_url, headers=upload_headers, timeout=60)
        if upload_resp.status_code not in (200, 201):
            logger.info("ℹ️ [Facebook] file_url no aceptado directamente, transfiriendo stream binario...")
            v_get = requests.get(video_url, stream=True, timeout=30)
            file_size = v_get.headers.get("content-length", "")
            binary_headers = {
                "Authorization": f"OAuth {fb_page_token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream",
            }
            upload_resp = requests.post(
                upload_url,
                headers=binary_headers,
                data=v_get.iter_content(chunk_size=1024 * 1024),
                timeout=120,
            )
            if upload_resp.status_code not in (200, 201):
                logger.error(f"❌ [Facebook] Error en la subida de video: {upload_resp.text}")
                return None

        # Step 3: Publish Reel
        logger.info("🚀 [Facebook] Finalizando y publicando Reel en la Página de Facebook...")
        finish_payload = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption,
            "access_token": fb_page_token,
        }
        if title:
            finish_payload["title"] = title

        finish_resp = requests.post(init_url, data=finish_payload, timeout=30)
        finish_data = finish_resp.json()
        
        fb_permalink = f"https://www.facebook.com/reel/{video_id}"
        if finish_resp.status_code == 200 and finish_data.get("success"):
            logger.info(f"🌟 [Facebook] ¡Reel publicado en la Página de Facebook! URL: {fb_permalink}")
        else:
            logger.warning(f"Aviso en la publicación de Facebook: {finish_data}")

        return fb_permalink

    except Exception as e:
        logger.error(f"❌ [Facebook] Error durante la publicación en Facebook: {e}")
        return None


# -----------------------------------------------------------------------------
# YouTube Shorts Logic (Publicación de Shorts)
# -----------------------------------------------------------------------------
def publish_youtube_short(s3_client, video_key: str, title: str, description: str, tags: Optional[list] = None) -> Optional[str]:
    """Publishes a vertical video as a YouTube Short using YouTube Data API v3."""
    yt_client_id = os.getenv("YOUTUBE_CLIENT_ID")
    yt_client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    yt_refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")

    if not yt_client_id or not yt_client_secret or not yt_refresh_token:
        logger.info("ℹ️ YouTube Shorts no configurado. Se omite.")
        return None

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        logger.error("❌ Librerías de Google API no instaladas. Asegúrate de tener google-api-python-client y google-auth-oauthlib.")
        return None

    logger.info("🚀 [YouTube] Preparando subida de video a YouTube Shorts...")

    creds = Credentials(
        token=None,
        refresh_token=yt_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=yt_client_id,
        client_secret=yt_client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    clean_title = title.strip()
    if "#Shorts" not in clean_title and "#shorts" not in clean_title:
        clean_title = f"{clean_title} #Shorts"
    if len(clean_title) > 100:
        clean_title = clean_title[:91].rstrip() + " | #Shorts"

    clean_desc = description.strip()
    if "#Shorts" not in clean_desc and "#shorts" not in clean_desc:
        clean_desc = f"{clean_desc}\n\n#Shorts #YouTubeShorts"

    if tags is None:
        tags = ["Shorts", "Valencia Tribal", "Valencia", "Excursiones Valencia", "España", "Viajes", "Turismo"]

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        logger.info(f"⏳ [YouTube] Descargando {video_key} desde R2 para subida directa...")
        s3_client.download_file(Bucket=R2_BUCKET_NAME, Key=video_key, Filename=tmp_path)
        file_size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        logger.info(f"✅ [YouTube] Video descargado temporalmente ({file_size_mb:.2f} MB).")

        body = {
            "snippet": {
                "title": clean_title,
                "description": clean_desc,
                "tags": tags,
                "categoryId": "19",  # Travel & Events
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            tmp_path,
            mimetype="video/mp4",
            chunksize=5 * 1024 * 1024,
            resumable=True,
        )

        logger.info("⏳ [YouTube] Transfiriendo archivo a YouTube Data API v3...")
        insert_request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status_upload, response = insert_request.next_chunk()
            if status_upload:
                logger.info(f"   [YouTube] Progreso de subida: {int(status_upload.progress() * 100)}%")

        video_id = response.get("id")
        if not video_id:
            logger.error(f"❌ [YouTube] Respuesta sin video ID: {response}")
            return None

        yt_permalink = f"https://www.youtube.com/shorts/{video_id}"
        logger.info(f"🌟 [YouTube] ¡Short publicado en YouTube! URL: {yt_permalink}")
        return yt_permalink

    except Exception as e:
        logger.error(f"❌ [YouTube] Error durante la publicación en YouTube Shorts: {e}")
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# -----------------------------------------------------------------------------
# Main Execution Pipeline
# -----------------------------------------------------------------------------
def run_automation(dry_run: bool = False, specific_video: Optional[str] = None, check_schedule: bool = False):
    """Main function executing the publication workflow."""
    print("=" * 70)
    print("  VALENCIA TRIBAL - AUTOMATIZACIÓN DE INSTAGRAM, FACEBOOK & YOUTUBE")
    print("=" * 70)

    if dry_run:
        logger.warning("⚠️  MODO DRY-RUN ACTIVADO: No se publicará nada ni se moverán archivos.")

    # 1. Configuración y validación de plataformas
    fb_page_id = os.getenv("FACEBOOK_PAGE_ID")
    fb_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
    fb_configured = bool(fb_page_id and fb_token)

    yt_client_id = os.getenv("YOUTUBE_CLIENT_ID")
    yt_client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    yt_refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    yt_configured = bool(yt_client_id and yt_client_secret and yt_refresh_token)

    should_publish_ig = True
    should_publish_fb = fb_configured
    should_publish_yt = yt_configured

    if check_schedule:
        is_valid, spain_hour, is_summer, is_fb_slot, is_yt_slot = check_publication_schedule()
        if not is_valid:
            logger.info("🏁 Finalizando ejecución según control de horarios.")
            return 0
        should_publish_fb = fb_configured and is_fb_slot
        should_publish_yt = yt_configured and is_yt_slot
        if fb_configured and not should_publish_fb:
            logger.info("⏸️ Franja horaria fuera de las 2 horas pico de Facebook. Facebook descansando.")
        if yt_configured and not should_publish_yt:
            logger.info("⏸️ Franja horaria fuera de las 2 horas pico de YouTube Shorts. YouTube descansando.")

    # 2. Inicializar cliente R2
    s3 = get_r2_client()

    processed_videos = set()

    # --- MODO VIDEO ESPECÍFICO ---
    if specific_video:
        video_key = specific_video
        video_url = get_public_video_url(s3, video_key)
        status = get_video_status(s3, video_key)
        custom_caption = None
        base_no_ext, _ = os.path.splitext(video_key)
        try:
            txt_obj = s3.get_object(Bucket=R2_BUCKET_NAME, Key=f"{base_no_ext}.txt")
            custom_caption = txt_obj["Body"].read().decode("utf-8").strip()
        except Exception:
            pass

        # Instagram
        if should_publish_ig and not status.get("instagram", {}).get("published"):
            caption_ig = build_caption(video_key, platform="instagram", custom_text=custom_caption)
            if dry_run:
                logger.info(f"🔎 [DRY-RUN] Publicaría en Instagram: {video_key}")
            else:
                container_id = create_reels_container(video_url, caption_ig)
                wait_for_container_processing(container_id)
                media_id, ig_permalink = publish_reels_container(container_id)
                status["instagram"] = {
                    "published": True,
                    "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "media_id": media_id,
                    "permalink": ig_permalink,
                }
                save_video_status(s3, video_key, status)
                processed_videos.add(video_key)

        # Facebook
        if should_publish_fb and not status.get("facebook", {}).get("published"):
            caption_fb = build_caption(video_key, platform="facebook", custom_text=custom_caption)
            seo_title = build_seo_title(video_key)
            if dry_run:
                logger.info(f"🔎 [DRY-RUN] Publicaría en Facebook: {video_key} (Título: {seo_title})")
            else:
                fb_permalink = publish_facebook_reel(video_url, caption_fb, title=seo_title)
                if fb_permalink:
                    status["facebook"] = {
                        "published": True,
                        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "permalink": fb_permalink,
                    }
                    save_video_status(s3, video_key, status)
                    processed_videos.add(video_key)

        # YouTube
        if should_publish_yt and not status.get("youtube", {}).get("published"):
            caption_yt = build_caption(video_key, platform="youtube", custom_text=custom_caption)
            yt_title = build_youtube_title(video_key)
            if dry_run:
                logger.info(f"🔎 [DRY-RUN] Publicaría en YouTube Shorts: {video_key} (Título: {yt_title})")
            else:
                yt_permalink = publish_youtube_short(s3, video_key, title=yt_title, description=caption_yt)
                if yt_permalink:
                    status["youtube"] = {
                        "published": True,
                        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "permalink": yt_permalink,
                    }
                    save_video_status(s3, video_key, status)
                    processed_videos.add(video_key)

        if not dry_run:
            check_and_archive_fully_published(s3, video_key, require_facebook=fb_configured, require_youtube=yt_configured)
        return 0

    # --- MODO AUTOMÁTICO DESDE COLA ---

    # [Paso 1/3] Publicación en Instagram
    ig_video_key = None
    if should_publish_ig:
        logger.info("--- [1/3] PROCESANDO COLA DE INSTAGRAM ---")
        ig_item = get_next_video_from_queue(s3, platform="instagram")
        if ig_item:
            ig_video_key, ig_custom_caption, ig_status = ig_item
            ig_url = get_public_video_url(s3, ig_video_key)
            ig_caption = build_caption(ig_video_key, platform="instagram", custom_text=ig_custom_caption)

            print("-" * 70)
            print(f"📷 VISTA PREVIA INSTAGRAM REEL [{ig_video_key}]:")
            print(ig_caption[:250] + ("..." if len(ig_caption) > 250 else ""))
            print("-" * 70)

            if dry_run:
                logger.info(f"🔎 [DRY-RUN] Instagram publicaría: {ig_video_key} (URL: {ig_url[:60]}...)")
            else:
                container_id = create_reels_container(ig_url, ig_caption)
                wait_for_container_processing(container_id)
                media_id, ig_permalink = publish_reels_container(container_id)
                ig_status["instagram"] = {
                    "published": True,
                    "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "media_id": media_id,
                    "permalink": ig_permalink,
                }
                save_video_status(s3, ig_video_key, ig_status)
                processed_videos.add(ig_video_key)
                logger.info(f"✅ Estado de Instagram guardado para {ig_video_key}.")
        else:
            logger.info("ℹ️ No hay videos pendientes de publicación en Instagram.")

    # [Paso 2/3] Publicación en Facebook (solo en franjas pico)
    fb_video_key = None
    if should_publish_fb:
        logger.info("--- [2/3] PROCESANDO COLA DE FACEBOOK (HORA PICO) ---")
        fb_item = get_next_video_from_queue(s3, platform="facebook")
        if fb_item:
            fb_video_key, fb_custom_caption, _ = fb_item
            # Recargar estado fresco por si fb_video_key == ig_video_key
            fb_status = get_video_status(s3, fb_video_key)
            fb_url = get_public_video_url(s3, fb_video_key)
            fb_caption = build_caption(fb_video_key, platform="facebook", custom_text=fb_custom_caption)
            fb_title = build_seo_title(fb_video_key)

            print("-" * 70)
            print(f"🔥 VISTA PREVIA FACEBOOK REEL [{fb_video_key}]:")
            print(f"🏷️ Título SEO: {fb_title}")
            print(fb_caption[:250] + ("..." if len(fb_caption) > 250 else ""))
            print("-" * 70)

            if dry_run:
                logger.info(f"🔎 [DRY-RUN] Facebook publicaría: {fb_video_key} (Título: {fb_title})")
            else:
                fb_permalink = publish_facebook_reel(fb_url, fb_caption, title=fb_title)
                if fb_permalink:
                    fb_status["facebook"] = {
                        "published": True,
                        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "permalink": fb_permalink,
                    }
                    save_video_status(s3, fb_video_key, fb_status)
                    processed_videos.add(fb_video_key)
                    logger.info(f"✅ Estado de Facebook guardado para {fb_video_key}.")
        else:
            logger.info("ℹ️ No hay videos pendientes de publicación en Facebook.")
    else:
        if fb_configured:
            logger.info("⏸️ Facebook descansando en esta franja (programado solo para 2 horas pico al día: 13:20 / 14:20 y 18:20).")

    # [Paso 3/3] Publicación en YouTube Shorts (solo en franjas pico)
    yt_video_key = None
    if should_publish_yt:
        logger.info("--- [3/3] PROCESANDO COLA DE YOUTUBE SHORTS (HORA PICO) ---")
        yt_item = get_next_video_from_queue(s3, platform="youtube")
        if yt_item:
            yt_video_key, yt_custom_caption, _ = yt_item
            # Recargar estado fresco
            yt_status = get_video_status(s3, yt_video_key)
            yt_caption = build_caption(yt_video_key, platform="youtube", custom_text=yt_custom_caption)
            yt_title = build_youtube_title(yt_video_key)

            print("-" * 70)
            print(f"🎬 VISTA PREVIA YOUTUBE SHORT [{yt_video_key}]:")
            print(f"🏷️ Título: {yt_title}")
            print(yt_caption[:250] + ("..." if len(yt_caption) > 250 else ""))
            print("-" * 70)

            if dry_run:
                logger.info(f"🔎 [DRY-RUN] YouTube Shorts publicaría: {yt_video_key} (Título: {yt_title})")
            else:
                yt_permalink = publish_youtube_short(s3, yt_video_key, title=yt_title, description=yt_caption)
                if yt_permalink:
                    yt_status["youtube"] = {
                        "published": True,
                        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "permalink": yt_permalink,
                    }
                    save_video_status(s3, yt_video_key, yt_status)
                    processed_videos.add(yt_video_key)
                    logger.info(f"✅ Estado de YouTube guardado para {yt_video_key}.")
        else:
            logger.info("ℹ️ No hay videos pendientes de publicación en YouTube Shorts.")
    else:
        if yt_configured:
            logger.info("⏸️ YouTube Shorts descansando en esta franja (programado solo para 2 horas pico al día).")

    # [Paso 4] Comprobación de archivo a published/
    # Un video solo se archiva si se ha publicado en TODAS las plataformas requeridas
    if not dry_run:
        for vkey in processed_videos:
            check_and_archive_fully_published(s3, vkey, require_facebook=fb_configured, require_youtube=yt_configured)
    else:
        logger.info("🔎 [DRY-RUN] Simulación finalizada. No se archivó ningún archivo en R2.")

    print("=" * 70)
    print("🎉 EJECUCIÓN COMPLETADA")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publicar Reels en Instagram y Facebook automáticamente desde Cloudflare R2.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula el proceso sin publicar en redes ni mover archivos en R2.",
    )
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="Especifica la clave exacta del video en R2 para publicar manualmente (ej: queue/ejemplo.mp4).",
    )
    parser.add_argument(
        "--check-schedule",
        action="store_true",
        help="Verifica si la hora actual en España corresponde al horario programado. Si no, finaliza sin publicar.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Activa logs detallados de depuración.",
    )

    args = parser.parse_args()
    if args.debug:
        logger.setLevel(logging.DEBUG)

    try:
        sys.exit(run_automation(dry_run=args.dry_run, specific_video=args.video, check_schedule=args.check_schedule))
    except Exception as exc:
        logger.error(f"❌ Error durante la ejecución: {exc}", exc_info=args.debug)
        sys.exit(1)
