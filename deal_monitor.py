#!/usr/bin/env python3
"""
MEILENFLUCHT ✈️ — Auswertung und Benachrichtigung
Essen. Trinken. Status retten.

Ablauf:
    Rasterquellen (Amadeus, Duffel, Kiwi, Travelpayouts) liefern Angebote
    ->  SerpAPI prüft die günstigsten Treffer gegen Google Flights nach
    ->  filtern  ->  Lufthansa-Flugplan-Gegenprüfung
    ->  mit Historie vergleichen  ->  nur echte Verbesserungen an n8n posten

Jede Quelle ist nur aktiv, wenn ihre Zugangsdaten als Umgebungsvariable
gesetzt sind. Der Suchraum (Abflughäfen x Datumspaare) wird pro Quelle
in einem rotierenden Raster abgearbeitet, damit das jeweilige
API-Kontingent reicht.

Aufruf:  python deal_monitor.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import duffel_quelle
import flight_scraper as amadeus_quelle
import kiwi_quelle
import lufthansa_check
import serpapi_quelle
import travelpayouts_quelle

BASIS = Path(__file__).parent
CONFIG = json.loads((BASIS / "config.json").read_text(encoding="utf-8"))

# Rasterquellen; SerpAPI läuft separat als Nachprüfung, die Lufthansa
# Open API separat als Flugplan-Check (sie liefert keine Preise)
QUELLEN = {
    "amadeus": amadeus_quelle,
    "duffel": duffel_quelle,
    "kiwi": kiwi_quelle,
    "travelpayouts": travelpayouts_quelle,
}


# --------------------------------------------------------------------------
# Datenstruktur
# --------------------------------------------------------------------------

@dataclass
class Angebot:
    """Ein Hin- und Rückflug-Paar."""
    abflughafen: str
    hinflug_datum: str          # ISO, z.B. "2026-12-08"
    rueckflug_datum: str
    preis_pro_person: float     # EUR, Hin und Rück zusammen
    kabine_langstrecke: str     # "FIRST" | "BUSINESS" | ...
    umsteige_hin: int
    umsteige_rueck: int
    fluege_hin: str             # z.B. "LH991 AMS-FRA, LH572 FRA-JNB"
    fluege_rueck: str
    buchungslink: str = ""
    quelle: str = ""            # "amadeus" | "duffel" | "kiwi" | ...

    @property
    def schluessel(self) -> str:
        return f"{self.abflughafen}|{self.hinflug_datum}|{self.rueckflug_datum}"

    @property
    def aufenthalt_tage(self) -> int:
        h = date.fromisoformat(self.hinflug_datum)
        r = date.fromisoformat(self.rueckflug_datum)
        return (r - h).days

    @property
    def preis_gesamt(self) -> float:
        return self.preis_pro_person * CONFIG["reise"]["passagiere"]


# --------------------------------------------------------------------------
# Suchraum
# --------------------------------------------------------------------------

def datumspaare():
    """Alle sinnvollen, noch in der Zukunft liegenden Kombinationen."""
    z = CONFIG["zeitraum"]
    hin_von = max(date.fromisoformat(z["hinflug_von"]),
                  date.today() + timedelta(days=1))
    hin_bis = date.fromisoformat(z["hinflug_bis"])
    rueck_von = date.fromisoformat(z["rueckflug_von"])
    rueck_bis = date.fromisoformat(z["rueckflug_bis"])
    min_t, max_t = z["aufenthalt_min_tage"], z["aufenthalt_max_tage"]

    tag = hin_von
    while tag <= hin_bis:
        for dauer in range(min_t, max_t + 1):
            rueck = tag + timedelta(days=dauer)
            if rueck_von <= rueck <= rueck_bis:
                yield tag, rueck
        tag += timedelta(days=1)


def _abfragen(name, modul, auswahl) -> tuple[list[Angebot], int]:
    """Eine Quelle über eine Liste von (hafen, hin, rueck) laufen lassen.

    Liefert (Angebote, Anzahl tatsächlich abgearbeiteter Kombinationen),
    damit der Raster-Cursor bei vorzeitigem Abbruch nicht Bereiche
    überspringt.
    """
    reise = CONFIG["reise"]
    q_cfg = CONFIG["quellen"]
    ergebnisse: list[Angebot] = []

    abgearbeitet = 0
    fehler_in_folge = 0
    for hafen, hin, rueck in auswahl:
        try:
            treffer = modul.suche_fluege(
                hafen, reise["ziel"], hin, rueck,
                kabine=reise["kabine"],
                passagiere=reise["passagiere"],
                airlines=q_cfg["airlines"],
                max_angebote=q_cfg["max_angebote_pro_abfrage"],
            )
            fehler_in_folge = 0
            abgearbeitet += 1
        except modul.KontingentErschoepft:
            print(f"Quelle {name}: Kontingent erschöpft nach {abgearbeitet} "
                  "Abfragen — Rest wird beim nächsten Lauf nachgeholt.",
                  file=sys.stderr)
            break
        except modul.SucheFehler as e:
            abgearbeitet += 1
            fehler_in_folge += 1
            print(f"Quelle {name}: Abfrage übersprungen: {e}", file=sys.stderr)
            if fehler_in_folge >= 5:
                print(f"Quelle {name}: {fehler_in_folge} Fehler in Folge "
                      "— Quelle wird für heute aufgegeben.", file=sys.stderr)
                break
            continue

        for t in treffer:
            ergebnisse.append(Angebot(
                abflughafen=hafen,
                hinflug_datum=hin.isoformat(),
                rueckflug_datum=rueck.isoformat(),
                preis_pro_person=t["preis"],
                kabine_langstrecke=t["kabine"],
                umsteige_hin=t["stops_hin"],
                umsteige_rueck=t["stops_rueck"],
                fluege_hin=t["segmente_hin"],
                fluege_rueck=t["segmente_rueck"],
                buchungslink=t.get("link", ""),
                quelle=name,
            ))
    return ergebnisse, abgearbeitet


def _fenster(kombis, start: int, budget: int):
    """Raster-Fenster ab Position start (ohne den Cursor zu bewegen)."""
    breite = min(budget, len(kombis))
    return [kombis[(start + i) % len(kombis)] for i in range(breite)]


def angebote_holen(hist: dict) -> list[Angebot]:
    """Alle aktiven Rasterquellen abfragen, dann SerpAPI-Nachprüfung.

    Jede Quelle merkt sich ihre Position im Suchraster (Cursor in der
    Historie) — jeder Lauf scannt also ein neues Fenster, auch von Hand
    gestartete Zusatzläufe bringen frische Daten.
    """
    q_cfg = CONFIG["quellen"]

    kombis = [
        (hafen, hin, rueck)
        for hafen in CONFIG["abflughaefen"]
        for hin, rueck in datumspaare()
    ]
    if not kombis:
        print("Keine zukünftigen Datumspaare mehr im Suchzeitraum.")
        return []

    cursor = hist.setdefault("raster_cursor", {})
    ergebnisse: list[Angebot] = []

    for name, modul in QUELLEN.items():
        if not modul.verfuegbar():
            print(f"Quelle {name}: Zugangsdaten nicht gesetzt — übersprungen.")
            continue

        budget = q_cfg["api_calls_pro_lauf"].get(name, 50)
        start = cursor.get(name, 0) % len(kombis)
        auswahl = _fenster(kombis, start, budget)
        print(f"Quelle {name}: Fenster ab Position {start}, "
              f"{len(auswahl)} von {len(kombis)} Kombinationen")
        treffer, abgearbeitet = _abfragen(name, modul, auswahl)
        cursor[name] = (start + abgearbeitet) % len(kombis)
        ergebnisse += treffer

    ergebnisse += _nachpruefung_serpapi(kombis, cursor, ergebnisse)
    return ergebnisse


def _nachpruefung_serpapi(kombis, cursor, bisher) -> list[Angebot]:
    """Die günstigsten Treffer der anderen Quellen bei Google Flights prüfen."""
    if not serpapi_quelle.verfuegbar():
        print("Quelle serpapi: Zugangsdaten nicht gesetzt — übersprungen.")
        return []

    budget = CONFIG["quellen"]["api_calls_pro_lauf"].get("serpapi", 3)

    kandidaten, gesehen = [], set()
    for a in sorted((x for x in bisher if passt(x)),
                    key=lambda x: x.preis_pro_person):
        k = (a.abflughafen,
             date.fromisoformat(a.hinflug_datum),
             date.fromisoformat(a.rueckflug_datum))
        if k not in gesehen:
            gesehen.add(k)
            kandidaten.append(k)
        if len(kandidaten) >= budget:
            break

    if not kandidaten:
        # nichts nachzuprüfen — Budget in das Raster stecken
        start = cursor.get("serpapi", 0) % len(kombis)
        kandidaten = _fenster(kombis, start, budget)
        print(f"Quelle serpapi: keine Kandidaten, stattdessen "
              f"{len(kandidaten)} Raster-Abfragen.")
        treffer, abgearbeitet = _abfragen("serpapi", serpapi_quelle, kandidaten)
        cursor["serpapi"] = (start + abgearbeitet) % len(kombis)
        return treffer

    print(f"Quelle serpapi: prüft {len(kandidaten)} Top-Treffer nach.")
    treffer, _ = _abfragen("serpapi", serpapi_quelle, kandidaten)
    return treffer


# --------------------------------------------------------------------------
# Filter
# --------------------------------------------------------------------------

def passt(a: Angebot) -> bool:
    f = CONFIG["filter"]
    if f["first_auf_langstrecke_pflicht"] and a.kabine_langstrecke != "FIRST":
        return False
    if a.umsteige_hin > f["max_umsteige_hinflug"]:
        return False
    if a.umsteige_rueck > f["max_umsteige_rueckflug"]:
        return False
    if a.preis_pro_person > CONFIG["schwellwerte"]["alarm_ab"]:
        return False
    return True


# --------------------------------------------------------------------------
# Website-Daten — bestes Angebot je Strecke (Abflughafen + Via)
# --------------------------------------------------------------------------

WEB_DATEI = "top_angebote.json"


def _qualitaet(a: Angebot) -> bool:
    """Wie passt(), aber ohne Preisschwelle — für den Marktüberblick."""
    f = CONFIG["filter"]
    if f["first_auf_langstrecke_pflicht"] and a.kabine_langstrecke != "FIRST":
        return False
    return (a.umsteige_hin <= f["max_umsteige_hinflug"]
            and a.umsteige_rueck <= f["max_umsteige_rueckflug"])


def _via(a: Angebot) -> str:
    """Umsteigeort aus dem ersten Hinflug-Segment ziehen."""
    erster = a.fluege_hin.split(",")[0]
    m = re.search(r"([A-Z]{3})-([A-Z]{3})", erster)
    if not m or m.group(2) == CONFIG["reise"]["ziel"]:
        return "Direkt"
    return m.group(2)


def webseite_daten_schreiben(alle: list[Angebot]) -> None:
    """Rollierender Bestand für den Flugvergleich auf meilenflucht.de."""
    p = BASIS / WEB_DATEI
    bestand: dict = {}
    if p.exists():
        for e in json.loads(p.read_text(encoding="utf-8")).get("angebote", []):
            bestand[(e["abflughafen"], e["via"])] = e

    heute = date.today()
    for a in alle:
        if not _qualitaet(a):
            continue
        e = {
            **asdict(a),
            "via": _via(a),
            "aufenthalt_tage": a.aufenthalt_tage,
            "preis_gesamt": a.preis_gesamt,
            "stand": heute.isoformat(),
        }
        k = (e["abflughafen"], e["via"])
        alt = bestand.get(k)
        if alt is None or e["preis_pro_person"] < alt["preis_pro_person"]:
            bestand[k] = e

    frisch = [
        e for e in bestand.values()
        if date.fromisoformat(e["hinflug_datum"]) >= heute
        and (heute - date.fromisoformat(e.get("stand", "1970-01-01"))).days <= 14
    ]
    frisch.sort(key=lambda e: e["preis_pro_person"])
    p.write_text(json.dumps(
        {"erzeugt_am": datetime.now().isoformat(timespec="seconds"),
         "angebote": frisch[:12]},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Website-Daten: {len(frisch[:12])} Strecken in {WEB_DATEI}")


# --------------------------------------------------------------------------
# Historie — verhindert, dass täglich dieselbe Mail kommt
# --------------------------------------------------------------------------

def historie_laden() -> dict:
    p = BASIS / CONFIG["state_datei"]
    if not p.exists():
        return {"beste_preise": {}, "letzter_lauf": None}
    return json.loads(p.read_text(encoding="utf-8"))


def historie_speichern(h: dict) -> None:
    h["letzter_lauf"] = datetime.now().isoformat(timespec="seconds")
    (BASIS / CONFIG["state_datei"]).write_text(
        json.dumps(h, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def ist_meldenswert(a: Angebot, hist: dict) -> bool:
    """Nur melden, wenn spürbar besser als der bisher beste Preis."""
    s = CONFIG["schwellwerte"]
    if not s["nur_besser_als_letzter_alarm"]:
        return True

    bisher = hist["beste_preise"].get(a.schluessel)
    if bisher is None:
        return True
    return a.preis_pro_person <= bisher - s["mindestverbesserung_eur"]


# --------------------------------------------------------------------------
# Versand an n8n
# --------------------------------------------------------------------------

def an_n8n(treffer: list[Angebot], flugplan: dict[str, bool | None]) -> None:
    url = os.environ.get(CONFIG["n8n"]["webhook_url_env"])
    secret = os.environ.get(CONFIG["n8n"]["shared_secret_env"], "")

    if not url:
        print("FEHLER: N8N_WEBHOOK_URL nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    sofort = CONFIG["schwellwerte"]["sofort_buchen"]
    bester = min(treffer, key=lambda a: a.preis_pro_person)

    nutzlast = {
        "betreff": _betreff(bester, sofort),
        "anzahl_treffer": len(treffer),
        "bester_preis_pro_person": bester.preis_pro_person,
        "bester_preis_gesamt": bester.preis_gesamt,
        "passagiere": CONFIG["reise"]["passagiere"],
        "dringend": bester.preis_pro_person <= sofort,
        "angebote": [
            {
                **asdict(a),
                "aufenthalt_tage": a.aufenthalt_tage,
                "preis_gesamt": a.preis_gesamt,
                # True = Langstrecke im LH-Flugplan bestätigt,
                # False = nicht gefunden, null = nicht geprüft
                "flugplan_bestaetigt": flugplan.get(a.schluessel),
            }
            for a in sorted(treffer, key=lambda x: x.preis_pro_person)[:15]
        ],
        "erzeugt_am": datetime.now().isoformat(timespec="seconds"),
    }

    r = requests.post(
        url,
        json=nutzlast,
        headers={"X-Monitor-Secret": secret},
        timeout=30,
    )
    r.raise_for_status()
    print(f"An n8n gesendet: {len(treffer)} Treffer, bester "
          f"{bester.preis_pro_person:.0f} EUR p.P. ab {bester.abflughafen}")


def _betreff(a: Angebot, sofort: float) -> str:
    marke = "SOFORT PRUEFEN" if a.preis_pro_person <= sofort else "Preisalarm"
    return (f"MEILENFLUCHT ✈️ {marke}: JNB First ab {a.abflughafen} — "
            f"{a.preis_pro_person:.0f} EUR p.P. "
            f"({a.hinflug_datum} bis {a.rueckflug_datum})")


# --------------------------------------------------------------------------

def main() -> None:
    hist = historie_laden()

    alle = angebote_holen(hist)
    print(f"{len(alle)} Angebote geprüft")
    webseite_daten_schreiben(alle)

    passende = [a for a in alle if passt(a)]
    print(f"{len(passende)} unter Schwellwert "
          f"{CONFIG['schwellwerte']['alarm_ab']} EUR")

    neu = [a for a in passende if ist_meldenswert(a, hist)]

    # Historie immer fortschreiben, auch ohne Versand
    for a in passende:
        b = hist["beste_preise"].get(a.schluessel)
        if b is None or a.preis_pro_person < b:
            hist["beste_preise"][a.schluessel] = a.preis_pro_person

    if neu:
        flugplan: dict[str, bool | None] = {}
        if lufthansa_check.verfuegbar():
            flugplan = lufthansa_check.pruefe(neu, CONFIG["reise"]["ziel"])
            bestaetigt = sum(1 for v in flugplan.values() if v)
            print(f"LH-Flugplan-Check: {bestaetigt}/{len(neu)} bestätigt")
        else:
            print("LH-Flugplan-Check: Zugangsdaten nicht gesetzt — "
                  "übersprungen.")
        an_n8n(neu, flugplan)
    else:
        print("Nichts Neues — keine Mail.")

    historie_speichern(hist)


if __name__ == "__main__":
    main()
