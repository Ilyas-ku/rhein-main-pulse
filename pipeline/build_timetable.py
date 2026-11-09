"""Timetables for a Tuesday, a Saturday and a Sunday in the Rhein-Main box, from the gtfs.de feed.

Usage: build_timetable.py FEED_DIR OUT_DIR

  OUT_DIR/stops.json       stop coordinates (shared by all days), base64 float32
  OUT_DIR/c/DAY/HH.json    connections by departure hour, same layout as rhein-main-transit-reach:
                           stop ids u -> v, times in 10 s units, trip id (uint16, base64)
  OUT_DIR/network.json     stop-to-stop segments with their weekday departures (basemap)
"""
import base64
import json
import sys
from pathlib import Path

import duckdb
import numpy as np

FEED, OUT = Path(sys.argv[1]), Path(sys.argv[2])
BOX = (49.85, 8.20, 50.35, 9.00)
DAYS = {"weekday": ("20261117", "tuesday"), "saturday": ("20261121", "saturday"), "sunday": ("20261122", "sunday")}
FROM_H, TO_H = 3, 28


def b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def main():
    con = duckdb.connect()
    con.execute("SET threads=4")
    csv = lambda f: f"read_csv('{FEED / f}', all_varchar=true, header=true)"
    S, W, N, E = BOX
    con.execute(f"""CREATE TABLE stops AS SELECT stop_id, CAST(stop_lat AS DOUBLE) lat, CAST(stop_lon AS DOUBLE) lon
                    FROM {csv('stops.txt')}
                    WHERE CAST(stop_lat AS DOUBLE) BETWEEN {S} AND {N} AND CAST(stop_lon AS DOUBLE) BETWEEN {W} AND {E}""")
    con.execute(f"CREATE TABLE trips AS SELECT trip_id, service_id, route_id FROM {csv('trips.txt')}")
    # only stop_times of trips touching the box, so the day queries below stay small
    con.execute(f"""CREATE TABLE st AS
        SELECT s.trip_id, CAST(s.stop_sequence AS INT) seq, s.stop_id,
               CAST(split_part(coalesce(s.arrival_time, s.departure_time), ':', 1) AS INT) * 3600
             + CAST(split_part(coalesce(s.arrival_time, s.departure_time), ':', 2) AS INT) * 60
             + CAST(split_part(coalesce(s.arrival_time, s.departure_time), ':', 3) AS INT) arr_s,
               CAST(split_part(coalesce(s.departure_time, s.arrival_time), ':', 1) AS INT) * 3600
             + CAST(split_part(coalesce(s.departure_time, s.arrival_time), ':', 2) AS INT) * 60
             + CAST(split_part(coalesce(s.departure_time, s.arrival_time), ':', 3) AS INT) dep_s
        FROM {csv('stop_times.txt')} s
        WHERE s.trip_id IN (SELECT DISTINCT x.trip_id FROM {csv('stop_times.txt')} x JOIN stops USING (stop_id))""")
    # each consecutive pair of a trip, kept when both ends are in the box
    con.execute("""CREATE TABLE hops AS SELECT * FROM (
        SELECT trip_id, stop_id u, dep_s, lead(stop_id) OVER w v, lead(arr_s) OVER w arr_s
        FROM st WINDOW w AS (PARTITION BY trip_id ORDER BY seq))
        WHERE v IS NOT NULL AND u IN (SELECT stop_id FROM stops) AND v IN (SELECT stop_id FROM stops)
          AND arr_s >= dep_s""")
    print("hops", con.execute("SELECT count(*) FROM hops").fetchone()[0])

    used = con.execute("SELECT stop_id, lat, lon FROM stops WHERE stop_id IN (SELECT u FROM hops UNION SELECT v FROM hops) ORDER BY stop_id").fetchall()
    sid = {s: i for i, (s, _, _) in enumerate(used)}
    assert len(used) < 65536
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump({"box": BOX, "n": len(used), "lat": b64(np.array([r[1] for r in used], np.float32)),
               "lon": b64(np.array([r[2] for r in used], np.float32)), "days": {d: v[0] for d, v in DAYS.items()}},
              open(OUT / "stops.json", "w"), separators=(",", ":"))

    for day, (date, wd) in DAYS.items():
        rows = con.execute(f"""
            WITH cal AS (
                SELECT service_id FROM {csv('calendar.txt')}
                WHERE {wd} = '1' AND start_date <= '{date}' AND end_date >= '{date}'
                UNION SELECT service_id FROM {csv('calendar_dates.txt')} WHERE date = '{date}' AND exception_type = '1'
                EXCEPT SELECT service_id FROM {csv('calendar_dates.txt')} WHERE date = '{date}' AND exception_type = '2')
            SELECT h.trip_id, h.u, h.v, h.dep_s, h.arr_s FROM hops h JOIN trips t USING (trip_id)
            WHERE t.service_id IN (SELECT service_id FROM cal) AND h.dep_s >= {FROM_H * 3600} AND h.arr_s <= {TO_H * 3600}
            ORDER BY h.dep_s, h.trip_id""").fetchnumpy()
        u = np.array([sid[x] for x in rows["u"]], np.uint16); v = np.array([sid[x] for x in rows["v"]], np.uint16)
        trip = np.unique(rows["trip_id"], return_inverse=True)[1]
        assert trip.max() < 65536
        dep, arr = rows["dep_s"].astype(np.int64), rows["arr_s"].astype(np.int64)
        (OUT / "c" / day).mkdir(parents=True, exist_ok=True)
        hours = dep // 3600
        for h in range(FROM_H, TO_H):
            m = hours == h
            json.dump({"u": b64(u[m]), "v": b64(v[m]), "dep": b64(np.round(dep[m] / 10).astype(np.uint16)),
                       "arr": b64(np.round(arr[m] / 10).astype(np.uint16)), "trip": b64(trip[m].astype(np.uint16))},
                      open(OUT / "c" / day / f"{h:02d}.json", "w"), separators=(",", ":"))
        print(day, date, len(u), "connections", trip.max() + 1, "trips")
        if day == "weekday":
            a, b = np.minimum(u, v).astype(np.int64), np.maximum(u, v).astype(np.int64)
            keys, n = np.unique(a * 65536 + b, return_counts=True)
            lat = np.array([r[1] for r in used]); lon = np.array([r[2] for r in used])
            seg = []
            for k, c in zip(keys, n):
                i, j = divmod(int(k), 65536)
                seg += [round(lon[i], 5), round(lat[i], 5), round(lon[j], 5), round(lat[j], 5), int(c)]
            json.dump({"seg": seg}, open(OUT / "network.json", "w"), separators=(",", ":"))
            print("segments", len(keys))


if __name__ == "__main__":
    main()
