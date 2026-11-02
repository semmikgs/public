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
        return
    neu["stand"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(ZIEL, "w", encoding="utf-8") as f:
        json.dump(neu, f, ensure_ascii=False, indent=1)
    print("daten.json aktualisiert.")


if __name__ == "__main__":
    main()
