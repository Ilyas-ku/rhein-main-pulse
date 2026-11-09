"""Destinations from OpenStreetMap (Overpass) inside the transit box -> pois.json.

Each POI gets a category and a weight:
  work     office / shop / craft / amenity / industrial-commercial area (ha) / airport terminal
  school   amenity=school
  uni      amenity=university|college
  shop     shop=*
  errand   doctors, pharmacies, banks, post, town halls, hairdressers and similar services
  escort   schools and kindergartens (taking someone there)
  leisure  parks, sports, gyms, restaurants, cafes, bars, cinemas, theatres
"""
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

S, W, N, E = 49.85, 8.20, 50.35, 9.00
BB = f"{S},{W},{N},{E}"
PARTS = [
    'nwr["office"]', 'nwr["shop"]', 'nwr["craft"]',
    'nwr["amenity"~"^(school|university|college|hospital|townhall|restaurant|cafe|bar|pub|fast_food|cinema|theatre|kindergarten|bank|pharmacy|doctors|dentist|clinic|post_office)$"]',
    'nwr["leisure"~"^(park|sports_centre|fitness_centre|pitch|swimming_pool|stadium)$"]',
    'way["landuse"~"^(industrial|commercial|retail)$"]', 'nwr["aeroway"="terminal"]',
]
QUERY = '[out:json][timeout:300];{sel}({bb});out tags bb;'



def area_ha(e):
    b = e.get("bounds")
    if not b:
        return 0
    h = (b["maxlat"] - b["minlat"]) * 110574
    w = (b["maxlon"] - b["minlon"]) * 111320 * math.cos(math.radians(50.1))
    return h * w / 1e4 * 0.6                      # bbox overstates a polygon's area


def classify(t, e):
    out = []
    am, le, lu = t.get("amenity"), t.get("leisure"), t.get("landuse")
    if "office" in t: out.append(("work", 4))
    if "craft" in t: out.append(("work", 2))
    if "shop" in t:
        out += [("work", 2), ("shop", 3 if t["shop"] in ("supermarket", "mall", "department_store") else 1)]
    if am == "school": out += [("school", 1), ("work", 5)]
    if am in ("university", "college"): out += [("uni", 1 + area_ha(e)), ("work", 20)]
    if am == "hospital": out.append(("work", 40))
    if am in ("townhall", "bank", "pharmacy", "doctors", "dentist", "clinic", "post_office"):
        out += [("work", 3), ("errand", 1)]
    if t.get("shop") in ("hairdresser", "beauty", "optician", "laundry", "dry_cleaning", "copyshop"): out.append(("errand", 1))
    if am in ("kindergarten", "school"): out.append(("escort", 1))                 # taking children there
    if am == "kindergarten": out.append(("work", 3))
    if am in ("restaurant", "cafe", "bar", "pub", "fast_food", "cinema", "theatre"):
        out += [("leisure", 1), ("work", 2)]
    if le in ("park", "sports_centre", "fitness_centre", "pitch", "swimming_pool", "stadium"):
        out.append(("leisure", 2 if le == "park" else 1))
    if lu in ("industrial", "commercial", "retail"): out.append(("work", min(3 * area_ha(e), 600)))
    if t.get("aeroway") == "terminal": out.append(("work", -1))     # sized in main()
    return out


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://overpass-api.de/api/interpreter"
    els, seen = [], set()
    Path("cache").mkdir(exist_ok=True)
    for k, sel in enumerate(PARTS):                # one query per selector: mirrors time out on the union
        cache = Path("cache") / f"part{k}.json"
        if cache.exists():
            part = json.load(open(cache))
            for e in part:
                if (e["type"], e["id"]) not in seen:
                    seen.add((e["type"], e["id"])); els.append(e)
            continue
        data = urllib.parse.urlencode({"data": QUERY.format(sel=sel, bb=BB)}).encode()
        for attempt in range(5):
            try:
                part = json.loads(urllib.request.urlopen(url, data=data, timeout=600).read())["elements"]
                break
            except Exception as ex:                # Overpass is busy at times
                print("retry", sel[:30], attempt, ex); time.sleep(15 * (attempt + 1))
        else:
            sys.exit(f"no answer for {sel}")
        json.dump(part, open(cache, "w"))
        for e in part:
            if (e["type"], e["id"]) not in seen:
                seen.add((e["type"], e["id"])); els.append(e)
        print(sel[:40], len(part))
    pois = []
    for e in els:
        b = e.get("bounds")                        # ways and relations: middle of the bounding box
        lat = e.get("lat") or (b and (b["minlat"] + b["maxlat"]) / 2)
        lon = e.get("lon") or (b and (b["minlon"] + b["maxlon"]) / 2)
        if lat is None:
            continue
        for cat, w in classify(e.get("tags", {}), e):
            if w > 0 or w == -1:
                pois.append([cat, round(lat, 5), round(lon, 5), round(w, 2)])
    # Frankfurt Airport, roughly 80k jobs: about 5% of the box's work weight, split over its terminals;
    # terminals of small airfields count like an ordinary workplace
    fra = lambda p: 50.02 < p[1] < 50.06 and 8.52 < p[2] < 8.61
    rest = sum(p[3] for p in pois if p[0] == "work" and p[3] > 0)
    nfra = sum(1 for p in pois if p[3] == -1 and fra(p))
    for p in pois:
        if p[3] == -1:
            p[3] = round(0.05 / 0.95 * rest / nfra, 2) if fra(p) else 5
    json.dump(pois, open("pois.json", "w"))
    from collections import Counter
    print(len(els), "elements", Counter(p[0] for p in pois))


if __name__ == "__main__":
    main()
