#!/usr/bin/env python3
"""
Publica un post en la Página de Facebook y/o en la cuenta de Instagram
Business vinculada, usando la Graph API de Meta directamente (sin Metricool).

Reutiliza los secrets ya existentes en este repo (de la automatización de
Reels de Instagram/Facebook):
    FACEBOOK_PAGE_ACCESS_TOKEN  Token de acceso de la Página de Facebook.
    FACEBOOK_PAGE_ID            ID numérico de la Página de Facebook.
    INSTAGRAM_ACCESS_TOKEN      Token de acceso de Instagram. Puede ser:
                                 - un token "IG..." de la API nueva de
                                   Instagram con Instagram Login (se usa
                                   contra graph.instagram.com), o
                                 - un token clásico de Facebook Login for
                                   Business (se usa contra graph.facebook.com
                                   junto con INSTAGRAM_USER_ID).
                                 Igual que en publish_instagram_reel.py.
    INSTAGRAM_USER_ID           ID numérico de la cuenta de Instagram Business
                                 (usado cuando el token NO es de tipo "IG").

Y estos inputs del workflow_dispatch:
    POST_TEXT           Texto del post.
    IMAGE_URL            URL pública de la imagen (opcional, pero obligatoria
                         si PUBLISH_INSTAGRAM=true).
    PUBLISH_FACEBOOK    "true"/"false"
    PUBLISH_INSTAGRAM   "true"/"false"

Sale con código de error != 0 si alguna publicación solicitada falla, para que
el step de GitHub Actions se marque en rojo y sea visible en el historial de
Actions. Cada red se intenta de forma independiente: si Instagram falla,
Facebook igualmente se intenta (y viceversa).
"""

import os
import sys
import time
import json
import urllib.request
import urllib.parse
import urllib.error

GRAPH_VERSION = "v21.0"
FACEBOOK_GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"


def env_bool(name, default=False):
    val = os.environ.get(name, "")
    if val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def http_post(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} calling {url}: {err_body}") from None


def http_get(url, params):
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    try:
        with urllib.request.urlopen(full_url, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} calling {url}: {err_body}") from None


def publish_facebook(token, page_id, text, image_url):
    if image_url:
        resp = http_post(f"{FACEBOOK_GRAPH_BASE}/{page_id}/photos", {
            "url": image_url,
            "caption": text,
            "access_token": token,
        })
        print("Facebook (foto) publicado:", resp)
    else:
        resp = http_post(f"{FACEBOOK_GRAPH_BASE}/{page_id}/feed", {
            "message": text,
            "access_token": token,
        })
        print("Facebook (texto) publicado:", resp)
    return resp


def get_instagram_graph_base(token):
    """Misma heurística que publish_instagram_reel.py: si el token es de la
    API nueva de Instagram (Instagram Login), hay que hablar con
    graph.instagram.com; si es un token clásico de Facebook Login for
    Business, se usa graph.facebook.com."""
    if token and (token.startswith("IG") or "instagram" in token.lower()):
        return f"https://graph.instagram.com/{GRAPH_VERSION}"
    return FACEBOOK_GRAPH_BASE


def get_effective_instagram_user_id(token, configured_user_id, graph_base):
    if token and token.startswith("IG"):
        try:
            resp = http_get(f"{graph_base}/me", {"fields": "id", "access_token": token})
            if "id" in resp:
                return resp["id"]
        except Exception:
            pass
    return configured_user_id or "me"


def publish_instagram(token, configured_user_id, text, image_url):
    if not image_url:
        raise RuntimeError("Instagram requiere image_url; no se ha proporcionado ninguna imagen.")

    graph_base = get_instagram_graph_base(token)
    ig_id = get_effective_instagram_user_id(token, configured_user_id, graph_base)
    print(f"Instagram: usando host {graph_base} y user id {ig_id}")

    # Paso 1: crear el contenedor de medio
    creation = http_post(f"{graph_base}/{ig_id}/media", {
        "image_url": image_url,
        "caption": text,
        "access_token": token,
    })
    creation_id = creation.get("id")
    if not creation_id:
        raise RuntimeError(f"No se obtuvo creation_id al crear el contenedor: {creation}")

    # Paso 2: esperar a que el contenedor esté listo (FINISHED)
    status = None
    for _ in range(15):
        status_resp = http_get(f"{graph_base}/{creation_id}", {
            "fields": "status_code",
            "access_token": token,
        })
        status = status_resp.get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"El contenedor de Instagram falló al procesarse: {status_resp}")
        time.sleep(3)
    else:
        raise RuntimeError(f"El contenedor de Instagram no terminó de procesarse a tiempo (último estado: {status}).")

    # Paso 3: publicar el contenedor
    publish_resp = http_post(f"{graph_base}/{ig_id}/media_publish", {
        "creation_id": creation_id,
        "access_token": token,
    })
    print("Instagram publicado:", publish_resp)
    return publish_resp


def main():
    fb_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
    ig_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    ig_id = os.environ.get("INSTAGRAM_USER_ID", "")
    text = os.environ.get("POST_TEXT", "")
    image_url = os.environ.get("IMAGE_URL", "").strip()
    do_fb = env_bool("PUBLISH_FACEBOOK", False)
    do_ig = env_bool("PUBLISH_INSTAGRAM", True)

    if not text:
        print("ERROR: POST_TEXT vacío", file=sys.stderr)
        sys.exit(1)

    results = {}
    errors = []

    if do_fb:
        if not fb_token or not page_id:
            errors.append("Facebook: faltan los secrets FACEBOOK_PAGE_ACCESS_TOKEN / FACEBOOK_PAGE_ID")
        else:
            try:
                results["facebook"] = publish_facebook(fb_token, page_id, text, image_url)
            except Exception as e:
                errors.append(f"Facebook: {e}")

    if do_ig:
        if not ig_token:
            errors.append("Instagram: falta el secret INSTAGRAM_ACCESS_TOKEN")
        else:
            try:
                results["instagram"] = publish_instagram(ig_token, ig_id, text, image_url)
            except Exception as e:
                errors.append(f"Instagram: {e}")

    print("\n--- Resumen ---")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    if errors:
        print("\nErrores:", file=sys.stderr)
        for e in errors:
            print(" -", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
