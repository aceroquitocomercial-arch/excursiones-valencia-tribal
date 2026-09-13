"""
fetch_feeds.py

Descarga los feeds RSS de valenciabonita.es y blog.visitvalencia.com y vuelca
sus <item> (title, link, pubDate) en docs/feeds.json.

Por qué existe este script
---------------------------
La tarea programada de valenciatribal.com que lee estos feeds corre de forma
desatendida (nadie hay delante para aprobar peticiones de red nuevas), y
además el entorno donde corre esa tarea tiene bloqueado el acceso directo a
valenciabonita.es y blog.visitvalencia.com por política de red.

Este script corre en un runner de GitHub Actions (con acceso normal a
internet, sin esas restricciones) y publica un JSON de solo lectura dentro
de este mismo repositorio. Con GitHub Pages activado (Settings -> Pages,
Source: rama "main", carpeta "/docs"), ese JSON queda disponible en una URL
pública tipo:

    https://<usuario>.github.io/excursiones-valencia-tribal/feeds.json

La tarea programada de valenciatribal.com puede entonces leer esa URL en
lugar de los feeds originales, sin necesitar aprobación de red ni tokens.

El JSON no contiene nada sensible: solo título, enlace y fecha de
publicación de entradas de blogs públicos (la misma información que ya
está en los feeds RSS públicos originales).
"""

import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

FEEDS = [
    {"source": "valenciabonita", "url": "https://valenciabonita.es/feed/"},
    {"source": "visitvalencia", "url": "https://blog.visitvalencia.com/rss.xml"},
]

USER_AGENT = "Mozilla/5.0 (compatible; ValenciaTribalFeedBot/1.0; +https://valenciatribal.com)"

OUTPUT_PATH = "docs/feeds.json"


def fetch_items(url: str, source: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    root = ET.fromstring(raw)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not link:
            continue
        items.append(
            {
                "source": source,
                "title": title,
                "link": link,
                "pubDate": pub_date,
            }
        )
    return items


def main() -> int:
    all_items: list[dict] = []
    errors: list[dict] = []

    for feed in FEEDS:
        try:
            items = fetch_items(feed["url"], feed["source"])
            print(f"[{feed['source']}] {len(items)} items")
            all_items.extend(items)
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"[{feed['source']}] ERROR: {msg}", file=sys.stderr)
            errors.append({"source": feed["source"], "error": msg})

    # Si AMBOS feeds fallan, no sobrescribimos el JSON existente: es mejor
    # servir datos ligeramente viejos que un archivo vacío que borraría el
    # historial de "seen_links" al otro lado.
    if errors and len(errors) == len(FEEDS):
        print("Todos los feeds fallaron, no se actualiza el JSON.", file=sys.stderr)
        return 1

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": all_items,
        "errors": errors,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Escrito {OUTPUT_PATH} con {len(all_items)} items totales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
