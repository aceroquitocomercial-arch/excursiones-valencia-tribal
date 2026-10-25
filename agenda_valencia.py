#!/usr/bin/env python3
"""Genera las páginas fijas "Qué hacer en Valencia este fin de semana" y "Planes gratis en Valencia" de valenciatribal.com.

Lee los posts de la categoría Agenda Valencia (263, incluye subcategorías) y las
excursiones (categoría de producto 260) por la API REST pública, saca la fecha de
cada evento del extracto, descarta los que ya pasaron y reescribe la página.

Uso:
  python3 agenda_valencia.py --dry-run out.html     # solo genera el HTML
  WP_USER=... WP_APP_PASS=... python3 agenda_valencia.py --page-id 49232 --gratis-page-id 49240
"""
import argparse, base64, datetime as dt, html, json, os, re, sys, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

SITE = "https://valenciatribal.com"
AGENDA_CAT = 263
EXCURSION_CAT = 260
TZ = ZoneInfo("Europe/Madrid")
UA = "ValenciaTribe-agenda/1.0"

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
         "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
         "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9,
         "sept": 9, "oct": 10, "nov": 11, "dic": 12}
MES_RE = r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|ene|feb|mar|abr|may|jun|jul|ago|sept|sep|oct|nov|dic)\b\.?"
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_NOMBRE = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre"]


def get_json(url, auth=None, data=None, method=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    if method:
        req.method = method
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def year_for(month, ref, explicit=None):
    if explicit:
        return int(explicit)
    y = ref.year
    if month < ref.month - 3:  # p. ej. post de noviembre que habla de enero
        y += 1
    return y


def parse_dates(s, ref):
    """Devuelve (inicio, fin) de las fechas encontradas en el texto, o None."""
    s = s.lower().replace("–", "-").replace("—", "-")
    found = []

    def add(d, m, y=None):
        try:
            found.append(dt.date(year_for(m, ref, y), m, int(d)))
        except ValueError:
            pass

    yr = r"(?:\s+(?:de\s+|del\s+)?(20\d\d))?"
    # del 16 al 18 de octubre (de 2026) / del 31 de octubre al 2 de noviembre
    for m in re.finditer(r"(\d{1,2})(?:\s+de\s+" + MES_RE + r")?\s+(?:al|-|hasta el|y el|y)\s+(\d{1,2})\s+de\s+" + MES_RE + yr, s):
        d1, m1, d2, m2, y = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        add(d1, MESES[m1] if m1 else MESES[m2], y)
        add(d2, MESES[m2], y)
    # 1-7 octubre / (2-4 oct)
    for m in re.finditer(r"(\d{1,2})\s*-\s*(\d{1,2})\s+(?:de\s+)?" + MES_RE + yr, s):
        add(m.group(1), MESES[m.group(3)], m.group(4))
        add(m.group(2), MESES[m.group(3)], m.group(4))
    # 10, 11 y 12 de octubre
    for m in re.finditer(r"((?:\d{1,2},\s*)+\d{1,2})\s+y\s+(\d{1,2})\s+de\s+" + MES_RE + yr, s):
        for d in re.findall(r"\d{1,2}", m.group(1)) + [m.group(2)]:
            add(d, MESES[m.group(3)], m.group(4))
    # 17 de octubre (de 2026)
    for m in re.finditer(r"\b(\d{1,2})\s+de\s+" + MES_RE + yr, s):
        add(m.group(1), MESES[m.group(2)], m.group(3))
    if not found:
        return None
    # descarta fechas absurdamente lejos del post (menciones a ediciones futuras/pasadas)
    found = [d for d in found if -60 <= (d - ref).days <= 240] or found
    return min(found), max(found)


def fecha_larga(d):
    return f"{DIAS[d.weekday()]} {d.day} de {MESES_NOMBRE[d.month]}"


def rango_txt(a, b):
    if a == b:
        return fecha_larga(a).capitalize()
    if a.month == b.month:
        return f"Del {a.day} al {b.day} de {MESES_NOMBRE[b.month]}"
    return f"Del {a.day} de {MESES_NOMBRE[a.month]} al {b.day} de {MESES_NOMBRE[b.month]}"


def weekend_for(today):
    wd = today.weekday()
    fri = today - dt.timedelta(days=wd - 4) if wd >= 4 else today + dt.timedelta(days=4 - wd)
    return fri, fri + dt.timedelta(days=2)


def fetch_posts(after):
    posts, page = [], 1
    while True:
        q = urllib.parse.urlencode({"categories": AGENDA_CAT, "per_page": 100, "page": page,
                                    "after": after.isoformat() + "T00:00:00", "_embed": "wp:featuredmedia",
                                    "_fields": "id,date,link,title,excerpt,content,_links,_embedded"})
        try:
            batch = get_json(f"{SITE}/wp-json/wp/v2/posts?{q}")
        except urllib.error.HTTPError as e:
            if e.code == 400:
                break
            raise
        posts += batch
        if len(batch) < 100:
            break
        page += 1
    return posts


def image_of(p):
    try:
        m = p["_embedded"]["wp:featuredmedia"][0]
        sizes = m.get("media_details", {}).get("sizes", {})
        for k in ("medium_large", "large", "medium"):
            if k in sizes:
                return sizes[k]["source_url"]
        return m.get("source_url")
    except (KeyError, IndexError, TypeError):
        return None


def fetch_excursions(today, horizon):
    prods = get_json(f"{SITE}/wp-json/wc/store/v1/products?category={EXCURSION_CAT}&per_page=100")
    out = []
    for p in prods:
        if not p.get("is_in_stock"):
            continue
        fechas = []
        for a in p.get("attributes", []):
            if a.get("name", "").upper() == "FECHA":
                for t in a.get("terms", []):
                    r = parse_dates(t["name"], today)
                    if r and r[0] == r[1] and today <= r[0] <= horizon:
                        fechas.append(r[0])
        if fechas:
            img = (p.get("images") or [{}])[0]
            out.append({"name": text(p["name"]).split(" — ")[0], "link": p["permalink"],
                        "price": int(p["prices"]["price"]) / (10 ** p["prices"].get("currency_minor_unit", 2)),
                        "dates": sorted(fechas), "img": img.get("thumbnail") or img.get("src")})
    return sorted(out, key=lambda x: x["dates"][0])


FREE_RE = re.compile(r"\b(gratis|gratuit[oa]s?|entrada libre|acceso libre|sin coste|de balde)\b", re.I)
AGENDA_URL = SITE + "/que-hacer-en-valencia-este-fin-de-semana/"
GRATIS_URL = SITE + "/planes-gratis-en-valencia/"
ROUNDUP_RE = re.compile(r"^(qué hacer|planes|agenda|mercados y ferias)\b", re.I)


def build(today):
    fri, sun = weekend_for(today)
    posts = fetch_posts(today - dt.timedelta(days=120))
    hoy, finde, proximos, adelante, largos, roundups = [], [], [], [], [], []
    for p in posts:
        title = text(p["title"]["rendered"])
        pub = dt.date.fromisoformat(p["date"][:10])
        ex = text(p["excerpt"]["rendered"])
        if ROUNDUP_RE.match(title):
            rr = parse_dates(title, pub)
            if (rr and rr[1] >= today) or (not rr and MESES_NOMBRE[today.month] in title.lower()):
                roundups.append({"title": title, "link": p["link"], "start": pub, "end": pub})
            continue
        r = parse_dates(title + ". " + ex, pub) or parse_dates(text(p["content"]["rendered"])[:900], pub)
        if not r:
            continue
        a, b = r
        if b < today:
            continue
        item = {"title": title, "link": p["link"], "start": a, "end": b, "img": image_of(p),
                "free": bool(FREE_RE.search(title + " " + ex)),
                "desc": (ex[:150].rsplit(" ", 1)[0] + "…") if len(ex) > 150 else ex}
        if (b - a).days > 10 and a <= today + dt.timedelta(days=14):
            largos.append(item)
        elif (b - a).days > 10:
            if a <= today + dt.timedelta(days=60):
                adelante.append(item)
        elif a <= sun and b >= fri:
            finde.append(item)
        elif a <= today < fri:
            hoy.append(item)
        elif a <= today + dt.timedelta(days=14):
            proximos.append(item)
        elif a <= today + dt.timedelta(days=60):
            adelante.append(item)
    key = lambda x: (x["start"], x["end"])
    for l in (hoy, finde, proximos, adelante):
        l.sort(key=key)
    largos.sort(key=lambda x: x["end"])
    roundups.sort(key=lambda x: x["start"], reverse=True)
    exc = fetch_excursions(today, today + dt.timedelta(days=21))
    exc_finde = [e for e in exc if any(fri <= d <= sun for d in e["dates"])]
    return dict(today=today, fri=fri, sun=sun, hoy=hoy, finde=finde, proximos=proximos, adelante=adelante,
                largos=largos, roundups=roundups, exc=exc, exc_finde=exc_finde)


def build_agenda(d):
    return render(d["today"], d["fri"], d["sun"], d["hoy"], d["finde"], d["proximos"], d["adelante"], d["largos"],
                  d["roundups"], (d["exc_finde"] or d["exc"])[:3], bool(d["exc_finde"]))


C = {"red": "#E51B4C", "ink": "#1d1d1f", "muted": "#5f6368", "line": "#ececec", "cream": "#FDFCF9"}


def card(it, label):
    img = (f'<a href="{it["link"]}" style="display:block"><img src="{html.escape(it["img"])}" alt="{html.escape(it["title"])}" '
           f'loading="lazy" style="width:100%;height:170px;object-fit:cover;border-radius:10px 10px 0 0;display:block;margin:0" /></a>') if it.get("img") else ""
    return (f'<div style="border:1px solid {C["line"]};border-radius:10px;background:#fff;overflow:hidden">{img}'
            f'<div style="padding:12px 14px 14px"><p style="margin:0 0 6px;font-size:13px;font-weight:600;color:{C["red"]};text-transform:uppercase;letter-spacing:.3px">{label}</p>'
            f'<h3 style="margin:0 0 6px;font-size:17px;line-height:1.35"><a href="{it["link"]}" style="color:{C["ink"]};text-decoration:none">{html.escape(it["title"])}</a></h3>'
            f'<p style="margin:0;font-size:14px;line-height:1.5;color:{C["muted"]}">{html.escape(it.get("desc", ""))}</p></div></div>')


def grid(items, labeler, max_cards=None):
    cards, rest = (items, []) if max_cards is None else (items[:max_cards], items[max_cards:])
    out = ('<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px;margin:0 0 8px">'
           + "".join(card(i, labeler(i)) for i in cards) + "</div>")
    if rest:
        out += (f'<p style="margin:14px 0 6px;font-weight:600">Y además:</p><ul style="margin:0 0 8px;padding-left:20px">'
                + "".join(f'<li style="margin:0 0 6px"><a href="{i["link"]}">{html.escape(i["title"])}</a> '
                          f'<span style="color:{C["muted"]};font-size:14px">· {labeler(i)}</span></li>' for i in rest) + "</ul>")
    return out


def h2(t):
    return f'<h2 style="font-size:24px;margin:36px 0 14px;line-height:1.3">{t}</h2>'


def render(today, fri, sun, hoy, finde, proximos, adelante, largos, roundups, exc, exc_is_finde):
    fin_txt = (f"del viernes {fri.day} al domingo {sun.day} de {MESES_NOMBRE[sun.month]}" if fri.month == sun.month
               else f"del viernes {fri.day} de {MESES_NOMBRE[fri.month]} al domingo {sun.day} de {MESES_NOMBRE[sun.month]}")
    parts = [f'<div data-vt-wrap="1" style="max-width:980px;margin:0 auto;padding:0 16px;font-size:17px;line-height:1.65;color:{C["ink"]}">',
             f'<p style="font-size:22px;font-weight:700;line-height:1.25;margin:0 0 8px">Qué hacer en Valencia este fin de semana: planes {fin_txt}</p>',
             f'<p style="margin:0 0 18px;font-size:14px;color:{C["muted"]}">Agenda actualizada el {fecha_larga(today)} de {today.year}. La revisamos cada día.</p>',
             '<p>Fiestas de pueblo, ferias gastronómicas, conciertos, mercados y exposiciones: aquí tienes los planes de este fin de semana en Valencia y alrededores, '
             'ordenados por fecha. Y si te apetece salir de la ciudad, abajo tienes las <a href="#excursiones">excursiones en grupo desde Valencia</a> con plazas libres.</p>',
             f'<p style="background:#FDF2F5;border-radius:8px;padding:10px 14px">💸 ¿Buscas algo sin gastar? Mira nuestra lista de <a href="{GRATIS_URL}"><strong>planes gratis en Valencia</strong></a>.</p>']
    if hoy:
        parts.append(h2(f"Hoy en Valencia ({fecha_larga(today)})"))
        parts.append(grid(hoy, lambda i: rango_txt(i["start"], i["end"]), max_cards=6))
    parts.append(h2(f"Planes este fin de semana en Valencia ({fri.day}–{sun.day} {MESES_NOMBRE[sun.month][:3]})"))
    if finde:
        parts.append(grid(finde, lambda i: rango_txt(i["start"], i["end"]), max_cards=12))
    else:
        parts.append("<p>Todavía estamos completando la agenda de este fin de semana. Mientras tanto, mira los próximos planes más abajo.</p>")
    parts.append(f'<div id="excursiones"></div>' + h2("Excursiones desde Valencia este fin de semana" if exc_is_finde else "Próximas excursiones desde Valencia"))
    if exc:
        parts.append('<p>Salimos en autobús desde Valencia (Estadio de Mestalla), con coordinador/a que habla español e inglés. Plan perfecto para conocer gente.</p>')
        parts.append(grid([{"title": e["name"], "link": e["link"], "img": e["img"], "dates": e["dates"],
                            "desc": f'{e["price"]:.0f} € · Salida en autobús desde Valencia'} for e in exc[:3]],
                          lambda i: " · ".join(fecha_larga(d) for d in i["dates"][:2])))
    parts.append(f'<p><a href="{SITE}/excursiones-fin-de-semana-desde-valencia/" style="color:{C["red"]};font-weight:600">Ver el calendario completo de excursiones →</a></p>')
    if proximos:
        parts.append(h2("Próximos días"))
        parts.append(grid(proximos, lambda i: rango_txt(i["start"], i["end"]), max_cards=9))
    if adelante:
        parts.append(h2("Más adelante"))
        parts.append(grid(adelante, lambda i: rango_txt(i["start"], i["end"]), max_cards=6))
    if largos:
        parts.append(h2("Exposiciones y eventos de varias semanas"))
        parts.append(grid(largos, lambda i: ("Hasta el " + f"{i['end'].day} de {MESES_NOMBRE[i['end'].month]}") if i["start"] <= today else rango_txt(i["start"], i["end"]), max_cards=6))
    if roundups:
        parts.append(h2("Agendas completas del mes"))
        parts.append("<ul>" + "".join(f'<li><a href="{r["link"]}">{html.escape(r["title"])}</a></li>' for r in roundups) + "</ul>")
    parts.append(h2("Preguntas frecuentes"))
    faq = [("¿Cada cuánto se actualiza esta agenda?",
            "Todos los días. Los eventos que ya han pasado desaparecen solos y los nuevos se añaden en cuanto los publicamos en nuestra <a href=\"" + SITE + "/category/agenda-valencia/\">Agenda Valencia</a>."),
           ("¿Hay excursiones desde Valencia este fin de semana?",
            "Sí, casi todos los fines de semana salimos en autobús desde Valencia a pueblos, calas, cañones y rutas de senderismo. Las tienes en la sección de excursiones de esta página. "
            f'Tienes todas las fechas en nuestro <a href="{SITE}/excursiones-fin-de-semana-desde-valencia/">calendario de excursiones desde Valencia</a>.'),
           ("¿Qué hacer en Valencia si buscas planes fuera de la ciudad?",
            "Échale un vistazo a nuestras <a href=\"" + SITE + "/category/guias-y-planes/\">guías y planes</a>: pueblos con encanto, rutas y escapadas de un día cerca de Valencia.")]
    for q, a in faq:
        parts.append(f'<h3 style="font-size:19px;margin:22px 0 6px">{q}</h3><p style="margin:0">{a}</p>')
    parts.append("</div>")
    return "\n".join(parts)


def render_gratis(d):
    today, fri, sun = d["today"], d["fri"], d["sun"]
    lab = lambda i: rango_txt(i["start"], i["end"])
    hoy_f = [i for i in d["hoy"] if i["free"]]
    finde_f = [i for i in d["finde"] if i["free"]]
    prox_f = [i for i in d["proximos"] + d["adelante"] if i["free"] and i["start"] <= today + dt.timedelta(days=45)]
    prox_f.sort(key=lambda x: (x["start"], x["end"]))
    largos_f = [i for i in d["largos"] if i["free"]]
    parts = [f'<div data-vt-wrap="1" style="max-width:980px;margin:0 auto;padding:0 16px;font-size:17px;line-height:1.65;color:{C["ink"]}">',
             f'<p style="font-size:22px;font-weight:700;line-height:1.25;margin:0 0 8px">Planes gratis en Valencia: qué hacer sin gastar en {MESES_NOMBRE[today.month]} de {today.year}</p>',
             f'<p style="margin:0 0 18px;font-size:14px;color:{C["muted"]}">Lista actualizada el {fecha_larga(today)} de {today.year}. La revisamos cada día.</p>',
             '<p>Conciertos al aire libre, fiestas de barrio, exposiciones, puertas abiertas y festivales con entrada libre: aquí reunimos los <strong>planes gratis en Valencia</strong> '
             'y alrededores que tienes por delante, ordenados por fecha. Los que ya han pasado desaparecen solos.</p>',
             f'<p style="background:#FDF2F5;border-radius:8px;padding:10px 14px">📅 ¿Quieres ver todos los planes, también los de pago? Consulta <a href="{AGENDA_URL}"><strong>qué hacer en Valencia este fin de semana</strong></a>.</p>']
    if hoy_f:
        parts.append(h2(f"Planes gratis hoy en Valencia ({fecha_larga(today)})"))
        parts.append(grid(hoy_f, lab, max_cards=6))
    parts.append(h2(f"Planes gratis este fin de semana ({fri.day}–{sun.day} {MESES_NOMBRE[sun.month][:3]})"))
    if finde_f:
        parts.append(grid(finde_f, lab, max_cards=12))
    else:
        parts.append(f'<p>De momento no tenemos planes gratuitos confirmados para este fin de semana. Echa un ojo a los <a href="{AGENDA_URL}">planes del fin de semana</a> o a los que vienen más abajo.</p>')
    if largos_f:
        parts.append(h2("Gratis durante varias semanas: exposiciones, ciclos y festivales"))
        parts.append(grid(largos_f, lambda i: ("Hasta el " + f"{i['end'].day} de {MESES_NOMBRE[i['end'].month]}") if i["start"] <= today else lab(i), max_cards=9))
    if prox_f:
        parts.append(h2("Próximos planes gratis en Valencia"))
        parts.append(grid(prox_f, lab, max_cards=9))
    cheap = sorted(d["exc"], key=lambda e: e["price"])[:3]
    if cheap:
        parts.append('<div id="excursiones"></div>' + h2(f"¿Y si sales de Valencia? Excursiones desde {min(e['price'] for e in cheap):.0f} €"))
        parts.append('<p>No son gratis, pero casi: autobús desde Valencia (Estadio de Mestalla), coordinador/a que habla español e inglés y un grupo con el que conocer gente.</p>')
        parts.append(grid([{"title": e["name"], "link": e["link"], "img": e["img"], "dates": e["dates"],
                            "desc": f'{e["price"]:.0f} € · Salida en autobús desde Valencia'} for e in cheap],
                          lambda i: " · ".join(fecha_larga(x) for x in i["dates"][:2])))
        parts.append(f'<p><a href="{SITE}/excursiones-fin-de-semana-desde-valencia/" style="color:{C["red"]};font-weight:600">Ver todas las excursiones desde Valencia →</a></p>')
    parts.append(h2("Preguntas frecuentes"))
    faq = [("¿Qué hacer gratis en Valencia?",
            "Además de los eventos de esta lista, en Valencia siempre puedes pasear por el Jardín del Turia, ver la Ciudad de las Artes y las Ciencias por fuera, "
            "recorrer el casco histórico, ir a la playa de la Malvarrosa o ver el atardecer en la Albufera. Muchos museos municipales son gratis los domingos y festivos."),
           ("¿Cada cuánto se actualiza esta lista?",
            f"Todos los días. Recogemos los eventos gratuitos que publicamos en nuestra <a href=\"{SITE}/category/agenda-valencia/\">Agenda Valencia</a> y quitamos los que ya han pasado."),
           ("¿Los planes gratis necesitan reserva?",
            "Algunos sí (sobre todo puertas abiertas y visitas guiadas). Entra en cada plan para ver los detalles, el horario y si hace falta inscribirse.")]
    for q, a in faq:
        parts.append(f'<h3 style="font-size:19px;margin:22px 0 6px">{q}</h3><p style="margin:0">{a}</p>')
    parts.append("</div>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", metavar="OUT")
    ap.add_argument("--page-id", type=int)
    ap.add_argument("--gratis-page-id", type=int)
    ap.add_argument("--today")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(TZ).date()
    data = build(today)
    agenda, gratis = build_agenda(data), render_gratis(data)
    if a.dry_run:
        open(a.dry_run, "w").write(agenda)
        open(a.dry_run.replace(".html", "") + "-gratis.html", "w").write(gratis)
        print("HTML generado:", a.dry_run, len(agenda), "+ gratis", len(gratis), "caracteres")
        return
    auth = f'{os.environ["WP_USER"]}:{os.environ["WP_APP_PASS"]}'
    for pid, body in ((a.page_id, agenda), (a.gratis_page_id, gratis)):
        if pid:
            r = get_json(f"{SITE}/wp-json/wp/v2/pages/{pid}", auth=auth, data={"content": body}, method="POST")
            print("Página actualizada:", r.get("link"), r.get("modified"))


if __name__ == "__main__":
    main()
