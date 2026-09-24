#!/usr/bin/env python3
"""
scripts/publish_blog_to_social.py

Detecta posts nuevos publicados en valenciatribal.com (WordPress) y los
publica automáticamente en la Página de Facebook y la cuenta de Instagram
de Valencia Tribal, usando la Graph API de Meta directamente (sin
Metricool y sin ningún paso manual).

Cómo funciona
-------------
1. Lee docs/blog_social_state.json (lista de post_ids de WordPress ya
   procesados). Si no existe, es la primera ejecución: se listan los
   posts actuales y se marcan todos como "ya vistos" SIN publicarlos en
   redes, para no volcar de golpe todo el histórico del blog la primera
   vez que se activa esta automatización.
2. En cada ejecución siguiente, consulta la API pública de WordPress
   (wp-json/wp/v2/posts) y compara con el estado guardado. Los posts
   nuevos (hasta MAX_POSTS_PER_RUN por ejecución, del más antiguo al más
   nuevo) se procesan.
3. Para cada post nuevo:
   - Compone una imagen vertical 1080x1350 (foto destacada + logo +
     título), igual que el proceso manual habitual, y la sube a
     Cloudflare R2 para tener una URL pública estable.
   - Publica en Facebook (foto + texto, o solo texto si el post no tiene
     imagen destacada).
   - Publica en Instagram (requiere imagen; se omite si el post no tiene
     imagen destacada utilizable).
4. Actualiza docs/blog_social_state.json con el resultado (éxito/fallo
   por red, por post) y el workflow hace commit + push si hubo cambios.

Reutiliza los secrets YA EXISTENTES en este repo — no hace falta crear
ninguno nuevo:
    FACEBOOK_PAGE_ACCESS_TOKEN, FACEBOOK_PAGE_ID,
    INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_USER_ID
        (los mismos que usa publish_social.py / publish_instagram_reel.py)
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
    R2_BUCKET_NAME, R2_PUBLIC_DOMAIN
        (los mismos que usa publish_instagram_reel.py)
"""

import datetime
import html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

import boto3
from botocore.config import Config
from PIL import Image, ImageDraw, ImageFont

# Permite importar publish_facebook / publish_instagram desde
# scripts/publish_social.py sin duplicar la lógica de la Graph API.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_social import publish_facebook, publish_instagram  # noqa: E402

WP_SITE = "https://valenciatribal.com"
WP_POSTS_API = f"{WP_SITE}/wp-json/wp/v2/posts"
LOGO_URL = f"{WP_SITE}/wp-content/uploads/2022/02/logo-valencia-tribal.png"

STATE_PATH = "docs/blog_social_state.json"
MAX_POSTS_PER_RUN = 5
SEED_LOOKBACK = 50  # cuántos posts recientes se consultan / se siembran en la 1ª ejecución

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME") or os.getenv("R2_BUCKET_NAME_BLOG_SOCIAL")
R2_PUBLIC_DOMAIN = os.getenv("R2_PUBLIC_DOMAIN", "").strip()
SOCIAL_IMAGES_PREFIX = os.getenv("R2_SOCIAL_IMAGES_PREFIX", "blog-social/")
PRESIGNED_URL_EXPIRY = 7 * 24 * 3600  # máximo permitido por S3/R2 (7 días)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ACCENT = (181, 89, 31)      # terracota, del logo de Valencia Tribal
BAND_BG = (20, 15, 13)      # negro cálido

USER_AGENT = "Mozilla/5.0 (compatible; ValenciaTribalSocialBot/1.0; +https://valenciatribal.com)"


# --------------------------------------------------------------------------
# Estado (docs/blog_social_state.json)
# --------------------------------------------------------------------------
def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


# --------------------------------------------------------------------------
# WordPress
# --------------------------------------------------------------------------
def fetch_recent_posts(per_page=20):
    url = f"{WP_POSTS_API}?per_page={per_page}&orderby=date&order=desc&status=publish&_embed=1"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def strip_html(raw_html):
    text = re.sub(r"<[^>]+>", "", raw_html or "")
    return html.unescape(text).strip()


def get_featured_image_url(post):
    try:
        media = post.get("_embedded", {}).get("wp:featuredmedia", [])
        if media and media[0].get("source_url"):
            return media[0]["source_url"]
    except Exception:
        pass
    return None


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# --------------------------------------------------------------------------
# Composición de imagen social 1080x1350 (misma plantilla que el proceso
# manual: foto de portada + badge del logo + banda de título)
# --------------------------------------------------------------------------
def _cover_crop(img, target_w, target_h):
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h
    if src_ratio > target_ratio:
        new_h = target_h; new_w = int(new_h * src_ratio)
    else:
        new_w = target_w; new_h = int(new_w / src_ratio)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _wrap_text(draw, text, font, max_width):
    words = text.split(); lines = []; cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines


def _add_logo_badge(canvas, logo, margin, badge_width):
    logo_ratio = logo.size[1] / logo.size[0]
    inner_w = badge_width; inner_h = int(inner_w * logo_ratio)
    logo_resized = logo.resize((inner_w, inner_h), Image.LANCZOS)
    pad = int(badge_width * 0.18)
    badge_w = inner_w + pad * 2; badge_h = inner_h + pad * 2
    badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(badge)
    radius = int(badge_h * 0.22)
    bdraw.rounded_rectangle([0, 0, badge_w - 1, badge_h - 1], radius=radius, fill=(255, 255, 255, 235))
    badge.alpha_composite(logo_resized, (pad, pad))
    canvas.alpha_composite(badge, (margin, margin))


def _add_title_band(canvas, title, canvas_w, canvas_h, font_size, max_lines=4):
    draw_probe = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(FONT_BOLD, font_size)
    pad_x = int(canvas_w * 0.055)
    max_text_width = canvas_w - pad_x * 2
    lines = _wrap_text(draw_probe, title, font, max_text_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while draw_probe.textbbox((0, 0), last + "…", font=font)[2] > max_text_width and len(last) > 0:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    line_height = int(font_size * 1.22)
    text_block_h = line_height * len(lines)
    pad_top = int(canvas_h * 0.035); pad_bottom = int(canvas_h * 0.045)
    accent_h = max(4, int(canvas_h * 0.006)); accent_gap = int(canvas_h * 0.02)
    band_h = pad_top + accent_h + accent_gap + text_block_h + pad_bottom
    gradient_zone_h = int(band_h * 1.9)
    gradient = Image.new("RGBA", (canvas_w, gradient_zone_h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(gradient)
    for i in range(gradient_zone_h):
        t = i / gradient_zone_h
        alpha = int(0 + 235 * t)
        gdraw.line([(0, i), (canvas_w, i)], fill=(*BAND_BG, alpha))
    canvas.alpha_composite(gradient, (0, canvas_h - gradient_zone_h))
    accent_y = canvas_h - band_h + pad_top
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([pad_x, accent_y, pad_x + int(canvas_w * 0.12), accent_y + accent_h], fill=ACCENT)
    ty = accent_y + accent_h + accent_gap
    for line in lines:
        draw.text((pad_x, ty), line, font=font, fill=(255, 255, 255, 255))
        ty += line_height


def compose_social_image(photo_bytes, logo_bytes, title):
    W, H = 1080, 1350  # vertical 4:5, misma imagen para Facebook e Instagram
    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    canvas = _cover_crop(photo, W, H).convert("RGBA")
    _add_logo_badge(canvas, logo, margin=int(W * 0.04), badge_width=int(W * 0.20))
    _add_title_band(canvas, title, W, H, font_size=int(W * 0.052), max_lines=4)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Cloudflare R2 (mismo patrón que publish_instagram_reel.py)
# --------------------------------------------------------------------------
def get_r2_client():
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
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


def upload_social_image(s3_client, key, image_bytes):
    s3_client.put_object(Bucket=R2_BUCKET_NAME, Key=key, Body=image_bytes, ContentType="image/jpeg")
    domain = R2_PUBLIC_DOMAIN
    if not domain:
        # Sin dominio público configurado: se usa una URL prefirmada de R2
        # (mismo mecanismo que publish_instagram_reel.py, que ya funciona con Meta).
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": key},
            ExpiresIn=PRESIGNED_URL_EXPIRY,
        )
    if not domain.startswith("http://") and not domain.startswith("https://"):
        domain = f"https://{domain}"
    domain = domain.rstrip("/")
    return f"{domain}/{key.lstrip('/')}"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def build_post_text(post):
    title = strip_html(post["title"]["rendered"])
    excerpt = strip_html(post["excerpt"]["rendered"])
    link = post["link"]
    parts = [title]
    if excerpt:
        parts.append(excerpt)
    parts.append(link)
    return "\n\n".join(parts)


def main():
    try:
        posts = fetch_recent_posts(per_page=SEED_LOOKBACK)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"ERROR consultando la API de WordPress: {exc}", file=sys.stderr)
        return 1

    posts_by_id = {p["id"]: p for p in posts}

    state = load_state()
    if state is None:
        # Primera ejecución: sembrar sin publicar nada, para no volcar de
        # golpe el histórico del blog en redes sociales.
        state = {
            "processed_post_ids": sorted(posts_by_id.keys()),
            "log": [],
            "seeded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        save_state(state)
        print(f"Primera ejecución: sembrados {len(posts_by_id)} posts existentes sin publicar en redes.")
        return 0

    processed = set(state.get("processed_post_ids", []))
    candidates = sorted(pid for pid in posts_by_id if pid not in processed)

    if not candidates:
        print("Sin posts nuevos.")
        return 0

    candidates = candidates[:MAX_POSTS_PER_RUN]
    print(f"Posts nuevos a procesar: {candidates}")

    try:
        logo_bytes = download(LOGO_URL)
    except Exception as exc:
        print(f"Aviso: no se pudo descargar el logo ({exc}); se seguirá sin logo si hace falta.", file=sys.stderr)
        logo_bytes = None

    s3_client = None
    any_error = False
    fb_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    fb_page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
    ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    ig_user_id = os.environ.get("INSTAGRAM_USER_ID", "")

    for pid in candidates:
        post = posts_by_id[pid]
        title = strip_html(post["title"]["rendered"])
        link = post["link"]
        text = build_post_text(post)
        image_url_original = get_featured_image_url(post)

        entry = {
            "post_id": pid,
            "title": title,
            "link": link,
            "processed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "facebook_published": False,
            "instagram_published": False,
            "errors": [],
        }

        social_image_url = None
        if image_url_original and logo_bytes:
            try:
                photo_bytes = download(image_url_original)
                composed = compose_social_image(photo_bytes, logo_bytes, title)
                if s3_client is None:
                    s3_client = get_r2_client()
                key = f"{SOCIAL_IMAGES_PREFIX}post-{pid}.jpg"
                social_image_url = upload_social_image(s3_client, key, composed)
            except Exception as e:
                entry["errors"].append(f"Componiendo/subiendo imagen: {e}")
        elif not image_url_original:
            entry["errors"].append("Sin imagen destacada en el post; se omite Instagram.")

        try:
            if fb_token and fb_page_id:
                publish_facebook(fb_token, fb_page_id, text, social_image_url)
                entry["facebook_published"] = True
            else:
                entry["errors"].append("Facebook: faltan los secrets FACEBOOK_PAGE_ACCESS_TOKEN / FACEBOOK_PAGE_ID")
                any_error = True
        except Exception as e:
            entry["errors"].append(f"Facebook: {e}")
            any_error = True

        if social_image_url:
            try:
                if ig_token:
                    publish_instagram(ig_token, ig_user_id, text, social_image_url)
                    entry["instagram_published"] = True
                else:
                    entry["errors"].append("Instagram: falta el secret INSTAGRAM_ACCESS_TOKEN")
                    any_error = True
            except Exception as e:
                entry["errors"].append(f"Instagram: {e}")
                any_error = True

        state.setdefault("processed_post_ids", []).append(pid)
        state.setdefault("log", []).append(entry)
        print(json.dumps(entry, ensure_ascii=False, indent=2))

    state["processed_post_ids"] = sorted(set(state["processed_post_ids"]))
    state["log"] = state.get("log", [])[-100:]  # no dejar crecer el historial indefinidamente
    save_state(state)

    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
