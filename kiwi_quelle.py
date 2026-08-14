#!/usr/bin/env python3
"""
Quelle Kiwi — Suche über die Kiwi Tequila API.

Achtung: Kiwi nimmt derzeit keine neuen Tequila-Registrierungen an.
Ohne bestehenden API-Key bleibt diese Quelle inaktiv und wird vom
deal_monitor automatisch übersprungen.

Benötigte Umgebungsvariable:
    KIWI_API_KEY
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_URL = "https://api.tequila.kiwi.com/v2/search"

_KABINEN_HIN = {"FIRST": "F", "BUSINESS": "C", "PREMIUM_ECONOMY": "W", "ECONOMY": "M"}
_KABINEN_RUECK = {v: k for k, v in _KABINEN_HIN.items()}


class SucheFehler(RuntimeError):
    pass


class KontingentErschoepft(SucheFehler):
    """HTTP 429 — Ratenlimit erreicht."""


def verfuegbar() -> bool:
    return bool(os.environ.get("KIWI_API_KEY"))


def _kiwi_datum(d) -> str:
    return d.strftime("%d/%m/%Y")


def suche_fluege(abflug, ziel, hin, rueck, *, kabine="FIRST", passagiere=2,
                 airlines=("LH",), max_angebote=5) -> list[dict]:
    """Ein API-Call pro Datumspaar; gleiche Rückgabe wie die anderen Quellen."""
    params = {
        "fly_from": abflug,
        "fly_to": ziel,
        "date_from": _kiwi_datum(hin),
        "date_to": _kiwi_datum(hin),
        "return_from": _kiwi_datum(rueck),
        "return_to": _kiwi_datum(rueck),
        "adults": passagiere,
        "selected_cabins": _KABINEN_HIN.get(kabine, "F"),
        "curr": "EUR",
        "limit": max_angebote,
    }
    if airlines:
        params["select_airlines"] = ",".join(airlines)

    r = requests.get(
        _URL,
        params=params,
        headers={"apikey": os.environ["KIWI_API_KEY"]},
        timeout=60,
    )
    if r.status_code == 429:
        raise KontingentErschoepft(r.text[:300])
    if r.status_code != 200:
        raise SucheFehler(f"{abflug}→{ziel} {hin} ({r.status_code}): {r.text[:300]}")

    ergebnisse = []
    for angebot in r.json().get("data", []):
        try:
            ergebnisse.append(_auswerten(angebot, ziel, passagiere))
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as e:
            logger.warning("Angebot übersprungen (%s %s): %s", abflug, hin, e)
    return ergebnisse


def _auswerten(angebot, ziel, passagiere) -> dict:
    hin_legs = [l for l in angebot["route"] if l.get("return") == 0]
    rueck_legs = [l for l in angebot["route"] if l.get("return") == 1]

    langstrecke = next(
        (l for l in hin_legs if l["flyTo"] == ziel), hin_legs[-1]
    )

    def segmente(legs):
        return ", ".join(
            f"{l['airline']}{l['flight_no']} {l['flyFrom']}-{l['flyTo']}"
            for l in legs
        )

    return {
        # Kiwi liefert den Gesamtpreis für alle Passagiere
        "preis": float(angebot["price"]) / passagiere,
        "kabine": _KABINEN_RUECK.get(langstrecke.get("fare_category", ""), ""),
        "stops_hin": len(hin_legs) - 1,
        "stops_rueck": len(rueck_legs) - 1,
        "segmente_hin": segmente(hin_legs),
        "segmente_rueck": segmente(rueck_legs),
        "link": angebot.get("deep_link", ""),
    }
