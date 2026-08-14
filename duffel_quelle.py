#!/usr/bin/env python3
"""
Quelle Duffel — echte buchbare Preise über die Duffel Air API.

Suche ist kostenlos (Fair-Use-Ratenlimit), bezahlt wird nur bei Buchung.
Test-Keys liefern Fantasiedaten; für echte Preise ist ein
verifiziertes Duffel-Konto mit Live-Key nötig.

Benötigte Umgebungsvariable:
    DUFFEL_API_KEY
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_URL = "https://api.duffel.com/air/offer_requests"

_KABINEN = {
    "first": "FIRST",
    "business": "BUSINESS",
    "premium_economy": "PREMIUM_ECONOMY",
    "economy": "ECONOMY",
}


class SucheFehler(RuntimeError):
    pass


class KontingentErschoepft(SucheFehler):
    """HTTP 429 — Ratenlimit erreicht."""


def verfuegbar() -> bool:
    return bool(os.environ.get("DUFFEL_API_KEY"))


def suche_fluege(abflug, ziel, hin, rueck, *, kabine="FIRST", passagiere=2,
                 airlines=("LH",), max_angebote=5) -> list[dict]:
    """Ein API-Call pro Datumspaar; gleiche Rückgabe wie die anderen Quellen."""
    rumpf = {
        "data": {
            "slices": [
                {"origin": abflug, "destination": ziel,
                 "departure_date": hin.isoformat()},
                {"origin": ziel, "destination": abflug,
                 "departure_date": rueck.isoformat()},
            ],
            "passengers": [{"type": "adult"}] * passagiere,
            "cabin_class": kabine.lower(),
        }
    }
    r = requests.post(
        _URL,
        params={"return_offers": "true"},
        json=rumpf,
        headers={
            "Authorization": f"Bearer {os.environ['DUFFEL_API_KEY']}",
            "Duffel-Version": "v2",
        },
        timeout=60,
    )
    if r.status_code == 429:
        raise KontingentErschoepft(r.text[:300])
    if r.status_code not in (200, 201):
        raise SucheFehler(f"{abflug}→{ziel} {hin} ({r.status_code}): {r.text[:300]}")

    ergebnisse = []
    for angebot in r.json().get("data", {}).get("offers", []):
        try:
            treffer = _auswerten(angebot, ziel, passagiere, airlines)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.warning("Angebot übersprungen (%s %s): %s", abflug, hin, e)
            continue
        if treffer:
            ergebnisse.append(treffer)
        if len(ergebnisse) >= max_angebote:
            break
    return ergebnisse


def _auswerten(angebot, ziel, passagiere, airlines) -> dict | None:
    if angebot.get("total_currency") != "EUR":
        return None

    hinreise, rueckreise = angebot["slices"][0], angebot["slices"][1]

    langstrecke = next(
        (s for s in hinreise["segments"]
         if s["destination"]["iata_code"] == ziel),
        hinreise["segments"][-1],
    )
    if airlines and langstrecke["marketing_carrier"]["iata_code"] not in airlines:
        return None

    def kabine_von(segment) -> str:
        pax = segment.get("passengers") or [{}]
        return _KABINEN.get(pax[0].get("cabin_class", ""), "")

    def segmente(reise):
        return ", ".join(
            f"{s['marketing_carrier']['iata_code']}"
            f"{s['marketing_carrier_flight_number']} "
            f"{s['origin']['iata_code']}-{s['destination']['iata_code']}"
            for s in reise["segments"]
        )

    return {
        "preis": float(angebot["total_amount"]) / passagiere,
        "kabine": kabine_von(langstrecke),
        "stops_hin": len(hinreise["segments"]) - 1,
        "stops_rueck": len(rueckreise["segments"]) - 1,
        "segmente_hin": segmente(hinreise),
        "segmente_rueck": segmente(rueckreise),
        "link": "",
    }
