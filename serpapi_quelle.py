#!/usr/bin/env python3
"""
Quelle SerpAPI — Google-Flights-Preise zur Nachprüfung.

Mit 100 Gratis-Suchen pro Monat ist SerpAPI zu knapp für das volle
Raster. deal_monitor setzt diese Quelle deshalb gezielt ein: Sie prüft
die günstigsten Treffer der anderen Quellen nach (Standard: 3 pro Lauf).

Die erste Google-Flights-Antwort enthält Hinflug-Details und den
Gesamtpreis für Hin und Rück; die Rückflug-Segmente stecken erst hinter
einem Folge-Call. Um Calls zu sparen, wird umsteige_rueck als 0
gemeldet und auf den Link verwiesen.

Benötigte Umgebungsvariable:
    SERPAPI_KEY
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_URL = "https://serpapi.com/search.json"

_TRAVEL_CLASS = {"ECONOMY": 1, "PREMIUM_ECONOMY": 2, "BUSINESS": 3, "FIRST": 4}


class SucheFehler(RuntimeError):
    pass


class KontingentErschoepft(SucheFehler):
    """HTTP 429 — Monatskontingent oder Ratenlimit erreicht."""


def verfuegbar() -> bool:
    return bool(os.environ.get("SERPAPI_KEY"))


def suche_fluege(abflug, ziel, hin, rueck, *, kabine="FIRST", passagiere=2,
                 airlines=("LH",), max_angebote=5) -> list[dict]:
    """Ein API-Call pro Datumspaar; gleiche Rückgabe wie die anderen Quellen."""
    params = {
        "engine": "google_flights",
        "departure_id": abflug,
        "arrival_id": ziel,
        "outbound_date": hin.isoformat(),
        "return_date": rueck.isoformat(),
        "type": "1",
        "adults": passagiere,
        "travel_class": _TRAVEL_CLASS.get(kabine, 4),
        "currency": "EUR",
        "hl": "de",
        "api_key": os.environ["SERPAPI_KEY"],
    }
    if airlines:
        params["include_airlines"] = ",".join(airlines)

    r = requests.get(_URL, params=params, timeout=60)
    if r.status_code == 429:
        raise KontingentErschoepft(r.text[:300])
    if r.status_code != 200:
        raise SucheFehler(f"{abflug}→{ziel} {hin} ({r.status_code}): {r.text[:300]}")

    daten = r.json()
    if daten.get("error"):
        raise SucheFehler(f"{abflug}→{ziel} {hin}: {daten['error']}")

    link = daten.get("search_metadata", {}).get("google_flights_url", "")
    kandidaten = (daten.get("best_flights", []) + daten.get("other_flights", []))

    ergebnisse = []
    for angebot in kandidaten[:max_angebote]:
        try:
            treffer = _auswerten(angebot, ziel, passagiere, airlines, link)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.warning("Angebot übersprungen (%s %s): %s", abflug, hin, e)
            continue
        if treffer:
            ergebnisse.append(treffer)
    return ergebnisse


def _kabine(text: str) -> str:
    t = text.lower()
    if "first" in t:
        return "FIRST"
    if "business" in t:
        return "BUSINESS"
    if "premium" in t:
        return "PREMIUM_ECONOMY"
    if "economy" in t:
        return "ECONOMY"
    return ""


def _auswerten(angebot, ziel, passagiere, airlines, link) -> dict | None:
    if "price" not in angebot:
        return None
    fluege = angebot["flights"]

    langstrecke = next(
        (f for f in fluege if f["arrival_airport"]["id"] == ziel), fluege[-1]
    )
    carrier = langstrecke.get("flight_number", "").split(" ")[0]
    if airlines and carrier not in airlines:
        return None

    return {
        # Google Flights nennt den Gesamtpreis für alle Passagiere
        "preis": float(angebot["price"]) / passagiere,
        "kabine": _kabine(langstrecke.get("travel_class", "")),
        "stops_hin": len(fluege) - 1,
        "stops_rueck": 0,  # erst im Folge-Call sichtbar, siehe Docstring
        "segmente_hin": ", ".join(
            f"{f.get('flight_number', '?')} "
            f"{f['departure_airport']['id']}-{f['arrival_airport']['id']}"
            for f in fluege
        ),
        "segmente_rueck": "Rückflug: siehe Google-Flights-Link",
        "link": link,
    }
