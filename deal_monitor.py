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


def _abfragen(name, modul, auswahl) -> list[Angebot]:
    """Eine Quelle über eine Liste von (hafen, hin, rueck) laufen lassen."""
    reise = CONFIG["reise"]
    q_cfg = CONFIG["quellen"]
    ergebnisse: list[Angebot] = []

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
        except modul.KontingentErschoepft:
            print(f"Quelle {name}: Kontingent erschöpft — "
                  "Rest entfällt für heute.", file=sys.stderr)
            break
        except modul.SucheFehler as e:
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
    return ergebnisse


def angebote_holen() -> list[Angebot]:
    """Alle aktiven Rasterquellen abfragen, dann SerpAPI-Nachprüfung."""
    q_cfg = CONFIG["quellen"]

    kombis = [
        (hafen, hin, rueck)
        for hafen in CONFIG["abflughaefen"]
        for hin, rueck in datumspaare()
    ]
    if not kombis:
        print("Keine zukünftigen Datumspaare mehr im Suchzeitraum.")
        return []

    heute = date.today().toordinal()
    ergebnisse: list[Angebot] = []

    for name, modul in QUELLEN.items():
        if not modul.verfuegbar():
            print(f"Quelle {name}: Zugangsdaten nicht gesetzt — übersprungen.")
            continue

        budget = q_cfg["api_calls_pro_lauf"].get(name, 50)
        zyklus = max(1, -(-len(kombis) // budget))  # ceil
        auswahl = kombis[heute % zyklus::zyklus]
        print(f"Quelle {name}: {len(auswahl)} von {len(kombis)} Kombinationen "
              f"heute an der Reihe (voller Durchlauf alle {zyklus} Tage)")
        ergebnisse += _abfragen(name, modul, auswahl)

    ergebnisse += _nachpruefung_serpapi(kombis, ergebnisse)
    return ergebnisse


def _nachpruefung_serpapi(kombis, bisher) -> list[Angebot]:
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
        # nichts nachzuprüfen — Budget in das rotierende Raster stecken
        zyklus = max(1, -(-len(kombis) // budget))
        kandidaten = kombis[date.today().toordinal() % zyklus::zyklus]
        print(f"Quelle serpapi: keine Kandidaten, stattdessen "
              f"{len(kandidaten)} Raster-Abfragen.")
    else:
        print(f"Quelle serpapi: prüft {len(kandidaten)} Top-Treffer nach.")

    return _abfragen("serpapi", serpapi_quelle, kandidaten)


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

    alle = angebote_holen()
    print(f"{len(alle)} Angebote geprüft")

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
