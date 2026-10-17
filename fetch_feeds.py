"""
fetch_feeds.py

Vigía de temas para el blog de valenciatribal.com. Descarga SOLO fuentes
oficiales y vuelca sus entradas (source, title, link, pubDate y, si se conocen,
event_start / event_end / place) en docs/feeds.json.

Fuentes (todas oficiales, decisión de Javier del 17/10/2026):
  - visitvalencia_agenda: agenda oficial de Visit València (turismo oficial de
    la ciudad). RSS https://www.visitvalencia.com/rss.xml
  - visitvalencia_blog: blog oficial de Visit València.
    RSS https://blog.visitvalencia.com/rss.xml
  - dival_agenda: agenda turística de la Diputació de València (ferias, fiestas
    y eventos de los pueblos de la provincia), vía la API REST pública de
    turisme.dival.es (tipo de contenido turist_agenda, con fechas en acf).
  - ivc_agenda: agenda cultural del Institut Valencià de Cultura (Generalitat),
    dataset abierto de dadesobertes.gva.es (actualización diaria). Solo
    provincia de Valencia, sin películas sueltas, eventos que empiezan en los
    próximos 45 días o siguen en curso.

Valencia Bonita (valenciabonita.es) está en LISTA NEGRA desde el 15/10/2026 y
NO se descarga. No se añaden medios, blogs ni agendas de terceros.

Por qué existe este script
---------------------------
La tarea programada que escribe los posts corre desatendida y no puede pedir
aprobación de red por dominio. Este script corre en GitHub Actions y publica
un JSON público (repositorio público) que la tarea lee con una sola petición:

    https://raw.githubusercontent.com/aceroquitocomercial-arch/excursiones-valencia-tribal/main/docs/feeds.json

El JSON no contiene nada sensible: solo título, enlace y fechas de entradas
públicas de webs oficiales.
"""

import csv
import io
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

USER_AGENT = "Mozilla/5.0 (compatible; ValenciaTribalFeedBot/2.0; +https://valenciatribal.com)"
OUTPUT_PATH = "docs/feeds.json"
BLACKLIST = ("valenciabonita",)

IVC_CSV = (
    "https://dadesobertes.gva.es/dataset/25cc4d21-e1dd-4d05-b057-dbcc44d4338c/resource/"
    "f76b5222-c09f-46cb-8bb0-4fbe377c611d/download/"
    "lista-de-actividades-culturales-programadas-por-el-ivc.csv"
)
IVC_HORIZON_DAYS = 45
IVC_SKIP_TYPES = {"pelicula", "película", "cine", "audiovisuales y cinematografía"}
# Formación y actividades profesionales (no son planes para el público general)
IVC_SKIP_TITLE = re.compile(r"\b(abono|entrenamientos?|talleres?|curso|clase|masterclass|residencia|saludarte|desmitificando)\b|, con ", re.I)
IVC_SKIP_PLACE = re.compile(r"lagranja", re.I)


def http_get(url: str, timeout: int = 40, tries: int = 3) -> bytes:
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
    raise last  # type: ignore[misc]


def rss_items(url: str, source: str) -> list[dict]:
    root = ET.fromstring(http_get(url))
    out = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        out.append({
            "source": source,
            "title": (item.findtext("title") or "").strip(),
            "link": link,
            "pubDate": (item.findtext("pubDate") or "").strip(),
        })
    return out


def acf_date(s: str | None) -> str | None:
    if s and re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def dival_items(source: str) -> list[dict]:
    url = ("https://turisme.dival.es/wp-json/wp/v2/turist_agenda"
           "?per_page=40&orderby=date&order=desc&_fields=date_gmt,link,title,acf")
    data = json.loads(http_get(url))
    today = date.today().isoformat()
    out = []
    for x in data:
        acf = x.get("acf") or {}
        start, end = acf_date(acf.get("fecha")), acf_date(acf.get("fecha_fin"))
        end = end or start
        if end and end < today:
            continue  # ya terminado
        title = re.sub(r"<[^>]+>", "", x["title"]["rendered"])
        title = title.replace("&#8217;", "’").replace("&#8211;", "–").replace("&amp;", "&").strip()
        pub = datetime.fromisoformat(x["date_gmt"]).replace(tzinfo=timezone.utc)
        out.append({
            "source": source,
            "title": title,
            "link": x["link"],
            "pubDate": format_datetime(pub),
            "event_start": start,
            "event_end": end,
        })
    return out


def ivc_items(source: str) -> list[dict]:
    raw = http_get(IVC_CSV, timeout=90).decode("utf-8-sig", "ignore")
    rows = csv.DictReader(io.StringIO(raw), delimiter=";")
    today = date.today()
    horizon = today + timedelta(days=IVC_HORIZON_DAYS)
    grouped: dict[str, dict] = {}
    for r in rows:
        if "val" not in (r.get("provincia") or "").lower():
            continue
        if (r.get("tipo_evento") or "").strip().lower() in IVC_SKIP_TYPES:
            continue
        if IVC_SKIP_TITLE.search(r.get("titulo_evento") or "") or IVC_SKIP_PLACE.search(r.get("lugar_evento") or ""):
            continue
        link = (r.get("web") or "").strip()
        if not link.startswith("http"):
            continue
        try:
            s = datetime.strptime(r["fecha_inicio"].strip(), "%d/%m/%Y").date()
            e = datetime.strptime((r.get("fecha_fin") or r["fecha_inicio"]).strip(), "%d/%m/%Y").date()
        except (ValueError, KeyError):
            continue
        g = grouped.setdefault(link, {
            "title": (r.get("titulo_evento") or "").strip(),
            "place": f'{(r.get("lugar_evento") or "").strip()}, {(r.get("municipio") or "").strip()}'.strip(", "),
            "start": s, "end": e,
        })
        g["start"], g["end"] = min(g["start"], s), max(g["end"], e)
    out = []
    for link, g in grouped.items():
        if g["end"] < today or g["start"] > horizon:
            continue
        out.append({
            "source": source,
            "title": g["title"].capitalize() if g["title"].isupper() else g["title"],
            "link": link,
            "pubDate": "",
            "event_start": g["start"].isoformat(),
            "event_end": g["end"].isoformat(),
            "place": g["place"],
        })
    out.sort(key=lambda x: x["event_start"])
    return out


FEEDS = [
    ("visitvalencia_agenda", lambda s: rss_items("https://www.visitvalencia.com/rss.xml", s)),
    ("visitvalencia_blog", lambda s: rss_items("https://blog.visitvalencia.com/rss.xml", s)),
    ("dival_agenda", dival_items),
    ("ivc_agenda", ivc_items),
]


def main() -> int:
    all_items: list[dict] = []
    errors: list[dict] = []
    for source, fn in FEEDS:
        try:
            items = [i for i in fn(source) if not any(b in i["link"] for b in BLACKLIST)]
            print(f"[{source}] {len(items)} items")
            all_items.extend(items)
        except Exception as exc:  # una fuente caída no debe tumbar las demás
            msg = f"{type(exc).__name__}: {exc}"
            print(f"[{source}] ERROR: {msg}", file=sys.stderr)
            errors.append({"source": source, "error": msg})

    # Si TODAS las fuentes fallan, no se sobrescribe el JSON: mejor datos algo
    # viejos que un archivo vacío.
    if len(errors) == len(FEEDS):
        print("Todas las fuentes fallaron, no se actualiza el JSON.", file=sys.stderr)
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
