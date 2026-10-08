# Automatischer Zugkalender: Niederweimar ↔ Frankfurt (Main) Hbf

Dieses Repository erzeugt automatisch einen öffentlichen iCalendar-Feed (`zugkalender.ics`) für:

- **Niederweimar → Frankfurt (Main) Hbf**, Mo–Fr, Abfahrt 05:25–07:00
- **Frankfurt (Main) Hbf → Niederweimar**, Mo–Fr, Abfahrt 14:00–16:30

## Datenquelle

Verwendet wird der **kostenlos öffentlich abrufbare GTFS-Feed „Regional Rail Germany“ von GTFS.DE**:

`https://download.gtfs.de/germany/rv_free/latest.zip`

Der Feed enthält den deutschen Schienenregionalverkehr (u. a. RB, RE, IRE und S-Bahnen), wird regelmäßig aktualisiert und steht unter Creative Commons 4.0. Es ist **kein DB-Account und kein API-Schlüssel erforderlich**.

## Einrichtung

1. Repository auf GitHub anlegen, z. B. `zugkalender`.
2. Den gesamten Inhalt dieses Projekts in das Repository hochladen.
3. Unter **Settings → Pages** einstellen:
   - Source: `Deploy from a branch`
   - Branch: `main`
   - Folder: `/docs`
4. Unter **Actions** den Workflow **„Fahrplan aktualisieren“** einmal über **Run workflow** starten.
5. Die Kalenderadresse lautet anschließend ungefähr:

   `https://DEIN-GITHUB-NAME.github.io/zugkalender/zugkalender.ics`

6. In Google Kalender: **Weitere Kalender → + → Per URL** und diese `.ics`-Adresse eintragen.

Nicht „Importieren“ verwenden, weil das nur eine Momentaufnahme erzeugt.

## Automatische Aktualisierung

GitHub Actions lädt den öffentlichen GTFS-Feed täglich neu und erzeugt daraus einen Kalender für die kommenden 35 Tage. Änderungen im GTFS-Fahrplan werden damit automatisch übernommen.

Google Kalender kann abonnierte Internetkalender mit Verzögerung aktualisieren; eine Änderung im Feed muss deshalb nicht sofort in Google Kalender sichtbar sein.

## Keine persönlichen Daten / keine Secrets

Das Programm benötigt keine Zugangsdaten. Es werden keine personenbezogenen Daten in den Kalender geschrieben.

## Hinweis zur Datenqualität

GTFS.DE weist darauf hin, dass die kostenlosen Feeds ohne Gewähr auf Vollständigkeit und Korrektheit veröffentlicht werden. Für eine konkrete Fahrt sollte im Zweifel zusätzlich die aktuelle Reiseauskunft geprüft werden.
