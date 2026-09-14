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
    """Returns the UTC datetime of the most recently published Reel in R2."""
    try:
        if s3_client is None:
            s3_client = get_r2_client()
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=PUBLISHED_PREFIX)
        latest_dt = None
        valid_extensions = (".mp4", ".mov", ".m4v")
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.lower().endswith(valid_extensions):
                    dt = obj["LastModified"]
                    if latest_dt is None or dt > latest_dt:
                        latest_dt = dt
        return latest_dt
    except Exception as e:
        logger.debug(f"Aviso al consultar timestamp del último reel publicado en R2: {e}")
        return None


def is_scheduled_publication_time(cron_minute: int = 10, min_interval_minutes: int = 90) -> bool:
    """
    Verifica de forma robusta si la hora actual corresponde a los horarios programados:
    - Verano (CEST, UTC+2): 5 Reels al día -> [07:10, 10:10, 13:10, 16:10, 18:10] España
      Disparadores UTC activos: [05:10, 08:10, 11:10, 14:10, 16:10].
    - Invierno (CET, UTC+1): 6 Reels al día -> [07:10, 09:10, 12:10, 14:10, 16:10, 18:10] España
      Disparadores UTC activos: [06:10, 08:10, 11:10, 13:10, 15:10, 17:10].

    Resiliencia anti-retrasos y protección contra duplicados:
    - Permite absorber retrasos de hasta 75 minutos causados por la cola de GitHub Actions.
    - Aplica cooldown de 90 min si ya se publicó un Reel recientemente en R2.
    """
    dt_utc = datetime.datetime.now(datetime.timezone.utc)
    spain_dt, is_summer = get_spain_datetime(dt_utc)

    if is_summer:
        season = "Verano (CEST, UTC+2)"
        target_hours = [7, 10, 13, 16, 18]
        active_utc_hours = [5, 8, 11, 14, 16]
        expected_reels = 5
    else:
        season = "Invierno (CET, UTC+1)"
        target_hours = [7, 9, 12, 14, 16, 18]
        active_utc_hours = [6, 8, 11, 13, 15, 17]
        expected_reels = 6

    formatted_time = spain_dt.strftime('%Y-%m-%d %H:%M:%S')
    schedule_str = ", ".join([f"{h:02d}:10" for h in target_hours])
    logger.info(f"🕒 Hora en España: {formatted_time} ({season})")
    logger.info(f"📅 Horarios activos ({expected_reels} Reels/día): [{schedule_str}]")

    # 1. Protección de Cooldown contra publicaciones repetidas o solapadas
    last_pub = get_last_published_timestamp()
    if last_pub is not None:
        elapsed = (dt_utc - last_pub).total_seconds() / 60.0
        if elapsed < min_interval_minutes:
            logger.info(f"⏸️ Cooldown activo: El último Reel se publicó hace {int(elapsed)} min (< {min_interval_minutes} min). Omitiendo.")
            return False

    # 2. Comprobar si coincide con alguno de los disparadores UTC activos con ventana de tolerancia (-5 min a +75 min)
    matched_target = None
    min_diff = 9999
    for uh in active_utc_hours:
        scheduled_utc = dt_utc.replace(hour=uh, minute=cron_minute, second=0, microsecond=0)
        diff_minutes = (dt_utc - scheduled_utc).total_seconds() / 60.0
        if -5 <= diff_minutes <= 75:
            matched_target = uh
            min_diff = diff_minutes
            break

    if matched_target is not None:
        target_spain = matched_target + (2 if is_summer else 1)
        logger.info(f"✅ Franja válida: Slot {target_spain:02d}:10 España (Trigger UTC {matched_target:02d}:{cron_minute:02d}, desfase: {min_diff:.1f}m).")
        return True
    else:
        logger.info(f"⏸️ La hora actual no coincide con ningún disparador activo de {season}. Finalizando sin cambios.")
        return False



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


def build_caption(video_key: str, platform: str = "instagram", custom_text: Optional[str] = None) -> str:
    """Generates the high-converting caption with SEO hook, location, semantic body and platform CTA."""
    if custom_text and custom_text.strip():
        txt = custom_text.strip()
        if platform == "facebook" and "valenciatribal.com" not in txt:
            txt = f"{txt}\n\n👉 Info y reservas: https://valenciatribal.com"
        if "+34603225337" not in txt:
            txt = f"{txt}\n📲 Reservar por WhatsApp: https://wa.me/34603225337 (+34 603 22 53 37)"
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
    else:
        cta = (
            "👉 Consulta fechas y reserva tu plaza antes de que se agoten en valenciatribal.com (enlace directo en la bio @valenciatribal)\n"
            "👉 Síguenos en @valenciatribal para descubrir más escapadas cada semana 🔔\n\n"
            "📲 Dudas y reservas directas por WhatsApp: +34 603 22 53 37"
        )

    all_hashtags = f"{cfg['hashtags']} {COMMON_SEO_HASHTAGS}"

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
# Queue Management (R2)
# -----------------------------------------------------------------------------
def get_next_video_from_queue(s3_client) -> Optional[Tuple[str, str]]:
    """
    Scans the queue prefix in R2 for the next video (.mp4/.mov).
    Returns (video_key, caption_text) or None if queue is empty.
    """
    logger.info(f"Buscando videos pendientes en '{R2_BUCKET_NAME}/{QUEUE_PREFIX}'...")
    
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
        logger.info(f"ℹ️ No hay videos pendientes en el bucket '{R2_BUCKET_NAME}'.")
        return None

    # Sort alphabetically/chronologically to ensure predictable execution
    video_keys.sort()
    chosen_video = video_keys[0]
    logger.info(f"🎯 Próximo video a publicar seleccionado: {chosen_video}")

    # Look for companion caption file (.txt or .json)
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
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            logger.info("ℹ️ Sin archivo .txt personalizado. Se aplicará plantilla de alta conversión.")
        else:
            logger.warning(f"Aviso al buscar {caption_txt_key}: {e}.")

    return chosen_video, custom_caption


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
    """Moves video and companion caption file from queue/ to published/ in R2."""
    filename = os.path.basename(video_key)
    dest_video_key = f"{PUBLISHED_PREFIX.rstrip('/')}/{filename}"

    logger.info(f"📦 Moviendo {video_key} -> {dest_video_key}...")
    
    # 1. Copy video
    s3_client.copy_object(
        Bucket=R2_BUCKET_NAME,
        CopySource={"Bucket": R2_BUCKET_NAME, "Key": video_key},
        Key=dest_video_key,
    )
    s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=video_key)

    # 2. Check and move companion .txt if it exists
    base_no_ext, _ = os.path.splitext(video_key)
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

    logger.info("✅ Archivos transferidos a la carpeta 'published/' en R2.")


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
# Main Execution Pipeline
# -----------------------------------------------------------------------------
def run_automation(dry_run: bool = False, specific_video: Optional[str] = None):
    """Main function executing the publication workflow."""
    print("=" * 70)
    print("  VALENCIA TRIBAL - AUTOMATIZACIÓN DE INSTAGRAM & FACEBOOK REELS")
    print("=" * 70)

    if dry_run:
        logger.warning("⚠️  MODO DRY-RUN ACTIVADO: No se publicará nada ni se moverán archivos.")

    # 1. Initialize R2
    s3 = get_r2_client()

    # 2. Select Video
    if specific_video:
        video_key = specific_video
        custom_caption = None
        logger.info(f"Usando video específico provisto: {video_key}")
    else:
        queue_item = get_next_video_from_queue(s3)
        if not queue_item:
            logger.info("🏁 No hay videos pendientes para publicar hoy. Finalizando con éxito.")
            return 0
        video_key, custom_caption = queue_item

    # 3. Resolve Public / Presigned URL
    video_url = get_public_video_url(s3, video_key)

    # 4. Generate & Preview Captions & SEO Metadata
    caption_ig = build_caption(video_key, platform="instagram", custom_text=custom_caption)
    caption_fb = build_caption(video_key, platform="facebook", custom_text=custom_caption)
    seo_title = build_seo_title(video_key)

    print("-" * 70)
    print(f"🏷️  TÍTULO SEO DEL REEL (FACEBOOK): {seo_title}")
    print("-" * 70)
    print("📄 VISTA PREVIA DEL PIE DE FOTO (INSTAGRAM):")
    print(caption_ig)
    print("-" * 70)
    print("📄 VISTA PREVIA DEL PIE DE FOTO (FACEBOOK):")
    print(caption_fb)
    print("-" * 70)

    fb_page_id = os.getenv("FACEBOOK_PAGE_ID")
    if dry_run:
        logger.info("🔎 Verificación dry-run exitosa:")
        logger.info(f" - Video R2: {video_key}")
        logger.info(f" - Título SEO (Facebook): {seo_title}")
        logger.info(f" - URL generada: {video_url[:80]}...")
        logger.info(f" - Instagram Account ID: {INSTAGRAM_USER_ID or 'NO CONFIGURADO'}")
        logger.info(f" - Facebook Page ID: {fb_page_id or '(No configurado, solo Instagram)'}")
        logger.info("Dry-run completado sin realizar cambios.")
        return 0

    # 5. Instagram API Flow
    container_id = create_reels_container(video_url, caption_ig)
    wait_for_container_processing(container_id)
    media_id, ig_permalink = publish_reels_container(container_id)

    # 6. Facebook Page Reels Flow (Simultáneo con Título SEO)
    fb_permalink = publish_facebook_reel(video_url, caption_fb, title=seo_title)


    # 7. Post-processing in R2
    move_video_to_published(s3, video_key)

    print("=" * 70)
    print("🎉 PUBLICACIÓN COMPLETADA CON ÉXITO")
    print(f"📌 Instagram Media ID: {media_id}")
    if ig_permalink:
        print(f"🔗 Instagram URL: {ig_permalink}")
    if fb_permalink:
        print(f"🔗 Facebook Reel URL: {fb_permalink}")
    print(f"📁 Video archivado en R2 bajo: {PUBLISHED_PREFIX}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publicar Reels en Instagram automáticamente desde Cloudflare R2.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula el proceso sin publicar en Instagram ni mover archivos en R2.",
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
        help="Verifica si la hora actual en España corresponde al horario programado (5 en verano, 6 en invierno). Si no, finaliza sin publicar.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Activa logs detallados de depuración.",
    )

    args = parser.parse_args()
    if args.debug:
        logger.setLevel(logging.DEBUG)

    if args.check_schedule and not is_scheduled_publication_time():
        sys.exit(0)

    try:
        sys.exit(run_automation(dry_run=args.dry_run, specific_video=args.video))
    except Exception as exc:
        logger.error(f"❌ Error durante la ejecución: {exc}", exc_info=args.debug)
        sys.exit(1)
