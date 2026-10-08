#!/usr/bin/env python3
"""Erzeugt einen iCalendar-Feed aus dem frei zugänglichen GTFS-Feed von GTFS.DE.

Keine Registrierung, kein DB-API-Schlüssel und keine persönlichen Daten erforderlich.
Der Feed enthält den deutschen Schienenregionalverkehr und wird von GTFS.DE
regelmäßig aktualisiert.
"""

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


CONFIG = json.loads(
    Path("config.json").read_text(encoding="utf-8")
)

TZ = ZoneInfo(
    CONFIG.get("timezone", "Europe/Berlin")
)

FEED_URL = (
    "https://download.gtfs.de/germany/rv_free/latest.zip"
)


def fetch_feed() -> zipfile.ZipFile:
    print("Lade aktuellen GTFS-Fahrplan ...")

    req = Request(
        FEED_URL,
        headers={
            "User-Agent": "zugkalender/2.0"
        }
    )

    with urlopen(req, timeout=120) as response:
        data = response.read()

    print(
        f"GTFS-Download: "
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


def gtfs_time(
    value: str,
    service_day: date
) -> datetime:
    """GTFS-Zeit; Werte >=24:00 dürfen bis in den Folgetag reichen."""

    h, m, s = map(
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
            hours=h,
            minutes=m,
            seconds=s
        )
    )


def hm(value: str):
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


def esc(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def ics_dt(dt: datetime) -> str:
    return dt.astimezone(
        TZ
    ).strftime(
        "%Y%m%dT%H%M%S"
    )


def date_range(
    start: date,
    end: date
):
    d = start

    while d <= end:
        yield d
        d += timedelta(days=1)


def active_dates(
    calendar_rows,
    calendar_dates_rows,
    start,
    end
):

    active = set()

    for row in calendar_rows:

        s = datetime.strptime(
            row["start_date"],
            "%Y%m%d"
        ).date()

        e = datetime.strptime(
            row["end_date"],
            "%Y%m%d"
        ).date()

        lo = max(start, s)
        hi = min(end, e)

        if lo > hi:
            continue

        weekday_keys = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday"
        ]

        for d in date_range(lo, hi):

            if row[
                weekday_keys[d.weekday()]
            ] == "1":

                active.add(
                    (
                        row["service_id"],
                        d
                    )
                )

    for row in calendar_dates_rows:

        d = datetime.strptime(
            row["date"],
            "%Y%m%d"
        ).date()

        if not (
            start <= d <= end
        ):
            continue

        key = (
            row["service_id"],
            d
        )

        if row["exception_type"] == "1":

            active.add(key)

        elif row["exception_type"] == "2":

            active.discard(key)

    return active


def normalize_station_name(
    value: str
) -> str:
    """Normalisiert Bahnhofsnamen für einen robusten Vergleich."""

    if not value:
        return ""

    value = value.lower()

    # Umlaute vereinheitlichen
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }

    for old, new in replacements.items():
        value = value.replace(
            old,
            new
        )

    # Alles entfernen, was kein Buchstabe oder keine Zahl ist.
    # Dadurch werden z. B. diese Schreibweisen gleich:
    #
    # Frankfurt (Main) Hbf
    # Frankfurt(Main)Hbf
    # Frankfurt-Main Hbf
    #
    value = re.sub(
        r"[^a-z0-9]",
        "",
        value
    )

    return value


def station_ids(
    stops,
    station_name
):
    """Findet alle GTFS-Stop-IDs eines Bahnhofs.

    Die Suche erfolgt zunächst exakt und anschließend über
    eine normalisierte Schreibweise. Dadurch werden
    unterschiedliche Schreibweisen desselben Bahnhofs erkannt.
    """

    wanted_exact = " ".join(
        station_name
        .lower()
        .split()
    )

    wanted_normalized = normalize_station_name(
        station_name
    )

    ids = set()

    print(
        f"Suche Bahnhof: {station_name}"
    )

    # ---------------------------------------------------------
    # 1. Exakte Suche
    # ---------------------------------------------------------

    for stop in stops:

        name = " ".join(
            stop.get(
                "stop_name",
                ""
            )
            .lower()
            .split()
        )

        if name == wanted_exact:

            ids.add(
                stop["stop_id"]
            )

    # ---------------------------------------------------------
    # 2. Normalisierte Suche
    # ---------------------------------------------------------

    if not ids:

        for stop in stops:

            name = normalize_station_name(
                stop.get(
                    "stop_name",
                    ""
                )
            )

            if name == wanted_normalized:

                ids.add(
                    stop["stop_id"]
                )

    # ---------------------------------------------------------
    # 3. Teilweise Suche als zusätzliche Absicherung
    # ---------------------------------------------------------

    if not ids:

        for stop in stops:

            name = normalize_station_name(
                stop.get(
                    "stop_name",
                    ""
                )
            )

            if (
                wanted_normalized in name
                or name in wanted_normalized
            ):

                ids.add(
                    stop["stop_id"]
                )

    # ---------------------------------------------------------
    # Ergebnis
    # ---------------------------------------------------------

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
                f"  -> "
                f"{stop.get('stop_name')} "
                f"[{stop.get('stop_id')}]"
            )

    return ids


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

    end = (
        today
        + timedelta(days=horizon)
    )

    # ---------------------------------------------------------
    # GTFS laden
    # ---------------------------------------------------------

    with fetch_feed() as zf:

        print("Lese GTFS-Dateien ...")

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

        calendar = (
            read_csv(
                zf,
                "calendar.txt"
            )
            if "calendar.txt"
            in zf.namelist()
            else []
        )

        calendar_dates = (
            read_csv(
                zf,
                "calendar_dates.txt"
            )
            if "calendar_dates.txt"
            in zf.namelist()
            else []
        )

        routes = (
            read_csv(
                zf,
                "routes.txt"
            )
            if "routes.txt"
            in zf.namelist()
            else []
        )

    print(
        f"Stops: {len(stops)}"
    )

    print(
        f"Trips: {len(trips)}"
    )

    print(
        f"Stop times: {len(stop_times)}"
    )

    # ---------------------------------------------------------
    # Bahnhöfe bestimmen
    # ---------------------------------------------------------

    from_ids = station_ids(
        stops,
        CONFIG["from"]["name"]
    )

    to_ids = station_ids(
        stops,
        CONFIG["to"]["name"]
    )

    print()

    print(
        f"Startbahnhof: "
        f"{CONFIG['from']['name']}"
    )

    print(
        f"Zielbahnhof: "
        f"{CONFIG['to']['name']}"
    )

    print()

    # ---------------------------------------------------------
    # Trips und Linien vorbereiten
    # ---------------------------------------------------------

    trip_by_id = {
        t["trip_id"]: t
        for t in trips
    }

    route_by_id = {
        r["route_id"]: r
        for r in routes
    }

    # ---------------------------------------------------------
    # Relevante Stopzeiten sammeln
    # ---------------------------------------------------------

    relevant = {}

    for st in stop_times:

        sid = st.get(
            "stop_id"
        )

        if (
            sid not in from_ids
            and sid not in to_ids
        ):
            continue

        tid = st["trip_id"]

        relevant.setdefault(
            tid,
            []
        ).append(st)

    print(
        f"Relevante Trips: "
        f"{len(relevant)}"
    )

    # ---------------------------------------------------------
    # Trips mit beiden Bahnhöfen bestimmen
    # ---------------------------------------------------------

    pairs = []

    for tid, rows in relevant.items():

        trip = trip_by_id.get(
            tid
        )

        if not trip:
            continue

        try:

            ordered = sorted(
                rows,
                key=lambda r:
                int(
                    r.get(
                        "stop_sequence",
                        "0"
                    )
                )
            )

        except ValueError:

            ordered = rows

        from_rows = [
            r
            for r in ordered
            if r.get("stop_id")
            in from_ids
        ]

        to_rows = [
            r
            for r in ordered
            if r.get("stop_id")
            in to_ids
        ]

        if (
            from_rows
            and to_rows
        ):

            pairs.append(
                (
                    tid,
                    trip,
                    from_rows,
                    to_rows
                )
            )

    print(
        f"Trips mit beiden "
        f"Bahnhöfen: {len(pairs)}"
    )

    # ---------------------------------------------------------
    # Gültige Betriebstage
    # ---------------------------------------------------------

    active = active_dates(
        calendar,
        calendar_dates,
        today,
        end
    )

    events = []

    counts = {
        "outbound": 0,
        "return": 0
    }

    # ---------------------------------------------------------
    # Fahrten erzeugen
    # ---------------------------------------------------------

    for d in date_range(
        today,
        end
    ):

        # Nur Montag bis Freitag
        if d.weekday() >= 5:
            continue

        # -----------------------------------------------------
        # Morgens hin
        # -----------------------------------------------------

        directions = [
            (
                "outbound",
                from_ids,
                to_ids,
                CONFIG["morning"]
            ),
            (
                "return",
                to_ids,
                from_ids,
                CONFIG["afternoon"]
            )
        ]

        for (
            direction,
            origin_ids,
            destination_ids,
            window
        ) in directions:

            for (
                tid,
                trip,
                origin_rows,
                destination_rows
            ) in pairs:

                # Prüfen, ob der Zug an diesem Tag fährt
                if (
                    trip.get("service_id"),
                    d
                ) not in active:

                    continue

                # -------------------------------------------------
                # Richtige Fahrtrichtung
                # -------------------------------------------------

                found_connection = False

                for o in origin_rows:

                    for dest in destination_rows:

                        try:

                            origin_sequence = int(
                                o.get(
                                    "stop_sequence",
                                    "0"
                                )
                            )

                            destination_sequence = int(
                                dest.get(
                                    "stop_sequence",
                                    "0"
                                )
                            )

                        except ValueError:

                            continue

                        if (
                            origin_sequence
                            >= destination_sequence
                        ):

                            continue

                        dep_raw = (
                            o.get(
                                "departure_time"
                            )
                            or o.get(
                                "arrival_time"
                            )
                        )

                        arr_raw = (
                            dest.get(
                                "arrival_time"
                            )
                            or dest.get(
                                "departure_time"
                            )
                        )

                        if not dep_raw or not arr_raw:
                            continue

                        dep = gtfs_time(
                            dep_raw,
                            d
                        )

                        arr = gtfs_time(
                            arr_raw,
                            d
                        )

                        # -------------------------------------------------
                        # Zeitfenster prüfen
                        # -------------------------------------------------

                        if not in_window(
                            dep,
                            window[
                                "departure_start"
                            ],
                            window[
                                "departure_end"
                            ]
                        ):

                            continue

                        if arr <= dep:
                            continue

                        # -------------------------------------------------
                        # Linieninformationen
                        # -------------------------------------------------

                        route = route_by_id.get(
                            trip.get(
                                "route_id",
                                ""
                            ),
                            {}
                        )

                        route_name = (
                            route.get(
                                "route_short_name"
                            )
                            or route.get(
                                "route_long_name"
                            )
                            or "Zug"
                        )

                        headsign = trip.get(
                            "trip_headsign",
                            ""
                        )

                        service_trip = trip.get(
                            "trip_id",
                            tid
                        )

                        # -------------------------------------------------
                        # Bahnhofsnamen
                        # -------------------------------------------------

                        if direction == "outbound":

                            origin_name = CONFIG[
                                "from"
                            ][
                                "name"
                            ]

                            destination_name = CONFIG[
                                "to"
                            ][
                                "name"
                            ]

                        else:

                            origin_name = CONFIG[
                                "to"
                            ][
                                "name"
                            ]

                            destination_name = CONFIG[
                                "from"
                            ][
                                "name"
                            ]

                        # -------------------------------------------------
                        # Kalender-Titel
                        # -------------------------------------------------

                        summary = (
                            f"{route_name}: "
                            f"{origin_name} → "
                            f"{destination_name}"
                        )

                        # -------------------------------------------------
                        # Eindeutige ID
                        # -------------------------------------------------

                        uid = (
                            f"{d:%Y%m%d}-"
                            f"{direction}-"
                            f"{tid}-"
                            f"{o.get('stop_sequence')}-"
                            f"{dest.get('stop_sequence')}"
                            "@zugkalender"
                        )

                        # -------------------------------------------------
                        # Beschreibung
                        # -------------------------------------------------

                        desc = (
                            f"Linie: {route_name}\\n"
                            f"Abfahrt: {dep:%H:%M}\\n"
                            f"Ankunft: {arr:%H:%M}\\n"
                            f"Ziel/Headsign: {headsign}\\n"
                            f"Quelle: GTFS für Deutschland / "
                            f"Schienenregionalverkehr\\n"
                            f"Trip-ID: {service_trip}"
                        )

                        # -------------------------------------------------
                        # Kalender-Event
                        # -------------------------------------------------

                        event = "\r\n".join(
                            [
                                "BEGIN:VEVENT",

                                f"UID:{esc(uid)}",

                                (
                                    "DTSTAMP:"
                                    f"{datetime.now(TZ).strftime('%Y%m%dT%H%M%S')}"
                                ),

                                (
                                    "DTSTART;TZID=Europe/Berlin:"
                                    f"{ics_dt(dep)}"
                                ),

                                (
                                    "DTEND;TZID=Europe/Berlin:"
                                    f"{ics_dt(arr)}"
                                ),

                                (
                                    "SUMMARY:"
                                    f"{esc(summary)}"
                                ),

                                (
                                    "DESCRIPTION:"
                                    f"{esc(desc)}"
                                ),

                                (
                                    "LOCATION:"
                                    f"{esc(origin_name)}"
                                ),

                                "STATUS:CONFIRMED",
                                "TRANSP:OPAQUE",

                                "END:VEVENT"
                            ]
                        )

                        events.append(
                            event
                        )

                        counts[
                            direction
                        ] += 1

                        found_connection = True

                        break

                    if found_connection:
                        break

    # ---------------------------------------------------------
    # Doppelte Events entfernen
    # ---------------------------------------------------------

    events = list(
        dict.fromkeys(
            events
        )
    )

    # ---------------------------------------------------------
    # Kalenderkopf
    # ---------------------------------------------------------

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
            + esc(
                CONFIG[
                    "calendar_name"
                ]
            )
        ),

        "X-WR-TIMEZONE:Europe/Berlin"
    ]

    # ---------------------------------------------------------
    # ICS-Datei schreiben
    # ---------------------------------------------------------

    ics_content = (
        "\r\n".join(
            header
            + events
            + ["END:VCALENDAR"]
        )
        + "\r\n"
    )

    Path(
        "docs/zugkalender.ics"
    ).write_text(
        ics_content,
        encoding="utf-8"
    )

    # ---------------------------------------------------------
    # Statusdatei schreiben
    # ---------------------------------------------------------

    status = {
        "generated_at":
            datetime.now(TZ).isoformat(),

        "valid_from":
            today.isoformat(),

        "valid_until":
            end.isoformat(),

        "events":
            len(events),

        "outbound_events":
            counts["outbound"],

        "return_events":
            counts["return"],

        "source":
            FEED_URL,

        "source_license":
            "Creative Commons 4.0",

        "stations": {
            "from":
                CONFIG["from"],

            "to":
                CONFIG["to"]
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

    print()
    print(
        "========================================"
    )
    print(
        "Kalender erfolgreich erzeugt!"
    )
    print(
        "========================================"
    )

    print(
        json.dumps(
            status,
            ensure_ascii=False,
            indent=2
        )
    )


if __name__ == "__main__":
    main()
