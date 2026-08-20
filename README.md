# MEILENFLUCHT ✈️

**Essen. Trinken. Status retten.**

Preismonitor (dreimal täglich: 8, 14 und 20 Uhr deutscher Zeit) für
LH-Group-Flüge nach **Johannesburg (JNB) und Kapstadt (CPT)** — **First und
Business** getrennt: zwei Personen, Hin- und Rückflug im Dezember, 5–10 Tage
Aufenthalt, Abflug ab **AMS, FMO, MUC, FRA oder ZRH**. Gemailt wird nur, wenn
ein Angebot unter der Kabinen-Schwelle liegt (First 6.000 €, Business 3.500 €
p. P.) und spürbar besser ist als alles bisher Gesehene.

## Wie es funktioniert

```
GitHub Actions (06/12/18 UTC = 8/14/20 Uhr Sommerzeit)
  └─ deal_monitor.py
       ├─ Raster: Amadeus · Duffel · Kiwi · Travelpayouts  (aktiv, wenn Key gesetzt)
       ├─ SerpAPI (Google Flights): prüft den besten Treffer nach
       ├─ Filter: gesuchte Kabine auf der Langstrecke, max. 1 Umstieg, Kabinen-Schwellwert
       ├─ Lufthansa Open API: bestätigt die Langstrecke im offiziellen Flugplan
       ├─ preis_historie.json: nur echte Verbesserungen (≥ 200 €) melden
       └─ POST an n8n-Webhook  ->  n8n formatiert & verschickt die Mail
```

Der Suchraum (2 Flughäfen × Dezember-Abflüge × 5–10 Tage Aufenthalt ≈ 370
Kombinationen) wird pro Quelle in einem rotierenden Raster abgefragt, damit die
API-Kontingente reichen. Mit dem Standard-Budget von 65 Amadeus-Calls pro Lauf
ist der volle Suchraum alle ~6 Tage einmal komplett abgedeckt.

## Quellen

| Quelle  | Modul               | Zugang | Anmerkung |
|---------|---------------------|--------|-----------|
| Amadeus | `flight_scraper.py` | `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET`, optional `AMADEUS_ENV` | **Achtung:** Das kostenlose Self-Service-Portal wurde im Juli 2026 abgeschaltet; Neuzugänge gibt es nur noch über das Enterprise-Portal (Firmenkunden). Das Modul bleibt für Alt-Keys drin — ohne Key wird die Quelle übersprungen. |
| Duffel  | `duffel_quelle.py`  | `DUFFEL_API_KEY` | Suche kostenlos; Live-Key erfordert verifiziertes Konto. Test-Keys liefern Fantasiedaten. |
| Kiwi    | `kiwi_quelle.py`    | `KIWI_API_KEY` | Tequila nimmt derzeit keine neuen Registrierungen an — ohne Alt-Key bleibt die Quelle inaktiv. |
| Travelpayouts | `travelpayouts_quelle.py` | `TRAVELPAYOUTS_TOKEN` | Kostenloser Key; gecachte Preise aus echten Nutzersuchen — für Nischenrouten lückig, als Gratis-Zusatzsignal nützlich. |
| SerpAPI | `serpapi_quelle.py` | `SERPAPI_KEY` | Google-Flights-Preise. Läuft als Nachprüfung der 3 günstigsten Treffer pro Lauf, damit die 100 Gratis-Suchen/Monat reichen. |
| Lufthansa Open API | `lufthansa_check.py` | `LH_CLIENT_ID`, `LH_CLIENT_SECRET` | **Keine Preisquelle** (die öffentlichen Endpunkte liefern keine Tarife) — prüft vor dem Versand, ob die Langstrecke laut offiziellem Flugplan am gemeldeten Tag fliegt (`flugplan_bestaetigt` im Payload). |

## Einrichtung

1. **GitHub-Secrets** anlegen (Repo → Settings → Secrets and variables → Actions):
   Pflicht: `N8N_WEBHOOK_URL`, `N8N_SHARED_SECRET` und mindestens ein Quellen-Key —
   realistisch für Privatpersonen: `DUFFEL_API_KEY` (app.duffel.com),
   `TRAVELPAYOUTS_TOKEN` (travelpayouts.com), `SERPAPI_KEY` (serpapi.com),
   `LH_CLIENT_ID`/`LH_CLIENT_SECRET` (developer.lufthansa.com).
   Nur mit Altbestand: `AMADEUS_CLIENT_ID`/`AMADEUS_CLIENT_SECRET`, `KIWI_API_KEY`.
2. **n8n**: `n8n-workflow.json` importieren, im Knoten "Mail senden" ein
   SMTP-Credential anlegen (Gmail-App-Passwort, smtp.gmail.com:465), im Knoten
   "Secret prüfen" das Geheimnis (= `N8N_SHARED_SECRET`) und im Mail-Knoten die
   Empfängeradresse eintragen. Workflow veröffentlichen.
3. Fertig — der Workflow `monitor.yml` läuft dreimal täglich (06/12/18 UTC) oder manuell
   über *Run workflow*.

## Lokal testen

```
pip install -r requirements.txt
AMADEUS_CLIENT_ID=… AMADEUS_CLIENT_SECRET=… N8N_WEBHOOK_URL=… python deal_monitor.py
```

Alle Reiseparameter (Zeitraum, Schwellwerte, Filter, API-Budgets) stehen in
`config.json`.
