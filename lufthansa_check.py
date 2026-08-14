#!/usr/bin/env python3
"""
Lufthansa Open API — Flugplan-Gegenprüfung (keine Preisquelle!).

Die öffentliche Lufthansa Open API liefert keine Preise; ihre
Offer-Endpunkte sind Partnern vorbehalten. Was sie kann: den offiziellen
Flugplan. Damit wird vor dem Versand geprüft, ob die Langstrecke eines
Treffers am gemeldeten Tag überhaupt verkehrt — das entlarvt
Geisterpreise aus gecachten Quellen.

Die Prüfung ist optional und darf den Lauf nie abbrechen: bei jedem
Fehler wird schlicht "ungeprüft" gemeldet.

Benötigte Umgebungsvariablen:
    LH_CLIENT_ID, LH_CLIENT_SECRET   (developer.lufthansa.com)
"""

from __future__ import annotations

import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

_BASIS = "https://api.lufthansa.com/v1"

_token: str | None = None
_token_gueltig_bis: float = 0.0
_plan_cache: dict[tuple, set] = {}


def verfuegbar() -> bool:
    return bool(os.environ.get("LH_CLIENT_ID")
                and os.environ.get("LH_CLIENT_SECRET"))


def _zugangstoken() -> str:
    global _token, _token_gueltig_bis
    if _token and time.monotonic() < _token_gueltig_bis:
        return _token
    r = requests.post(
        f"{_BASIS}/oauth/token",
        data={
            "client_id": os.environ["LH_CLIENT_ID"],
            "client_secret": os.environ["LH_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    r.raise_for_status()
    antwort = r.json()
    _token = antwort["access_token"]
    _token_gueltig_bis = time.monotonic() + int(antwort.get("expires_in", 1800)) - 60
    return _token


def _langstrecke(segmente_hin: str, ziel: str) -> tuple[str, str] | None:
    """Aus "LH991 AMS-FRA, LH572 FRA-JNB" das Segment nach `ziel` ziehen."""
    for teil in segmente_hin.split(","):
        m = re.search(r"([A-Z0-9]{2})\s?(\d+)\s+([A-Z]{3})-([A-Z]{3})", teil.strip())
        if m and m.group(4) == ziel:
            return f"{m.group(1)}{int(m.group(2))}", m.group(3)
    return None


def _geplante_fluege(abflug: str, ziel: str, datum: str) -> set | None:
    """Flugnummern laut LH-Flugplan für Strecke+Tag, gecacht. None = Fehler."""
    schluessel = (abflug, ziel, datum)
    if schluessel in _plan_cache:
        return _plan_cache[schluessel]
    try:
        r = requests.get(
            f"{_BASIS}/operations/schedules/{abflug}/{ziel}/{datum}",
            params={"directFlights": 1},
            headers={"Authorization": f"Bearer {_zugangstoken()}",
                     "Accept": "application/json"},
            timeout=30,
        )
        if r.status_code == 404:            # kein Direktflug an dem Tag
            fluege: set = set()
        else:
            r.raise_for_status()
            eintraege = (r.json().get("ScheduleResource", {})
                         .get("Schedule", []))
            if isinstance(eintraege, dict):
                eintraege = [eintraege]
            fluege = set()
            for plan in eintraege:
                fls = plan.get("Flight", [])
                if isinstance(fls, dict):
                    fls = [fls]
                for f in fls:
                    mc = f.get("MarketingCarrier", {})
                    fluege.add(f"{mc.get('AirlineID', '')}"
                               f"{mc.get('FlightNumber', '')}")
    except Exception as e:
        logger.warning("Flugplan %s-%s %s nicht prüfbar: %s",
                       abflug, ziel, datum, e)
        _plan_cache[schluessel] = None
        return None
    _plan_cache[schluessel] = fluege
    return fluege


def pruefe(angebote, ziel: str) -> dict[str, bool | None]:
    """
    Je Angebots-Schlüssel: True (Langstrecke steht im Flugplan),
    False (steht nicht drin) oder None (nicht prüfbar).
    """
    ergebnis: dict[str, bool | None] = {}
    for a in angebote:
        seg = _langstrecke(a.fluege_hin, ziel)
        if seg is None:
            ergebnis[a.schluessel] = None
            continue
        flugnr, abflug = seg
        plan = _geplante_fluege(abflug, ziel, a.hinflug_datum)
        ergebnis[a.schluessel] = None if plan is None else (flugnr in plan)
    return ergebnis
