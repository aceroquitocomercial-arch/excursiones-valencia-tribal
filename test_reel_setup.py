#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Diagnostic & Setup Tool for Instagram Reels & Cloudflare R2
Project: Valencia Tribal (valenciatribal.com)

Usage:
  python test_reel_setup.py --all
  python test_reel_setup.py --r2
  python test_reel_setup.py --instagram
  python test_reel_setup.py --get-long-lived-token --client-id <APP_ID> --client-secret <APP_SECRET> --token <SHORT_TOKEN>
"""

import os
import sys
import argparse
import requests
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Asegurar codificación UTF-8 en consola de Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def load_env():
    """Load environment variables from .env if present."""
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() not in os.environ:
                            os.environ[k.strip()] = v.strip().strip("\"'")

load_env()

GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")

def get_graph_api_base(token: str = None) -> str:
    host = os.getenv("GRAPH_API_HOST")
    if host:
        return f"https://{host.replace('https://', '').strip('/')}/{GRAPH_API_VERSION}"
    if token and (token.startswith("IG") or "instagram" in token.lower()):
        return f"https://graph.instagram.com/{GRAPH_API_VERSION}"
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}"



def test_cloudflare_r2():
    print("\n" + "=" * 60)
    print("☁️  VERIFICACIÓN DE CLOUDFLARE R2")
    print("=" * 60)

    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    bucket_name = os.getenv("R2_BUCKET_NAME")
    public_domain = os.getenv("R2_PUBLIC_DOMAIN", "").strip()

    print(f" • R2_ACCOUNT_ID:        {account_id or '❌ NO DEFINIDO'}")
    print(f" • R2_ACCESS_KEY_ID:     {access_key[:6] + '...' if access_key else '❌ NO DEFINIDO'}")
    print(f" • R2_SECRET_ACCESS_KEY: {'CONFIGURADO' if secret_key else '❌ NO DEFINIDO'}")
    print(f" • R2_BUCKET_NAME:       {bucket_name or '❌ NO DEFINIDO'}")
    print(f" • R2_PUBLIC_DOMAIN:     {public_domain or '(No configurado, se usarán URLs prefirmadas S3)'}")

    if not all([account_id, access_key, secret_key, bucket_name]):
        print("\n❌ Error: Faltan variables obligatorias de Cloudflare R2 en .env")
        return False

    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4")
        )

        # 1. Test bucket access
        s3.head_bucket(Bucket=bucket_name)
        print(f"\n✅ Conexión exitosa al bucket '{bucket_name}' en Cloudflare R2.")

        # 2. List queue/ or root
        queue_resp = s3.list_objects_v2(Bucket=bucket_name, Prefix="queue/")
        queue_items = queue_resp.get("Contents", [])
        videos_in_queue = [i["Key"] for i in queue_items if i["Key"].lower().endswith((".mp4", ".mov"))]

        location_label = "queue/"
        if not videos_in_queue:
            # Buscar en raíz del bucket
            root_resp = s3.list_objects_v2(Bucket=bucket_name)
            root_items = root_resp.get("Contents", [])
            videos_in_queue = [
                i["Key"] for i in root_items
                if i["Key"].lower().endswith((".mp4", ".mov")) and not i["Key"].startswith("published/")
            ]
            location_label = f"raíz del bucket '{bucket_name}'"

        print(f"\n📁 Videos pendientes en cola ({location_label}): {len(videos_in_queue)}")
        for v in videos_in_queue[:5]:
            print(f"   🎬 {v}")
        if len(videos_in_queue) > 5:
            print(f"   ... y {len(videos_in_queue) - 5} más.")

        # 3. List published/
        pub_resp = s3.list_objects_v2(Bucket=bucket_name, Prefix="published/")
        pub_items = pub_resp.get("Contents", [])
        videos_pub = [i["Key"] for i in pub_items if i["Key"].lower().endswith((".mp4", ".mov"))]
        print(f"📁 Videos ya publicados (published/): {len(videos_pub)}")

        # 4. Test URL generation
        if videos_in_queue:
            sample_key = videos_in_queue[0]
            if public_domain:
                sample_url = f"https://{public_domain.replace('https://', '').rstrip('/')}/{sample_key}"
                print(f"🔗 URL Pública de prueba: {sample_url}")
            else:
                presigned = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket_name, "Key": sample_key},
                    ExpiresIn=3600
                )
                print(f"🔗 URL Prefirmada S3 generada correctamente ({len(presigned)} caracteres).")

        return True

    except ClientError as e:
        print(f"\n❌ Error de acceso a Cloudflare R2: {e}")
        return False
    except Exception as e:
        print(f"\n❌ Error inesperado conectando a R2: {e}")
        return False


def test_instagram():
    print("\n" + "=" * 60)
    print("📸 VERIFICACIÓN DE INSTAGRAM GRAPH API")
    print("=" * 60)

    user_id = os.getenv("INSTAGRAM_USER_ID")
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")

    print(f" • INSTAGRAM_USER_ID:      {user_id or '❌ NO DEFINIDO'}")
    print(f" • INSTAGRAM_ACCESS_TOKEN: {token[:15] + '...' if token else '❌ NO DEFINIDO'}")

    if not user_id or not token:
        print("\n❌ Error: Faltan variables de Instagram en .env (INSTAGRAM_USER_ID o INSTAGRAM_ACCESS_TOKEN)")
        return False

    api_base = get_graph_api_base(token)
    endpoint_label = "Instagram Login for Business (graph.instagram.com)" if "graph.instagram.com" in api_base else "Facebook Login (graph.facebook.com)"
    print(f" • API Endpoint:           {api_base} ({endpoint_label})")

    # 1. Query account details
    url = f"{api_base}/{user_id}"
    params = {
        "fields": "id,username,name,account_type,media_count,followers_count",
        "access_token": token
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()

        if resp.status_code != 200:
            err = data.get("error", {})
            print(f"\n❌ Error al consultar la cuenta de Instagram en Meta Graph API:")
            print(f"   Mensaje: {err.get('message')}")
            print(f"   Código:  {err.get('code')}")
            print(f"   Subcódigo: {err.get('error_subcode')}")
            print(f"   Tipo:    {err.get('type')}")
            return False

        print("\n✅ Conexión con Instagram Graph API exitosa:")
        print(f"   👤 Usuario:       @{data.get('username')}")
        print(f"   🏷️  Nombre:        {data.get('name')}")
        print(f"   🆔 ID Cuenta:     {data.get('id')}")
        print(f"   📊 Tipo Cuenta:   {data.get('account_type', 'N/A')}")
        print(f"   👥 Seguidores:    {data.get('followers_count', 'N/A')}")
        print(f"   🎞️  Publicaciones: {data.get('media_count', 'N/A')}")

        # 2. Check publishing quota / container capabilities
        limit_url = f"{api_base}/{user_id}/content_publishing_limit"
        limit_resp = requests.get(limit_url, params={"access_token": token}, timeout=10)
        if limit_resp.status_code == 200:
            limit_data = limit_resp.json().get("data", [{}])[0]
            quota_usage = limit_data.get("quota_usage", 0)
            config_quota = limit_data.get("config", {}).get("quota_total", 100)
            print(f"   📈 Cuota de publicaciones hoy: {quota_usage} / {config_quota} Reels/posts usados.")
        
        return True

    except Exception as e:
        print(f"\n❌ Error de red consultando Instagram: {e}")
        return False


def test_facebook_page():
    print("\n" + "=" * 60)
    print("📘 VERIFICACIÓN DE FACEBOOK PAGE (PUBLICACIÓN SIMULTÁNEA)")
    print("=" * 60)

    page_id = os.getenv("FACEBOOK_PAGE_ID")
    token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")

    print(f" • FACEBOOK_PAGE_ID:           {page_id or '(No configurado aún)'}")
    print(f" • FACEBOOK_PAGE_ACCESS_TOKEN: {token[:15] + '...' if token else '(No configurado aún)'}")

    if not page_id or not token:
        print("\nℹ️ Publicación en Facebook Page no activa (se publicará solo en Instagram).")
        return None

    url = f"https://graph.facebook.com/v21.0/{page_id}"
    try:
        resp = requests.get(url, params={"fields": "id,name,link", "access_token": token}, timeout=15)
        data = resp.json()
        if resp.status_code != 200:
            err = data.get("error", {})
            print(f"\n❌ Error al verificar Página de Facebook: {err.get('message')}")
            return False

        print("\n✅ Conexión con Facebook Page exitosa:")
        print(f"   🏢 Nombre Página: {data.get('name')}")
        print(f"   🆔 ID Página:     {data.get('id')}")
        return True
    except Exception as e:
        print(f"\n❌ Error de red consultando Facebook: {e}")
        return False


def exchange_long_lived_token(app_id: str, app_secret: str, short_token: str):
    """Exchanges a short-lived user token (1-2 hours) for a 60-day long-lived token."""
    print("\n🔄 Solicitando Token de Larga Duración a Meta OAuth...")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token
    }
    resp = requests.get(url, params=params, timeout=15)
    data = resp.json()
    if resp.status_code == 200 and "access_token" in data:
        long_token = data["access_token"]
        expires_in = data.get("expires_in", "desconocido")
        days = round(expires_in / 86400, 1) if isinstance(expires_in, (int, float)) else expires_in
        print("✅ ¡Token de larga duración obtenido exitosamente!")
        print(f"   Duración estimada: ~{days} días ({expires_in} segundos)")
        print(f"\nGuarda este token en tu archivo .env como INSTAGRAM_ACCESS_TOKEN:")
        print(f"\n{long_token}\n")
    else:
        print("❌ Error al intercambiar token:")
        print(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Herramienta de diagnóstico para Instagram Reels y Cloudflare R2.")
    parser.add_argument("--r2", action="store_true", help="Probar conexión con Cloudflare R2.")
    parser.add_argument("--instagram", action="store_true", help="Probar conexión con Instagram Graph API.")
    parser.add_argument("--facebook", action="store_true", help="Probar conexión con Facebook Page API.")
    parser.add_argument("--all", action="store_true", help="Ejecutar todas las verificaciones.")
    parser.add_argument("--get-long-lived-token", action="store_true", help="Intercambiar token temporal por token de 60 días.")
    parser.add_argument("--client-id", type=str, help="Meta App ID")
    parser.add_argument("--client-secret", type=str, help="Meta App Secret")
    parser.add_argument("--token", type=str, help="Short-lived user access token")

    args = parser.parse_args()

    if args.get_long_lived_token:
        if not args.client_id or not args.client_secret or not args.token:
            print("❌ Debes proporcionar --client-id, --client-secret y --token")
            sys.exit(1)
        exchange_long_lived_token(args.client_id, args.client_secret, args.token)
        sys.exit(0)

    run_all = args.all or (not args.r2 and not args.instagram and not args.facebook)

    r2_ok = True
    ig_ok = True
    fb_ok = None

    if run_all or args.r2:
        r2_ok = test_cloudflare_r2()
    if run_all or args.instagram:
        ig_ok = test_instagram()
    if run_all or args.facebook:
        fb_ok = test_facebook_page()

    print("\n" + "=" * 60)
    print("RESUMEN DE DIAGNÓSTICO:")
    print(f" • Cloudflare R2:       {'✅ LISTO' if r2_ok else '⚠️ REVISAR CONFIGURACIÓN'}")
    print(f" • Instagram Graph API: {'✅ LISTO' if ig_ok else '⚠️ REVISAR CONFIGURACIÓN'}")
    if fb_ok is not None:
        print(f" • Facebook Page API:   {'✅ LISTO' if fb_ok else '⚠️ REVISAR CONFIGURACIÓN'}")
    else:
        print(f" • Facebook Page API:   ⚪ NO CONFIGURADO (OPCIONAL)")
    print("=" * 60 + "\n")
