#!/usr/bin/env python3

"""Erzeugt einen iCalendar-Feed aus dem GTFS-Feed von GTFS.DE."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile

from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


# ============================================================
# KONFIGURATION
# ============================================================

CONFIG = json.loads(
    Path("config.json").read_text(
        encoding="utf-8"
    )
)

TZ = ZoneInfo(
    CONFIG.get(
        "timezone",
        "Europe/Berlin"
    )
)

FEED_URL = (
    "https://download.gtfs.de/"
    "germany/rv_free/latest.zip"
)


# ============================================================
# GTFS-FEED LADEN
# ============================================================

def fetch_feed() -> zipfile.ZipFile:

    print(
        "Lade aktuellen GTFS-Fahrplan ..."
    )

    request = Request(
        FEED_URL,
        headers={
            "User-Agent":
                "zugkalender/3.0"
        }
    )

    with urlopen(
        request,
        timeout=120
    ) as response:

        data = response.read()

    print(
        "GTFS-Download: "
        f"{len(data) / 1024 / 1024:.1f} MB"
    )

    return zipfile.ZipFile(
        io.BytesIO(data)
    )


def read_csv(
    zf: zipfile.ZipFile,
    filename: str
) -> list[dict[str, str]]:

    with zf.open(filename) as raw:

        text = io.TextIOWrapper(
            raw,
            encoding="utf-8-sig",
            newline=""
        )

        return list(
            csv.DictReader(text)
        )


# ============================================================
# ZEITFUNKTIONEN
# ============================================================

def gtfs_time(
    value: str,
    service_day: date
) -> datetime:

    """
    GTFS erlaubt Stundenwerte über 24:
    z.B. 25:30:00.
    """

    hours, minutes, seconds = map(
        int,
        value.split(":")
    )

    return (
        datetime.combine(
            service_day,
            datetime.min.time(),
            TZ
        )
        + timedelta(
            hours=hours,
            minutes=minutes,
            seconds=seconds
        )
    )


def hm(
    value: str
):
    return datetime.strptime(
        value,
        "%H:%M"
    ).time()


def in_window(
    dt: datetime,
    start: str,
    end: str
) -> bool:

    current = dt.timetz().replace(
        tzinfo=None
    )

    return (
        hm(start)
        <= current
        <= hm(end)
    )


# ============================================================
# ICS-FUNKTIONEN
# ============================================================

def escape_ics(
    value: str
) -> str:

    return (
        str(value)
        .replace(
            "\\",
            "\\\\"
        )
        .replace(
            ";",
            "\\;"
        )
        .replace(
            ",",
            "\\,"
        )
        .replace(
            "\n",
            "\\n"
        )
    )


def ics_datetime(
    dt: datetime
) -> str:

    return dt.astimezone(
        TZ
    ).strftime(
        "%Y%m%dT%H%M%S"
    )


# ============================================================
# DATUMSBEREICH
# ============================================================

def date_range(
    start: date,
    end: date
):

    current = start

    while current <= end:

        yield current

        current += timedelta(
            days=1
        )


# ============================================================
# BETRIEBSTAGE
# ============================================================

def active_dates(
    calendar_rows,
    calendar_dates_rows,
    start,
    end
):

    active = set()

    weekday_keys = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday"
    ]

    # --------------------------------------------------------
    # Reguläre Betriebstage
    # --------------------------------------------------------

    for row in calendar_rows:

        service_start = datetime.strptime(
            row["start_date"],
            "%Y%m%d"
        ).date()

        service_end = datetime.strptime(
            row["end_date"],
            "%Y%m%d"
        ).date()

        first = max(
            start,
            service_start
        )

        last = min(
            end,
            service_end
        )

        if first > last:
            continue

        for current in date_range(
            first,
            last
        ):

            weekday = weekday_keys[
                current.weekday()
            ]

            if row.get(
                weekday
            ) == "1":

                active.add(
                    (
                        row["service_id"],
                        current
                    )
                )

    # --------------------------------------------------------
    # Fahrplan-Ausnahmen
    # --------------------------------------------------------

    for row in calendar_dates_rows:

        current = datetime.strptime(
            row["date"],
            "%Y%m%d"
        ).date()

        if not (
            start
            <= current
            <= end
        ):
            continue

        key = (
            row["service_id"],
            current
        )

        # 1 = zusätzlicher Verkehrstag
        if row["exception_type"] == "1":

            active.add(
                key
            )

        # 2 = Verkehr entfällt
        elif row["exception_type"] == "2":

            active.discard(
                key
            )

    return active


# ============================================================
# BAHNHOFSSUCHE
# ============================================================

def normalize_station_name(
    value: str
) -> str:

    if not value:
        return ""

    value = value.lower()

    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss"
    }

    for old, new in replacements.items():

        value = value.replace(
            old,
            new
        )

    return re.sub(
        r"[^a-z0-9]",
        "",
        value
    )


def station_ids(
    stops,
    station_name
):

    wanted = normalize_station_name(
        station_name
    )

    ids = set()

    print(
        f"Suche Bahnhof: "
        f"{station_name}"
    )

    # --------------------------------------------------------
    # Exakte / normalisierte Suche
    # --------------------------------------------------------

    for stop in stops:

        stop_name = normalize_station_name(
            stop.get(
                "stop_name",
                ""
            )
        )

        if stop_name == wanted:

            ids.add(
                stop["stop_id"]
            )

    # --------------------------------------------------------
    # Teiltreffer als Fallback
    # --------------------------------------------------------

    if not ids:

        for stop in stops:

            stop_name = normalize_station_name(
                stop.get(
                    "stop_name",
                    ""
                )
            )

            if (
                wanted in stop_name
                or stop_name in wanted
            ):

                ids.add(
                    stop["stop_id"]
                )

    if not ids:

        raise RuntimeError(
            "Bahnhof nicht im GTFS-Feed gefunden: "
            f"{station_name}"
        )

    print(
        f"Bahnhof gefunden: "
        f"{station_name}"
    )

    for stop in stops:

        if stop["stop_id"] in ids:

            print(
                "  -> "
                f"{stop.get('stop_name')} "
                f"[{stop.get('stop_id')}]"
            )

    return ids


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():

    horizon = int(
        CONFIG.get(
            "horizon_days",
            35
        )
    )

    today = datetime.now(
        TZ
    ).date()

    end_date = (
        today
        + timedelta(
            days=horizon
        )
    )

    print()
    print(
        "========================================"
    )
    print(
        "ZUGKALENDER"
    )
    print(
        "========================================"
    )

    print(
        f"Zeitraum: "
        f"{today} bis {end_date}"
    )

    print()

    # ========================================================
    # GTFS LADEN
    # ========================================================

    with fetch_feed() as zf:

        print(
            "Lese GTFS-Dateien ..."
        )

        stops = read_csv(
            zf,
            "stops.txt"
        )

        trips = read_csv(
            zf,
            "trips.txt"
        )

        stop_times = read_csv(
            zf,
            "stop_times.txt"
        )

        if "calendar.txt" in zf.namelist():

            calendar = read_csv(
                zf,
                "calendar.txt"
            )

        else:

            calendar = []

        if "calendar_dates.txt" in zf.namelist():

            calendar_dates = read_csv(
                zf,
                "calendar_dates.txt"
            )

        else:

            calendar_dates = []

        if "routes.txt" in zf.namelist():

            routes = read_csv(
                zf,
                "routes.txt"
            )

        else:

            routes = []

    print(
        f"Stops: {len(stops)}"
    )

    print(
        f"Trips: {len(trips)}"
    )

    print(
        f"Stop times: {len(stop_times)}"
    )

    print()

    # ========================================================
    # BAHNHÖFE
    # ========================================================

    from_station = CONFIG["from"]
    to_station = CONFIG["to"]

    from_ids = station_ids(
        stops,
        from_station["name"]
    )

    to_ids = station_ids(
        stops,
        to_station["name"]
    )

    print()

    print(
        "Startbahnhof: "
        f"{from_station['name']}"
    )

    print(
        "Zielbahnhof: "
        f"{to_station['name']}"
    )

    print()

    # ========================================================
    # TRIPS / ROUTEN
    # ========================================================

    trip_by_id = {
        trip["trip_id"]: trip
        for trip in trips
    }

    route_by_id = {
        route["route_id"]: route
        for route in routes
    }

    # ========================================================
    # STOP TIMES NACH TRIP SORTIEREN
    # ========================================================

    stop_times_by_trip = {}

    for row in stop_times:

        trip_id = row["trip_id"]

        stop_times_by_trip.setdefault(
            trip_id,
            []
        ).append(row)

    # ========================================================
    # BETRIEBSTAGE
    # ========================================================

    active = active_dates(
        calendar,
        calendar_dates,
        today,
        end_date
    )

    # ========================================================
    # EVENTS
    # ========================================================

    events = []

    outbound_count = 0
    return_count = 0

    # ========================================================
    # JEDEN TAG PRÜFEN
    # ========================================================

    for current_date in date_range(
        today,
        end_date
    ):

        # Nur Montag bis Freitag
        if (
            CONFIG.get(
                "weekday_only",
                True
            )
            and current_date.weekday() >= 5
        ):
            continue

        # ====================================================
        # DIE BEIDEN RICHTUNGEN
        #
        # WICHTIG:
        # Für die Rückfahrt werden die Bahnhöfe wirklich
        # umgedreht.
        # ====================================================

        directions = [

            {
                "name": "outbound",
                "label": "Hinfahrt",
                "origin_ids": from_ids,
                "destination_ids": to_ids,
                "origin_name": from_station["name"],
                "destination_name": to_station["name"],
                "window": CONFIG["morning"]
            },

            {
                "name": "return",
                "label": "Rückfahrt",
                "origin_ids": to_ids,
                "destination_ids": from_ids,
                "origin_name": to_station["name"],
                "destination_name": from_station["name"],
                "window": CONFIG["afternoon"]
            }
        ]

        # ====================================================
        # RICHTUNG DURCHLAUFEN
        # ====================================================

        for direction in directions:

            origin_ids = direction[
                "origin_ids"
            ]

            destination_ids = direction[
                "destination_ids"
            ]

            window = direction[
                "window"
            ]

            origin_name = direction[
                "origin_name"
            ]

            destination_name = direction[
                "destination_name"
            ]

            # ------------------------------------------------
            # Alle Trips prüfen
            # ------------------------------------------------

            for trip_id, trip in trip_by_id.items():

                service_id = trip.get(
                    "service_id"
                )

                # Fährt dieser Service an diesem Tag?
                if (
                    service_id,
                    current_date
                ) not in active:

                    continue

                rows = stop_times_by_trip.get(
                    trip_id,
                    []
                )

                if not rows:
                    continue

                # ------------------------------------------------
                # Stop Times nach Reihenfolge sortieren
                # ------------------------------------------------

                try:

                    rows = sorted(
                        rows,
                        key=lambda row:
                            int(
                                row.get(
                                    "stop_sequence",
                                    "0"
                                )
                            )
                    )

                except ValueError:

                    continue

                # ------------------------------------------------
                # Abfahrt und Ankunft suchen
                #
                # Wir suchen bewusst nach dem ERSTEN passenden
                # Origin und dem DARAUFFOLGENDEN Destination.
                # ------------------------------------------------

                connection = None

                for origin_index, origin_row in enumerate(
                    rows
                ):

                    if (
                        origin_row.get(
                            "stop_id"
                        )
                        not in origin_ids
                    ):
                        continue

                    for destination_row in rows[
                        origin_index + 1:
                    ]:

                        if (
                            destination_row.get(
                                "stop_id"
                            )
                            not in destination_ids
                        ):
                            continue

                        departure_raw = (
                            origin_row.get(
                                "departure_time"
                            )
                            or origin_row.get(
                                "arrival_time"
                            )
                        )

                        arrival_raw = (
                            destination_row.get(
                                "arrival_time"
                            )
                            or destination_row.get(
                                "departure_time"
                            )
                        )

                        if (
                            not departure_raw
                            or not arrival_raw
                        ):
                            continue

                        departure = gtfs_time(
                            departure_raw,
                            current_date
                        )

                        arrival = gtfs_time(
                            arrival_raw,
                            current_date
                        )

                        if arrival <= departure:
                            continue

                        # Zeitfenster prüfen
                        if not in_window(
                            departure,
                            window[
                                "departure_start"
                            ],
                            window[
                                "departure_end"
                            ]
                        ):
                            continue

                        connection = (
                            origin_row,
                            destination_row,
                            departure,
                            arrival
                        )

                        break

                    if connection:
                        break

                # Keine passende Verbindung
                if not connection:
                    continue

                (
                    origin_row,
                    destination_row,
                    departure,
                    arrival
                ) = connection

                # =================================================
                # ROUTENINFORMATIONEN
                # =================================================

                route = route_by_id.get(
                    trip.get(
                        "route_id",
                        ""
                    ),
                    {}
                )

                route_short_name = (
                    route.get(
                        "route_short_name"
                    )
                    or ""
                )

                route_long_name = (
                    route.get(
                        "route_long_name"
                    )
                    or ""
                )

                if route_short_name:

                    train_name = (
                        route_short_name
                    )

                elif route_long_name:

                    train_name = (
                        route_long_name
                    )

                else:

                    train_name = "Zug"

                headsign = (
                    trip.get(
                        "trip_headsign",
                        ""
                    )
                )

                # =================================================
                # TITEL
                # =================================================

                summary = (
                    f"{train_name}: "
                    f"{origin_name} → "
                    f"{destination_name}"
                )

                # =================================================
                # BESCHREIBUNG
                # =================================================

                description_lines = [

                    f"Abfahrt: "
                    f"{departure:%H:%M}",

                    f"Ankunft: "
                    f"{arrival:%H:%M}",

                    f"Linie: "
                    f"{train_name}"
                ]

                if headsign:

                    description_lines.append(
                        f"Zugziel: "
                        f"{headsign}"
                    )

                if route_long_name:

                    description_lines.append(
                        route_long_name
                    )

                description_lines.extend(
                    [
                        "",
                        "Quelle: GTFS.DE",
                        "Schienenregionalverkehr",
                        "",
                        "Dieser Kalender enthält "
                        "Fahrplandaten.",
                        "Verspätungen oder kurzfristige "
                        "Zugausfälle werden nicht "
                        "berücksichtigt."
                    ]
                )

                description = "\\n".join(
                    description_lines
                )

                # =================================================
                # Eindeutige UID
                # =================================================

                uid = (
                    f"{current_date:%Y%m%d}-"
                    f"{direction['name']}-"
                    f"{trip_id}-"
                    f"{origin_row.get('stop_sequence')}-"
                    f"{destination_row.get('stop_sequence')}"
                    "@zugkalender"
                )

                # =================================================
                # ICS EVENT
                # =================================================

                event = [
                    "BEGIN:VEVENT",

                    f"UID:{escape_ics(uid)}",

                    (
                        "DTSTAMP:"
                        f"{datetime.now(TZ).strftime('%Y%m%dT%H%M%S')}"
                    ),

                    (
                        "DTSTART;TZID=Europe/Berlin:"
                        f"{ics_datetime(departure)}"
                    ),

                    (
                        "DTEND;TZID=Europe/Berlin:"
                        f"{ics_datetime(arrival)}"
                    ),

                    (
                        "SUMMARY:"
                        f"{escape_ics(summary)}"
                    ),

                    (
                        "DESCRIPTION:"
                        f"{escape_ics(description)}"
                    ),

                    (
                        "LOCATION:"
                        f"{escape_ics(origin_name)}"
                    ),

                    "STATUS:CONFIRMED",

                    "TRANSP:OPAQUE",

                    "END:VEVENT"
                ]

                events.append(
                    "\r\n".join(event)
                )

                # Statistik
                if direction["name"] == "outbound":

                    outbound_count += 1

                else:

                    return_count += 1

    # ========================================================
    # DOPPELTE EVENTS ENTFERNEN
    # ========================================================

    events = list(
        dict.fromkeys(
            events
        )
    )

    # ========================================================
    # SORTIERUNG
    #
    # Die Events werden bereits chronologisch erzeugt.
    # ========================================================

    print()

    print(
        "========================================"
    )

    print(
        f"Hinfahrten: "
        f"{outbound_count}"
    )

    print(
        f"Rückfahrten: "
        f"{return_count}"
    )

    print(
        f"Gesamt: "
        f"{len(events)}"
    )

    print(
        "========================================"
    )

    # ========================================================
    # ICS-KOPF
    # ========================================================

    header = [

        "BEGIN:VCALENDAR",

        "VERSION:2.0",

        (
            "PRODID:"
            "//Zugkalender "
            "Niederweimar Frankfurt//DE"
        ),

        "CALSCALE:GREGORIAN",

        "METHOD:PUBLISH",

        (
            "X-WR-CALNAME:"
            + escape_ics(
                CONFIG[
                    "calendar_name"
                ]
            )
        ),

        "X-WR-TIMEZONE:Europe/Berlin"
    ]

    # ========================================================
    # ICS-DATEI SCHREIBEN
    # ========================================================

    ics_content = (
        "\r\n".join(
            header
            + events
            + [
                "END:VCALENDAR"
            ]
        )
        + "\r\n"
    )

    Path(
        "docs/zugkalender.ics"
    ).write_text(
        ics_content,
        encoding="utf-8"
    )

    # ========================================================
    # STATUSDATEI
    # ========================================================

    status = {

        "generated_at":
            datetime.now(
                TZ
            ).isoformat(),

        "valid_from":
            today.isoformat(),

        "valid_until":
            end_date.isoformat(),

        "events":
            len(events),

        "outbound_events":
            outbound_count,

        "return_events":
            return_count,

        "source":
            FEED_URL,

        "source_license":
            "Creative Commons 4.0",

        "stations": {

            "from":
                from_station,

            "to":
                to_station
        }
    }

    Path(
        "docs/status.json"
    ).write_text(
        json.dumps(
            status,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    # ========================================================
    # ABSCHLUSS
    # ========================================================

    print()

    print(
        "Kalender erfolgreich erzeugt!"
    )

    print(
        f"Zeitraum: "
        f"{today} bis {end_date}"
    )

    print(
        f"Hinfahrten: "
        f"{outbound_count}"
    )

    print(
        f"Rückfahrten: "
        f"{return_count}"
    )

    print(
        f"Gesamt: "
        f"{len(events)}"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
