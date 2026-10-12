#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generador de Refresh Token de YouTube Data API v3 para Valencia Tribal
Uso:
  python get_youtube_token.py
  python get_youtube_token.py --client-id <CLIENT_ID> --client-secret <CLIENT_SECRET>
"""

import os
import sys
import argparse
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(".env")

def main():
    parser = argparse.ArgumentParser(description="Generar YouTube Refresh Token")
    parser.add_argument("--client-id", help="Client ID de Google Cloud OAuth 2.0")
    parser.add_argument("--client-secret", help="Client Secret de Google Cloud OAuth 2.0")
    args = parser.parse_args()

    client_id = (args.client_id or os.getenv("YOUTUBE_CLIENT_ID", "")).strip()
    client_secret = (args.client_secret or os.getenv("YOUTUBE_CLIENT_SECRET", "")).strip()

    if not client_id or not client_secret:
        print("❌ Error: Se requiere Client ID y Client Secret.")
        print("Pásalos por argumento o configúralos en .env:")
        print("  python get_youtube_token.py --client-id TU_CLIENT_ID --client-secret TU_CLIENT_SECRET")
        return

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("❌ Error: Falta instalar google-auth-oauthlib.")
        print("Ejecuta: pip install google-auth-oauthlib")
        return

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"]
        }
    }

    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly"
    ]

    print("\n" + "=" * 65)
    print("🔑 GENERADOR DE TOKEN PARA YOUTUBE SHORTS (VALENCIA TRIBAL)")
    print("=" * 65)
    print(f" • Client ID: {client_id[:25]}...")
    print(" • Scopes:    youtube.upload")
    print("\n🌐 Abriendo navegador para autorizar tu canal de YouTube...")
    print("   (Si te sale 'Google no ha verificado esta app', haz clic en 'Avanzado' -> 'Ir a ... (no seguro)')")

    try:
        flow = InstalledAppFlow.from_client_config(client_config, scopes=scopes)
        creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

        if not creds.refresh_token:
            print("\n⚠️ Aviso: Google no devolvió un refresh_token nuevo.")
            print("Asegúrate de haber seleccionado 'prompt=consent' o de revocar permisos previos en:")
            print("https://myaccount.google.com/permissions")
            return

        print("\n" + "=" * 65)
        print("✅ ¡AUTORIZACIÓN EXITOSA!")
        print("=" * 65)
        print("\nCopia este valor en tu archivo .env y en los Secretos de GitHub:")
        print(f"\nYOUTUBE_REFRESH_TOKEN={creds.refresh_token}\n")

        # Opción de auto-actualizar .env
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()

            import re
            if "YOUTUBE_REFRESH_TOKEN=" in content:
                content = re.sub(r"YOUTUBE_REFRESH_TOKEN=.*", f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}", content)
            else:
                content += f"\nYOUTUBE_REFRESH_TOKEN={creds.refresh_token}"

            if "YOUTUBE_CLIENT_ID=" in content:
                content = re.sub(r"YOUTUBE_CLIENT_ID=.*", f"YOUTUBE_CLIENT_ID={client_id}", content)
            if "YOUTUBE_CLIENT_SECRET=" in content:
                content = re.sub(r"YOUTUBE_CLIENT_SECRET=.*", f"YOUTUBE_CLIENT_SECRET={client_secret}", content)

            with open(env_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("💾 El archivo local .env ha sido actualizado automáticamente.")

    except Exception as e:
        print(f"\n❌ Error durante el flujo de autorización: {e}")

if __name__ == "__main__":
    main()
