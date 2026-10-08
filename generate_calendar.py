import csv
import io
import json
import zipfile
from datetime import date, datetime, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


FEED_URL = "https://download.gtfs.de/germany/rv_free/latest.zip"
TIMEZONE = ZoneInfo("Europe/Berlin")

with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)


def fetch_feed():
    print("Lade aktuellen GTFS-Fahrplan ...")

    request = Request(
        FEED_URL,
        headers={"User-Agent": "zugkalender/1.0"}
    )

    with urlopen(request, timeout=60) as response:
        data = response.read()

    print(f"GTFS-Download: {len(data) / 1024 / 1024:.1f} MB")

    return zipfile.ZipFile(io.BytesIO(data))


def read_csv(zf, filename):
    with zf.open(filename) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig")
        return list(csv.DictReader(text))


def normalize(text):
    if not text:
        return ""

    return (
        text.lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
        .replace("(", "")
        .replace(")", "")
        .replace(".", "")
    )


def station_ids(stops, station_name, config_station=None):
    """
    Sucht einen Bahnhof robust über:
    1. stop_id / station_id aus der Konfiguration
    2. exakte Schreibweise
    3. normalisierte Schreibweise
    """

    # 1. Falls eine bekannte GTFS-/Stations-ID angegeben ist
    if config_station:
        candidates = {
            str(config_station),
            str(config_station).strip(),
        }

        for stop in stops:
            if str(stop.get("stop_id", "")) in candidates:
                print(
                    f"Bahnhof gefunden über ID: "
                    f"{station_name} -> {stop.get('stop_id')} "
                    f"({stop.get('stop_name')})"
                )
                return {stop["stop_id"]}

    # 2. Exakte Schreibweise
    exact = set()

    for stop in stops:
        if stop.get("stop_name", "").strip().lower() == station_name.strip().lower():
            exact.add(stop["stop_id"])

    if exact:
        print(f"Bahnhof gefunden: {station_name}")
        return exact

    # 3. Robuster Vergleich ohne Leerzeichen/Sonderzeichen
    wanted = normalize(station_name)

    matches = set()

    for stop in stops:
        stop_name = normalize(stop.get("stop_name", ""))

        if stop_name == wanted:
            matches.add(stop["stop_id"])

    if matches:
        print(f"Bahnhof gefunden (normalisierte Suche): {station_name}")

        for stop in stops:
            if stop["stop_id"] in matches:
                print(
                    f"  -> {stop.get('stop_name')} "
                    f"[{stop.get('stop_id')}]"
                )

        return matches

    # 4. Teiltreffer als letzte Möglichkeit
    partial = set()

    for stop in stops:
        stop_name = normalize(stop.get("stop_name", ""))

        if wanted in stop_name or stop_name in wanted:
            partial.add(stop["stop_id"])

    if partial:
        print(f"Bahnhof gefunden (Teiltreffer): {station_name}")

        for stop in stops:
            if stop["stop_id"] in partial:
                print(
                    f"  -> {stop.get('stop_name')} "
                    f"[{stop.get('stop_id')}]"
                )

        return partial

    raise RuntimeError(
        f"Bahnhof nicht im GTFS-Feed gefunden: {station_name}"
    )


def parse_time(value):
    """
    GTFS erlaubt auch Zeiten > 24:00:00.
    """

    parts = value.split(":")

    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2])

    return timedelta(
        hours=hours,
        minutes=minutes,
        seconds=seconds
    )


def active_dates(calendar_rows, calendar_dates_rows, start_date, end_date):
    active = set()

    calendar = {
        row["service_id"]: row
        for row in calendar_rows
    }

    current = start_date

    while current <= end_date:

        weekday = current.weekday()

        for service_id, row in calendar.items():

            start = datetime.strptime(
                row["start_date"], "%Y%m%d"
            ).date()

            end = datetime.strptime(
                row["end_date"], "%Y%m%d"
            ).date()

            if not (start <= current <= end):
                continue

            weekday_columns = [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ]

            if row[weekday_columns[weekday]] == "1":
                active.add((current, service_id))

        current += timedelta(days=1)

    # Ausnahmen
    for row in calendar_dates_rows:

        d = datetime.strptime(
            row["date"], "%Y%m%d"
        ).date()

        if not (start_date <= d <= end_date):
            continue

        key = (d, row["service_id"])

        # 1 = zusätzlich
        if row["exception_type"] == "1":
            active.add(key)

        # 2 = entfernen
        elif row["exception_type"] == "2":
            active.discard(key)

    return active


def format_ics_datetime(dt):
    return dt.astimezone(TIMEZONE).strftime("%Y%m%dT%H%M%S")


def escape_ics(text):
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def main():

    zf = fetch_feed()

    print("Lese GTFS-Dateien ...")

    stops = read_csv(zf, "stops.txt")
    stop_times = read_csv(zf, "stop_times.txt")
    trips = read_csv(zf, "trips.txt")

    try:
        calendar = read_csv(zf, "calendar.txt")
    except KeyError:
        calendar = []

    try:
        calendar_dates = read_csv(zf, "calendar_dates.txt")
    except KeyError:
        calendar_dates = []

    try:
        routes = read_csv(zf, "routes.txt")
    except KeyError:
        routes = []

    print(f"Stops: {len(stops)}")
    print(f"Trips: {len(trips)}")
    print(f"Stop times: {len(stop_times)}")

    # ---------------------------------------------------------
    # Bahnhöfe
    # ---------------------------------------------------------

    from_config = CONFIG["stations"]

    from_station = from_config["from"]
    to_station = from_config["to"]

    from_ids = station_ids(
        stops,
        from_station["name"],
        from_station.get("id")
    )

    to_ids = station_ids(
        stops,
        to_station["name"],
        to_station.get("id")
    )

    print()
    print("Startbahnhof:", from_station["name"])
    print("Zielbahnhof:", to_station["name"])
    print("Start-IDs:", from_ids)
    print("Ziel-IDs:", to_ids)
    print()

    # ---------------------------------------------------------
    # Stop Times vorbereiten
    # ---------------------------------------------------------

    stop_times_by_trip = {}

    for row in stop_times:

        trip_id = row["trip_id"]

        stop_times_by_trip.setdefault(
            trip_id,
            []
        ).append(row)

    # ---------------------------------------------------------
    # Trips
    # ---------------------------------------------------------

    trip_by_id = {
        row["trip_id"]: row
        for row in trips
    }

    route_by_id = {
        row["route_id"]: row
        for row in routes
    }

    # ---------------------------------------------------------
    # Zeitraum
    # ---------------------------------------------------------

    today = date.today()

    end_date = today + timedelta(days=35)

    active = active_dates(
        calendar,
        calendar_dates,
        today,
        end_date
    )

    print(
        f"Erzeuge Kalender für "
        f"{today} bis {end_date}"
    )

    events = []

    # ---------------------------------------------------------
    # Verbindungen suchen
    # ---------------------------------------------------------

    for trip_id, rows in stop_times_by_trip.items():

        if trip_id not in trip_by_id:
            continue

        trip = trip_by_id[trip_id]

        service_id = trip.get("service_id")

        # Für jeden Fahrplantag
        for current_date in (
            today + timedelta(days=i)
            for i in range((end_date - today).days + 1)
        ):

            # Nur Montag bis Freitag
            if current_date.weekday() >= 5:
                continue

            if (current_date, service_id) not in active:
                continue

            from_row = None
            to_row = None

            for row in rows:

                stop_id = row["stop_id"]

                if stop_id in from_ids:
                    from_row = row

                if stop_id in to_ids:
                    to_row = row

            if not from_row or not to_row:
                continue

            try:
                from_sequence = int(
                    from_row["stop_sequence"]
                )

                to_sequence = int(
                    to_row["stop_sequence"]
                )
            except ValueError:
                continue

            # Bahnhof muss in der richtigen Reihenfolge liegen
            if from_sequence >= to_sequence:
                continue

            departure = from_row["departure_time"]
            arrival = to_row["arrival_time"]

            dep_delta = parse_time(departure)
            arr_delta = parse_time(arrival)

            dep_dt = datetime.combine(
                current_date,
                datetime.min.time(),
                tzinfo=TIMEZONE
            ) + dep_delta

            arr_dt = datetime.combine(
                current_date,
                datetime.min.time(),
                tzinfo=TIMEZONE
            ) + arr_delta

            # -------------------------------------------------
            # Verbindungstyp / Richtung
            # -------------------------------------------------

            route_id = trip.get("route_id", "")
            route = route_by_id.get(route_id, {})

            route_short_name = route.get(
                "route_short_name",
                ""
            )

            route_long_name = route.get(
                "route_long_name",
                ""
            )

            # -------------------------------------------------
            # Prüfen, ob Verbindung in eines der gewünschten
            # Zeitfenster fällt
            # -------------------------------------------------

            windows = CONFIG["windows"]

            matched_window = None

            for window in windows:

                start = parse_time(
                    window["start"]
                )

                end = parse_time(
                    window["end"]
                )

                departure_time = dep_dt.timetz()

                start_time = (
                    datetime.combine(
                        current_date,
                        datetime.min.time(),
                        tzinfo=TIMEZONE
                    ) + start
                ).timetz()

                end_time = (
                    datetime.combine(
                        current_date,
                        datetime.min.time(),
                        tzinfo=TIMEZONE
                    ) + end
                ).timetz()

                if start_time <= departure_time <= end_time:

                    matched_window = window
                    break

            if not matched_window:
                continue

            # -------------------------------------------------
            # Kalender-Event
            # -------------------------------------------------

            direction = matched_window.get(
                "label",
                "Zugverbindung"
            )

            title = (
                f"{route_short_name} "
                f"{from_station['name']} → "
                f"{to_station['name']}"
            ).strip()

            if not route_short_name:
                title = (
                    f"Zug "
                    f"{from_station['name']} → "
                    f"{to_station['name']}"
                )

            description_parts = [
                f"Abfahrt: {from_station['name']}",
                f"Ankunft: {to_station['name']}",
            ]

            if route_short_name:
                description_parts.append(
                    f"Linie: {route_short_name}"
                )

            if route_long_name:
                description_parts.append(
                    route_long_name
                )

            description = "\\n".join(
                description_parts
            )

            uid = (
                f"{trip_id}-"
                f"{current_date.isoformat()}@zugkalender"
            )

            events.append({
                "uid": uid,
                "start": dep_dt,
                "end": arr_dt,
                "title": title,
                "description": description,
            })

    # ---------------------------------------------------------
    # Doppelte Einträge entfernen
    # ---------------------------------------------------------

    unique = {}

    for event in events:
        unique[event["uid"]] = event

    events = list(unique.values())

    events.sort(
        key=lambda x: x["start"]
    )

    print(
        f"{len(events)} Zugverbindungen gefunden."
    )

    # ---------------------------------------------------------
    # ICS schreiben
    # ---------------------------------------------------------

    now = datetime.now(TIMEZONE)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Zugkalender//Niederweimar Frankfurt//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Niederweimar ↔ Frankfurt",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for event in events:

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{event['uid']}",
            f"DTSTAMP:{format_ics_datetime(now)}",
            f"DTSTART:{format_ics_datetime(event['start'])}",
            f"DTEND:{format_ics_datetime(event['end'])}",
            f"SUMMARY:{escape_ics(event['title'])}",
            f"DESCRIPTION:{escape_ics(event['description'])}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")

    with open(
        "docs/zugkalender.ics",
        "w",
        encoding="utf-8",
        newline="\r\n"
    ) as f:

        f.write("\r\n".join(lines))

    # ---------------------------------------------------------
    # Statusdatei
    # ---------------------------------------------------------

    status = {
        "updated": now.isoformat(),
        "from": from_station["name"],
        "to": to_station["name"],
        "events": len(events),
        "period_start": today.isoformat(),
        "period_end": end_date.isoformat(),
        "source": FEED_URL,
    }

    with open(
        "docs/status.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            status,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("Kalender erfolgreich geschrieben.")


if __name__ == "__main__":
    main()
