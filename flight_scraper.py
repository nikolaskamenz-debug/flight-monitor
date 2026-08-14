#!/usr/bin/env python3
"""
Quelle Amadeus — Flugsuche über die Amadeus Self-Service API
(Flight Offers Search).

Ersetzt den früheren Mock, der Zufallspreise erzeugte. Jeder Aufruf von
suche_fluege() kostet genau einen API-Call — das Tageskontingent
verwaltet der Aufrufer (deal_monitor.py).

Benötigte Umgebungsvariablen:
    AMADEUS_CLIENT_ID       API-Key aus dem Amadeus-Entwicklerkonto
    AMADEUS_CLIENT_SECRET   zugehöriges Secret
    AMADEUS_ENV             "test" (Standard) oder "production"
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

_BASIS = {
    "test": "https://test.api.amadeus.com",
    "production": "https://api.amadeus.com",
}


class SucheFehler(RuntimeError):
    pass


class KontingentErschoepft(SucheFehler):
    """HTTP 429 — Raten- oder Monatslimit erreicht."""


def verfuegbar() -> bool:
    return bool(os.environ.get("AMADEUS_CLIENT_ID")
                and os.environ.get("AMADEUS_CLIENT_SECRET"))


_token: str | None = None
_token_gueltig_bis: float = 0.0


def _basis_url() -> str:
    umgebung = os.environ.get("AMADEUS_ENV", "test")
    if umgebung not in _BASIS:
        raise SucheFehler(
            f"AMADEUS_ENV muss 'test' oder 'production' sein, nicht {umgebung!r}"
        )
    return _BASIS[umgebung]


def _zugangstoken() -> str:
    global _token, _token_gueltig_bis
    if _token and time.monotonic() < _token_gueltig_bis:
        return _token

    r = requests.post(
        f"{_basis_url()}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["AMADEUS_CLIENT_ID"],
            "client_secret": os.environ["AMADEUS_CLIENT_SECRET"],
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise SucheFehler(
            f"Token-Anfrage fehlgeschlagen ({r.status_code}): {r.text[:300]}"
        )
    antwort = r.json()
    _token = antwort["access_token"]
    # 60 s Puffer vor dem tatsächlichen Ablauf
    _token_gueltig_bis = time.monotonic() + int(antwort.get("expires_in", 1800)) - 60
    return _token


def suche_fluege(abflug, ziel, hin, rueck, *, kabine="FIRST", passagiere=2,
                 airlines=("LH",), max_angebote=5) -> list[dict]:
    """
    Ein API-Call: Hin-/Rückflug-Angebote für ein konkretes Datumspaar.

    hin/rueck sind datetime.date. Liefert Dicts mit den Feldern, die
    deal_monitor.angebote_holen() erwartet: preis (EUR pro Person),
    kabine (auf der Langstrecke), stops_hin, stops_rueck,
    segmente_hin, segmente_rueck, link.
    """
    params = {
        "originLocationCode": abflug,
        "destinationLocationCode": ziel,
        "departureDate": hin.isoformat(),
        "returnDate": rueck.isoformat(),
        "adults": passagiere,
        "travelClass": kabine,
        "currencyCode": "EUR",
        "max": max_angebote,
    }
    if airlines:
        params["includedAirlineCodes"] = ",".join(airlines)

    r = requests.get(
        f"{_basis_url()}/v2/shopping/flight-offers",
        params=params,
        headers={"Authorization": f"Bearer {_zugangstoken()}"},
        timeout=60,
    )
    if r.status_code == 429:
        raise KontingentErschoepft(r.text[:300])
    if r.status_code != 200:
        raise SucheFehler(f"{abflug}→{ziel} {hin} ({r.status_code}): {r.text[:300]}")

    ergebnisse = []
    for angebot in r.json().get("data", []):
        try:
            ergebnisse.append(_auswerten(angebot, abflug, ziel, hin, rueck, passagiere))
        except (KeyError, IndexError, ValueError) as e:
            logger.warning("Angebot übersprungen (%s %s): %s", abflug, hin, e)
    return ergebnisse


def _auswerten(angebot, abflug, ziel, hin, rueck, passagiere) -> dict:
    hinreise, rueckreise = angebot["itineraries"][0], angebot["itineraries"][1]

    # Kabine je Segment steht in den travelerPricings, nicht am Segment selbst
    kabinen = {
        fd["segmentId"]: fd.get("cabin", "")
        for tp in angebot.get("travelerPricings", [])[:1]
        for fd in tp.get("fareDetailsBySegment", [])
    }
    # Langstrecke = das Hinflug-Segment, das am Ziel ankommt
    langstrecke = next(
        (s for s in hinreise["segments"] if s["arrival"]["iataCode"] == ziel),
        hinreise["segments"][-1],
    )

    def segmente(reise):
        return ", ".join(
            f"{s['carrierCode']}{s['number']} "
            f"{s['departure']['iataCode']}-{s['arrival']['iataCode']}"
            for s in reise["segments"]
        )

    return {
        "preis": float(angebot["price"]["grandTotal"]) / passagiere,
        "kabine": kabinen.get(langstrecke["id"], ""),
        "stops_hin": len(hinreise["segments"]) - 1,
        "stops_rueck": len(rueckreise["segments"]) - 1,
        "segmente_hin": segmente(hinreise),
        "segmente_rueck": segmente(rueckreise),
        "link": "https://www.google.com/travel/flights?q=" + quote_plus(
            f"{abflug} nach {ziel} {hin.isoformat()} bis {rueck.isoformat()} First Class"
        ),
    }
