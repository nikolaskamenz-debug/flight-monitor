#!/usr/bin/env python3
"""
Quelle Travelpayouts — gecachte Preise aus der Aviasales-Datenbank.

Kostenloser API-Key (travelpayouts.com). Die Daten stammen aus echten
Nutzersuchen und sind daher gecacht: für Nischenrouten in First Class
liefern viele Datumspaare schlicht nichts. Als kostenloses Zusatzsignal
trotzdem nützlich.

Benötigte Umgebungsvariable:
    TRAVELPAYOUTS_TOKEN
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

_TRIP_CLASS = {"ECONOMY": 0, "BUSINESS": 1, "FIRST": 2}


class SucheFehler(RuntimeError):
    pass


class KontingentErschoepft(SucheFehler):
    """HTTP 429 — Ratenlimit erreicht."""


def verfuegbar() -> bool:
    return bool(os.environ.get("TRAVELPAYOUTS_TOKEN"))


def suche_fluege(abflug, ziel, hin, rueck, *, kabine="FIRST", passagiere=2,
                 airlines=("LH",), max_angebote=5) -> list[dict]:
    """Ein API-Call pro Datumspaar; gleiche Rückgabe wie die anderen Quellen."""
    r = requests.get(
        _URL,
        params={
            "origin": abflug,
            "destination": ziel,
            "departure_at": hin.isoformat(),
            "return_at": rueck.isoformat(),
            "one_way": "false",
            "trip_class": _TRIP_CLASS.get(kabine, 2),
            "currency": "eur",
            "limit": max_angebote,
            "token": os.environ["TRAVELPAYOUTS_TOKEN"],
        },
        timeout=60,
    )
    if r.status_code == 429:
        raise KontingentErschoepft(r.text[:300])
    if r.status_code != 200:
        raise SucheFehler(f"{abflug}→{ziel} {hin} ({r.status_code}): {r.text[:300]}")

    ergebnisse = []
    for angebot in r.json().get("data", []):
        try:
            treffer = _auswerten(angebot, abflug, ziel, kabine, airlines)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Angebot übersprungen (%s %s): %s", abflug, hin, e)
            continue
        if treffer:
            ergebnisse.append(treffer)
    return ergebnisse


def _auswerten(angebot, abflug, ziel, kabine, airlines) -> dict | None:
    airline = angebot.get("airline", "")
    if airlines and airline not in airlines:
        return None

    link = angebot.get("link", "")
    return {
        # Preis gilt pro Person, Hin- und Rückflug zusammen
        "preis": float(angebot["price"]),
        # gecachte Daten tragen keine Kabine je Segment; es gilt die
        # angefragte trip_class
        "kabine": kabine,
        "stops_hin": int(angebot.get("transfers", 0)),
        "stops_rueck": int(angebot.get("return_transfers", 0)),
        "segmente_hin": f"{airline}{angebot.get('flight_number', '')} "
                        f"{abflug}-{ziel} (Details im Link)",
        "segmente_rueck": "Rückflug: Details im Link",
        "link": f"https://www.aviasales.com{link}" if link else "",
    }
