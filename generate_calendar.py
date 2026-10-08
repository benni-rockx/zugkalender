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
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CONFIG = json.loads(Path("config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo(CONFIG.get("timezone", "Europe/Berlin"))
FEED_URL = "https://download.gtfs.de/germany/rv_free/latest.zip"


def fetch_feed() -> zipfile.ZipFile:
    req = Request(FEED_URL, headers={"User-Agent": "zugkalender/2.0"})
    with urlopen(req, timeout=120) as response:
        data = response.read()
    return zipfile.ZipFile(io.BytesIO(data))


def read_csv(zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    with zf.open(filename) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def gtfs_time(value: str, service_day: date) -> datetime:
    """GTFS-Zeit; Werte >=24:00 dürfen bis in den Folgetag reichen."""
    h, m, s = map(int, value.split(":"))
    return datetime.combine(service_day, datetime.min.time(), TZ) + timedelta(
        hours=h, minutes=m, seconds=s
    )


def hm(value: str):
    return datetime.strptime(value, "%H:%M").time()


def in_window(dt: datetime, start: str, end: str) -> bool:
    return hm(start) <= dt.timetz().replace(tzinfo=None) <= hm(end)


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_dt(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y%m%dT%H%M%S")


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def active_dates(calendar_rows, calendar_dates_rows, start, end):
    active = set()
    for row in calendar_rows:
        s = datetime.strptime(row["start_date"], "%Y%m%d").date()
        e = datetime.strptime(row["end_date"], "%Y%m%d").date()
        lo, hi = max(start, s), min(end, e)
        if lo > hi:
            continue
        weekday_keys = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for d in date_range(lo, hi):
            if row[weekday_keys[d.weekday()]] == "1":
                active.add((row["service_id"], d))
    for row in calendar_dates_rows:
        d = datetime.strptime(row["date"], "%Y%m%d").date()
        if not (start <= d <= end):
            continue
        key = (row["service_id"], d)
        if row["exception_type"] == "1":
            active.add(key)
        elif row["exception_type"] == "2":
            active.discard(key)
    return active


def station_ids(stops, station_name):
    target = " ".join(station_name.lower().replace("(main)", "").split())
    ids = set()
    # Erst exakte Stop-/Stationsnamen. GTFS kann mehrere Bahnsteige enthalten.
    for s in stops:
        name = " ".join(s.get("stop_name", "").lower().split())
        if name == " ".join(station_name.lower().split()):
            ids.add(s["stop_id"])
    # Fallback für Varianten wie Frankfurt(Main)Hbf.
    if not ids:
        for s in stops:
            name = " ".join(s.get("stop_name", "").lower().replace("(main)", "").split())
            if name == target:
                ids.add(s["stop_id"])
    if not ids:
        raise RuntimeError(f"Bahnhof nicht im GTFS-Feed gefunden: {station_name}")
    return ids


def main():
    horizon = int(CONFIG.get("horizon_days", 35))
    today = datetime.now(TZ).date()
    end = today + timedelta(days=horizon)

    with fetch_feed() as zf:
        stops = read_csv(zf, "stops.txt")
        trips = read_csv(zf, "trips.txt")
        stop_times = read_csv(zf, "stop_times.txt")
        calendar = read_csv(zf, "calendar.txt") if "calendar.txt" in zf.namelist() else []
        calendar_dates = read_csv(zf, "calendar_dates.txt") if "calendar_dates.txt" in zf.namelist() else []
        routes = read_csv(zf, "routes.txt") if "routes.txt" in zf.namelist() else []

    from_ids = station_ids(stops, CONFIG["from"]["name"])
    to_ids = station_ids(stops, CONFIG["to"]["name"])

    trip_by_id = {t["trip_id"]: t for t in trips}
    route_by_id = {r["route_id"]: r for r in routes}

    # Stopzeiten je Trip; nur die beiden relevanten Bahnhöfe werden benötigt.
    relevant = {}
    for st in stop_times:
        sid = st.get("stop_id")
        if sid not in from_ids and sid not in to_ids:
            continue
        tid = st["trip_id"]
        relevant.setdefault(tid, []).append(st)

    # Nur Trips behalten, die beide Bahnhöfe in der richtigen Reihenfolge bedienen.
    pairs = []
    for tid, rows in relevant.items():
        trip = trip_by_id.get(tid)
        if not trip:
            continue
        try:
            ordered = sorted(rows, key=lambda r: int(r.get("stop_sequence", "0")))
        except ValueError:
            ordered = rows
        from_rows = [r for r in ordered if r.get("stop_id") in from_ids]
        to_rows = [r for r in ordered if r.get("stop_id") in to_ids]
        if from_rows and to_rows:
            pairs.append((tid, trip, from_rows, to_rows))

    active = active_dates(calendar, calendar_dates, today, end)
    events = []
    counts = {"outbound": 0, "return": 0}

    for d in date_range(today, end):
        if d.weekday() >= 5:
            continue
        for direction, origin_ids, destination_ids, window in [
            ("outbound", from_ids, to_ids, CONFIG["morning"]),
            ("return", to_ids, from_ids, CONFIG["afternoon"]),
        ]:
            for tid, trip, origin_rows, destination_rows in pairs:
                if (trip.get("service_id"), d) not in active:
                    continue
                # Richtige Richtung anhand der Stop-Reihenfolge bestimmen.
                for o in origin_rows:
                    for dest in destination_rows:
                        if int(o.get("stop_sequence", 0)) >= int(dest.get("stop_sequence", 0)):
                            continue
                        dep_raw = o.get("departure_time") or o.get("arrival_time")
                        arr_raw = dest.get("arrival_time") or dest.get("departure_time")
                        if not dep_raw or not arr_raw:
                            continue
                        dep = gtfs_time(dep_raw, d)
                        arr = gtfs_time(arr_raw, d)
                        if not in_window(dep, window["departure_start"], window["departure_end"]):
                            continue
                        if arr <= dep:
                            continue

                        route = route_by_id.get(trip.get("route_id", ""), {})
                        route_name = route.get("route_short_name") or route.get("route_long_name") or "Zug"
                        headsign = trip.get("trip_headsign", "")
                        service_trip = trip.get("trip_id", tid)
                        summary = f"{route_name}: {CONFIG[('from' if direction == 'outbound' else 'to')]['name']} → {CONFIG[('to' if direction == 'outbound' else 'from')]['name']}"
                        uid = f"{d:%Y%m%d}-{direction}-{tid}-{o.get('stop_sequence')}-{dest.get('stop_sequence')}@zugkalender"
                        desc = (
                            f"Linie: {route_name}\\n"
                            f"Abfahrt: {dep:%H:%M}\\n"
                            f"Ankunft: {arr:%H:%M}\\n"
                            f"Ziel/Headsign: {headsign}\\n"
                            f"Quelle: GTFS für Deutschland / Schienenregionalverkehr\\n"
                            f"Trip-ID: {service_trip}"
                        )
                        event = "\r\n".join([
                            "BEGIN:VEVENT",
                            f"UID:{esc(uid)}",
                            f"DTSTAMP:{datetime.now(TZ).strftime('%Y%m%dT%H%M%S')}",
                            f"DTSTART;TZID=Europe/Berlin:{ics_dt(dep)}",
                            f"DTEND;TZID=Europe/Berlin:{ics_dt(arr)}",
                            f"SUMMARY:{esc(summary)}",
                            f"DESCRIPTION:{esc(desc)}",
                            f"LOCATION:{esc(CONFIG[('from' if direction == 'outbound' else 'to')]['name'])}",
                            "STATUS:CONFIRMED",
                            "TRANSP:OPAQUE",
                            "END:VEVENT",
                        ])
                        events.append(event)
                        counts[direction] += 1
                        break
                    else:
                        continue
                    break

    # Doppelte Events vermeiden.
    events = list(dict.fromkeys(events))
    header = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zugkalender Niederweimar Frankfurt//DE",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:" + esc(CONFIG["calendar_name"]),
        "X-WR-TIMEZONE:Europe/Berlin",
    ]
    Path("docs/zugkalender.ics").write_text("\r\n".join(header + events + ["END:VCALENDAR"]) + "\r\n", encoding="utf-8")
    status = {
        "generated_at": datetime.now(TZ).isoformat(),
        "valid_from": today.isoformat(), "valid_until": end.isoformat(),
        "events": len(events), "outbound_events": counts["outbound"], "return_events": counts["return"],
        "source": FEED_URL, "source_license": "Creative Commons 4.0",
        "stations": {"from": CONFIG["from"], "to": CONFIG["to"]},
    }
    Path("docs/status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
