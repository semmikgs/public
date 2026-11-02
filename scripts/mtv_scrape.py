#!/usr/bin/env python3
"""Liest Tabelle, Spielplan und Hallen der MTV-mD-Staffel von nuLiga und schreibt mtv/daten.json.

Wird von der GitHub Action .github/workflows/mtv.yml alle 30 Minuten ausgeführt.
"""
import json, os, re, sys, datetime
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIEL = os.path.join(ROOT, "mtv", "daten.json")

TEAM = "MTV Schwarmstedt"
CHAMP = "HRLS 26/27"
GROUP = "489105"
BASIS = "https://hvnb-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/"
URL_TABELLE = f"{BASIS}groupPage?championship={quote(CHAMP)}&group={GROUP}"
URL_PLAN = f"{BASIS}groupPage?displayTyp=vorrunde&displayDetail=meetings&championship={quote(CHAMP)}&group={GROUP}"

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (MTV-mD-Infoseite)"


def seite(url):
    r = S.get(url, timeout=30)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def t(el):
    return re.sub(r"\s+", " ", el.get_text(" ") if el else "").strip()


def wir(name):
    return TEAM.lower() in (name or "").lower()


def tabelle(doc):
    zeilen = []
    for tr in doc.find_all("tr"):
        tds = tr.find_all("td")
        m = next((i for i, td in enumerate(tds) if td.select_one('a[href*="teamPortrait"]')), -1)
        if m < 1 or len(tds) < m + 8:
            continue
        rang = t(tds[m - 1])
        if not rang.isdigit():
            continue
        zeilen.append({
            "rang": int(rang), "team": t(tds[m]), "spiele": t(tds[m + 1]),
            "s": t(tds[m + 2]), "u": t(tds[m + 3]), "n": t(tds[m + 4]),
            "tore": t(tds[m + 5]), "diff": t(tds[m + 6]), "punkte": t(tds[m + 7]),
        })
    return zeilen


def spielplan(doc):
    spiele, datum = [], ""
    for tr in doc.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        texte = [t(td) for td in tds]
        for x in texte:
            if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", x):
                datum = x
                break
        i_zeit = next((i for i, x in enumerate(texte) if re.match(r"^\d{1,2}:\d{2}\b", x)), -1)
        if i_zeit < 0 or not datum:
            continue
        i_ort = next((i for i, td in enumerate(tds) if td.select_one('a[href*="courtInfo"]')), i_zeit + 1)
        if len(texte) < i_ort + 4:
            continue
        heim, gast = texte[i_ort + 2], texte[i_ort + 3]
        if not heim or not gast:
            continue
        erg = texte[i_ort + 4] if len(texte) > i_ort + 4 else ""
        link = tds[i_ort].find("a")
        loc = ""
        if link and link.get("href"):
            m = re.search(r"location=(\d+)", link["href"])
            loc = m.group(1) if m else ""
        spiele.append({
            "datum": datum,
            "zeit": re.match(r"\d{1,2}:\d{2}", texte[i_zeit]).group(0),
            "nr": texte[i_ort + 1],
            "hallenNr": texte[i_ort],
            "hallenTitel": (link.get("title") or "") if link else "",
            "location": loc,
            "heim": heim, "gast": gast,
            "erg": erg if re.fullmatch(r"\d+:\d+", erg) else "",
        })
    return spiele


def halle(loc):
    doc = seite(f"{BASIS}courtInfo?federation=HVNB&roundTyp=0&championship={quote(CHAMP)}&location={loc}")
    name = re.sub(r"\s*Hallenspielplan\s*$", "", t(doc.find("h1")), flags=re.I)
    name = re.sub(r"\s*\(\d+\)\s*$", "", name)
    zeilen = []
    h = next((x for x in doc.find_all(["h2", "h3"]) if "hallenadresse" in x.get_text().lower()), None)
    if h:
        teile = []
        for sib in h.next_siblings:
            if getattr(sib, "name", None) in ("h1", "h2", "h3"):
                break
            teile.append(sib.get_text("\n") if hasattr(sib, "get_text") else str(sib))
        zeilen = [re.sub(r"\s+", " ", z).strip() for z in "\n".join(teile).split("\n")]
        zeilen = [z for z in zeilen if z]
    i = next((i for i, z in enumerate(zeilen) if re.search(r"\b\d{5}\b", z)), -1)
    if i >= 0:
        plz = zeilen[i]
        if re.fullmatch(r"\d{5}", plz) and i + 1 < len(zeilen):
            plz += " " + zeilen[i + 1]
        adresse = ", ".join(zeilen[max(0, i - 1): i] + [plz])
    else:
        adresse = ", ".join(zeilen[:2])
    print(f"  Halle {loc}: {name} | {adresse}")
    return {"name": name, "adresse": adresse}


def ics_text(t):
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def kurz_halle(name):
    teil = name.split(",", 1)[1].strip() if "," in name else name
    return re.sub(r"\bSH\b", "Sporthalle", teil) or name


def kalender(daten):
    """Schreibt mtv/kalender/spiel-<nr>.ics je MTV-Spiel und mtv/kalender/alle.ics (abonnierbar)."""
    ordner = os.path.join(ROOT, "mtv", "kalender")
    os.makedirs(ordner, exist_ok=True)
    stamp = re.sub(r"[-:]", "", daten["stand"][:19]) + "Z"
    events = {}
    for sp in daten["spiele"]:
        if not (wir(sp["heim"]) or wir(sp["gast"])):
            continue
        t, m, j = map(int, sp["datum"].split("."))
        h, mi = map(int, sp["zeit"].split(":"))
        start = datetime.datetime(j, m, t, h, mi)
        ende = start + datetime.timedelta(minutes=90)
        hal = daten["hallen"].get(sp["location"], {})
        name = kurz_halle(hal.get("name", "")) or f"Halle {sp['hallenNr']}"
        ort = name + (", " + hal["adresse"] if hal.get("adresse") else "")
        titel = f"Handball mD: {sp['heim']} – {sp['gast']}"
        if sp["erg"]:
            titel += f" ({sp['erg']})"
        beschr = f"Anpfiff {sp['zeit']} Uhr\nRegionsliga D Süd, Spiel {sp['nr']}\nInfos: https://semmikgs.github.io/public/mtv/"
        f = "%Y%m%dT%H%M%S"
        events[sp["nr"]] = [
            "BEGIN:VEVENT",
            f"UID:mtv-md-2627-spiel-{sp['nr']}@semmikgs.github.io",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Europe/Berlin:{start.strftime(f)}",
            f"DTEND;TZID=Europe/Berlin:{ende.strftime(f)}",
            f"SUMMARY:{ics_text(titel)}",
            f"LOCATION:{ics_text(ort)}",
            f"DESCRIPTION:{ics_text(beschr)}",
            "END:VEVENT",
        ]
    tz = ["BEGIN:VTIMEZONE", "TZID:Europe/Berlin",
          "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST",
          "DTSTART:19700329T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
          "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET",
          "DTSTART:19701025T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
          "END:VTIMEZONE"]

    def datei(name, evs, titel):
        zeilen = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MTV Schwarmstedt mD//DE",
                  "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{titel}",
                  "X-WR-TIMEZONE:Europe/Berlin", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"] + tz
        for e in evs:
            zeilen += e
        zeilen.append("END:VCALENDAR")
        with open(os.path.join(ordner, name), "w", encoding="utf-8", newline="") as fh:
            fh.write("\r\n".join(zeilen) + "\r\n")

    for nr, ev in events.items():
        datei(f"spiel-{nr}.ics", [ev], "MTV mD")
    datei("alle.ics", list(events.values()), "MTV Schwarmstedt mD")
    for alt in os.listdir(ordner):
        m = re.fullmatch(r"spiel-(.+)\.ics", alt)
        if m and m.group(1) not in events:
            os.remove(os.path.join(ordner, alt))
    print(f"{len(events)} Kalenderdateien geschrieben.")


def main():
    alt = {}
    if os.path.exists(ZIEL):
        with open(ZIEL, encoding="utf-8") as f:
            alt = json.load(f)

    tab = tabelle(seite(URL_TABELLE))
    plan = spielplan(seite(URL_PLAN))
    print(f"Tabelle: {len(tab)} Zeilen, Spielplan: {len(plan)} Spiele")
    if not tab and not plan:
        sys.exit("Nichts gelesen – Seitenstruktur geändert?")

    hallen = dict(alt.get("hallen", {}))
    for loc in sorted({s["location"] for s in plan if s["location"] and (wir(s["heim"]) or wir(s["gast"]))}):
        if loc in hallen and re.search(r"\d{5} \S", hallen[loc].get("adresse", "")):
            continue
        try:
            hallen[loc] = halle(loc)
        except Exception as e:
            print(f"  Halle {loc} fehlgeschlagen: {e}")

    neu = {"tabelle": tab, "spiele": plan, "hallen": hallen}
    if all(alt.get(k) == v for k, v in neu.items()):
        print("Keine Änderung.")
        if not os.path.exists(os.path.join(ROOT, "mtv", "kalender", "alle.ics")):
            kalender(alt)
        return
    neu["stand"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(ZIEL, "w", encoding="utf-8") as f:
        json.dump(neu, f, ensure_ascii=False, indent=1)
    print("daten.json aktualisiert.")
    kalender(neu)


if __name__ == "__main__":
    main()
