"""Destinations from OpenStreetMap (Overpass) inside the transit box -> pois.json.

Each POI: [category, lat, lon, weight, open Tue, open Sat, open Sun].
Opening is read from the opening_hours tag where there is one; where there is none, it is the share
of tagged POIs of the same kind that open on that day (a probability, used as a weight factor).

  work     office / shop / craft / amenity / industrial-commercial area (ha) / airport terminal
  school, uni, escort (schools and kindergartens)
  shop     shops and petrol stations
  errand   banks, post, town halls, hairdressers and similar services
  doctor   doctors, dentists, clinics, hospitals
  sport    sports centres, gyms, pitches, pools
  eat      restaurants, cafés, bars, pubs, fast food, beer gardens
  culture  cinemas, theatres, museums, arts centres, stadiums, event venues, zoos
  church   places of worship, cemeteries
  forest, fields, park, lake, river, hills, zoo   places for a walk (forests, meadows and farmland, parks,
           lakes, river banks, peaks, zoos and botanical gardens)
  centre   pedestrian streets, for strolls in town
  daytrip  attractions, viewpoints, zoos, theme parks, castles, nature reserves
"""
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

S, W, N, E = 49.85, 8.20, 50.35, 9.00
BB = f"{S},{W},{N},{E}"
PARTS = [
    'nwr["office"]', 'nwr["shop"]', 'nwr["craft"]',
    'nwr["amenity"~"^(school|university|college|hospital|townhall|restaurant|cafe|bar|pub|fast_food|cinema|theatre|kindergarten|bank|pharmacy|doctors|dentist|clinic|post_office)$"]',
    'nwr["leisure"~"^(park|sports_centre|fitness_centre|pitch|swimming_pool|stadium)$"]',
    'way["landuse"~"^(industrial|commercial|retail)$"]', 'nwr["aeroway"="terminal"]',
    'nwr["amenity"~"^(place_of_worship|grave_yard|fuel|arts_centre|biergarten|events_venue)$"]',
    'nwr["tourism"~"^(museum|attraction|viewpoint|zoo|theme_park)$"]',
    'nwr["leisure"="nature_reserve"]', 'way["landuse"~"^(cemetery|forest)$"]', 'nwr["historic"="castle"]',
    # where people go for a walk (BfN Naturbewusstsein 2009) and city-centre strolls
    'way["landuse"~"^(meadow|farmland)$"]', 'nwr["natural"="water"]["water"~"^(lake|reservoir|pond)$"]',
    'RIVER', 'node["natural"="peak"]', 'nwr["leisure"="garden"]["garden:type"="botanical"]',
    'way["highway"="pedestrian"]', 'way["natural"="wood"]',
]
QUERY = '[out:json][timeout:300];{sel}({bb});out tags bb;'
# river banks: the nodes of the river lines, every one of them a point on a bank
RIVER_QUERY = '[out:json][timeout:300];way["waterway"="river"]({bb});node(w);out;'
DAYS = ["Tu", "Sa", "Su"]
WD = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH = 11                                         # the modelled days are in November


def area_ha(e):
    b = e.get("bounds")
    if not b:
        return 0
    h = (b["maxlat"] - b["minlat"]) * 110574
    w = (b["maxlon"] - b["minlon"]) * 111320 * math.cos(math.radians(50.1))
    return h * w / 1e4 * 0.6                      # bbox overstates a polygon's area


def open_days(oh):
    """Weekdays an opening_hours value opens on, or None if it cannot be read. Later rules win."""
    oh = oh.strip()
    if not oh:
        return None
    if oh.startswith("24/7"):
        return set(WD)
    days = {}
    # a comma after a time or "off" starts a new rule ("Fr,Sa 06:00-05:00, Su 06:00-03:00")
    oh = re.sub(r"(\d|\+|off|closed)\s*,\s*(?=(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)\b)", r"\1;", oh)
    rules = oh.split(";")
    for r in rules:
        r = r.strip().strip('"')
        if not r:
            continue
        mon = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?:\s*\d{1,2})?"
        m = re.match(rf"^({mon}(?:\s*-\s*{mon})?)\s*:?\s*", r)
        if m:                                       # month range: keep the rule only if it covers November
            ms = re.findall(r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec", m.group(1))
            a = MONTHS.index(ms[0]) + 1; b = MONTHS.index(ms[-1]) + 1
            inside = a <= MONTH <= b if a <= b else (MONTH >= a or MONTH <= b)
            if not inside:
                continue
            r = r[m.end():]
        m = re.match(r"^((?:Mo|Tu|We|Th|Fr|Sa|Su|PH)(?:\s*-\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))?(?:\s*,\s*(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)(?:\s*-\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))?)*)\s*(.*)$", r)
        sel, rest = (m.group(1), m.group(2)) if m else (None, r)
        ds = set()
        if sel is None:
            ds = set(WD)
        else:
            for part in sel.split(","):
                part = part.strip()
                if "-" in part:
                    a, b = [x.strip() for x in part.split("-")]
                    i, j = WD.index(a), WD.index(b)
                    ds |= set(WD[i:j + 1] if i <= j else WD[i:] + WD[:j + 1])
                elif part in WD:
                    ds.add(part)
        if re.search(r"\b(off|closed)\b", rest):
            state = False
        elif re.search(r"\d{1,2}:\d{2}|\+", rest) or rest == "":
            state = True
        else:
            continue                                # comments such as "nach Vereinbarung"
        for d in ds:
            days[d] = state
    if not days:
        return None
    return {d for d, s in days.items() if s}


def kind_of(t):
    """Group used to share opening behaviour between tagged and untagged POIs."""
    for k in ("shop", "amenity", "leisure", "tourism", "historic", "natural", "highway"):
        if k in t:
            return f"{k}:{t[k]}"
    if "office" in t: return "office"
    if "craft" in t: return "craft"
    if "aeroway" in t: return "aeroway"
    if "waterway" in t: return "waterway"
    return f"landuse:{t.get('landuse', '?')}"


def classify(t, e):
    out = []
    am, le, lu, to = t.get("amenity"), t.get("leisure"), t.get("landuse"), t.get("tourism")
    if "office" in t: out.append(("work", 4))
    if "craft" in t: out.append(("work", 2))
    if "shop" in t:
        out += [("work", 2), ("shop", 3 if t["shop"] in ("supermarket", "mall", "department_store") else 1)]
        if t["shop"] in ("hairdresser", "beauty", "optician", "laundry", "dry_cleaning", "copyshop"): out.append(("errand", 1))
    if am == "fuel": out += [("shop", 1), ("work", 2)]
    if am == "school": out += [("school", 1), ("work", 5)]
    if am in ("university", "college"): out += [("uni", 1 + area_ha(e)), ("work", 20)]
    if am in ("kindergarten", "school"): out.append(("escort", 1))
    if am == "kindergarten": out.append(("work", 3))
    if am == "hospital": out += [("work", 40), ("doctor", 5)]
    if am in ("doctors", "dentist", "clinic"): out += [("work", 3), ("doctor", 1)]
    if am in ("townhall", "bank", "pharmacy", "post_office"): out += [("work", 3), ("errand", 1)]
    if am in ("restaurant", "cafe", "bar", "pub", "fast_food", "biergarten"): out += [("eat", 1), ("work", 2)]
    if am in ("cinema", "theatre", "arts_centre", "events_venue") or to == "museum" or le == "stadium":
        out += [("culture", 3 if le == "stadium" else 1), ("work", 2)]
    if le in ("sports_centre", "fitness_centre", "pitch", "swimming_pool"):
        out.append(("sport", 2 if le in ("sports_centre", "fitness_centre", "swimming_pool") else 1))
    if am in ("place_of_worship", "grave_yard") or lu == "cemetery": out.append(("church", 1))
    nat, wt = t.get("natural"), t.get("water")
    big = lambda: min(1 + area_ha(e) / 10, 50)
    if lu == "forest" or nat == "wood" or le == "nature_reserve": out.append(("forest", big()))
    if lu in ("meadow", "farmland"): out.append(("fields", big()))
    if le == "park": out.append(("park", big()))
    if nat == "water" and wt in ("lake", "reservoir", "pond") and area_ha(e) >= 1: out.append(("lake", big()))
    if t.get("waterway") == "river_bank": out.append(("river", 1))
    if nat == "peak": out.append(("hills", 3 if float(re.sub(r"[^0-9.]", "", t.get("ele", "0")) or 0) > 400 else 1))
    if to == "zoo" or t.get("garden:type") == "botanical": out.append(("zoo", 3))
    if t.get("highway") == "pedestrian": out.append(("centre", big()))
    if to in ("attraction", "viewpoint", "zoo", "theme_park") or t.get("historic") == "castle" or le == "nature_reserve":
        out.append(("daytrip", 3 if to in ("zoo", "theme_park") else 1))
        if to in ("zoo", "theme_park"): out.append(("culture", 2))
    if lu in ("industrial", "commercial", "retail"): out.append(("work", min(3 * area_ha(e), 600)))
    if t.get("aeroway") == "terminal": out.append(("work", -1))     # sized in main()
    return out


def fetch(url):
    els, seen = [], set()
    Path("cache").mkdir(exist_ok=True)
    for k, sel in enumerate(PARTS):                # one query per selector: mirrors time out on the union
        cache = Path("cache") / f"part{k}.json"
        if cache.exists():
            part = json.load(open(cache))
        else:
            q = RIVER_QUERY.format(bb=BB) if sel == "RIVER" else QUERY.format(sel=sel, bb=BB)
            data = urllib.parse.urlencode({"data": q}).encode()
            for attempt in range(5):
                try:
                    part = json.loads(urllib.request.urlopen(url, data=data, timeout=600).read())["elements"]
                    break
                except Exception as ex:            # Overpass is busy at times
                    print("retry", sel[:30], attempt, ex); time.sleep(15 * (attempt + 1))
            else:
                sys.exit(f"no answer for {sel}")
            if sel == "RIVER":
                part = [dict(e, tags={"waterway": "river_bank"}) for e in part[::4]]
            json.dump(part, open(cache, "w"))
            print(sel[:40], len(part))
        for e in part:
            if (e["type"], e["id"]) not in seen:
                seen.add((e["type"], e["id"])); els.append(e)
    return els


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://overpass-api.de/api/interpreter"
    els = fetch(url)

    # opening by kind, from the POIs that carry a readable opening_hours tag
    parsed = {}
    share = defaultdict(lambda: [0, 0, 0, 0])      # kind -> [n, open Tu, open Sa, open Su]
    for e in els:
        t = e.get("tags", {})
        od = open_days(t["opening_hours"]) if "opening_hours" in t else None
        parsed[(e["type"], e["id"])] = od
        if od is not None:
            s = share[kind_of(t)]; s[0] += 1
            for i, d in enumerate(DAYS): s[i + 1] += d in od
    # kinds with few tags borrow from a related kind; places that are open daily by nature stay open
    always = ("leisure:park", "leisure:nature_reserve", "landuse:forest", "landuse:cemetery", "amenity:grave_yard",
              "landuse:meadow", "landuse:farmland", "natural:water", "natural:wood", "natural:peak", "waterway",
              "highway:pedestrian", "leisure:garden",
              "amenity:place_of_worship", "tourism:viewpoint", "tourism:attraction", "historic:castle", "aeroway",
              "amenity:hospital", "leisure:pitch")
    borrow = {"landuse:industrial": "craft", "landuse:commercial": "office", "landuse:retail": "shop:supermarket"}

    def open_prob(e):
        t = e.get("tags", {}); k = kind_of(t)
        if k in always:
            return [1.0, 1.0, 1.0]
        od = parsed[(e["type"], e["id"])]
        if od is not None:
            return [float(d in od) for d in DAYS]
        s = share.get(borrow.get(k, k))
        if not s or s[0] < 10:                     # rare kinds without tags: an assumption
            return [1.0, 1.0, 1.0] if k.startswith(("leisure", "tourism")) else [1.0, 0.5, 0.2]
        return [round(s[i + 1] / s[0], 2) for i in range(3)]

    pois = []
    for e in els:
        b = e.get("bounds")                        # ways and relations: middle of the bounding box
        lat = e.get("lat") or (b and (b["minlat"] + b["maxlat"]) / 2)
        lon = e.get("lon") or (b and (b["minlon"] + b["maxlon"]) / 2)
        if lat is None:
            continue
        op = open_prob(e)
        for cat, w in classify(e.get("tags", {}), e):
            if w > 0 or w == -1:
                pois.append([cat, round(lat, 5), round(lon, 5), round(w, 2), *op])
    # Frankfurt Airport, roughly 80k jobs: about 5% of the box's work weight, split over its terminals;
    # terminals of small airfields count like an ordinary workplace
    fra = lambda p: 50.02 < p[1] < 50.06 and 8.52 < p[2] < 8.61
    rest = sum(p[3] for p in pois if p[0] == "work" and p[3] > 0)
    nfra = sum(1 for p in pois if p[3] == -1 and fra(p))
    for p in pois:
        if p[3] == -1:
            p[3] = round(0.05 / 0.95 * rest / nfra, 2) if fra(p) else 5
    json.dump(pois, open("pois.json", "w"), separators=(",", ":"))

    from collections import Counter
    print(len(els), "elements", Counter(p[0] for p in pois))
    for k in ("shop:supermarket", "shop:bakery", "shop:clothes", "amenity:doctors", "amenity:restaurant", "office", "amenity:fuel"):
        s = share.get(k)
        if s: print(f"{k:18s} tagged {s[0]:5d}  open Tue {s[1] / s[0]:.0%}  Sat {s[2] / s[0]:.0%}  Sun {s[3] / s[0]:.0%}")
    for cat in ("shop", "errand", "doctor", "eat", "work"):
        ws = [(p[3], p[4], p[5], p[6]) for p in pois if p[0] == cat]
        tot = sum(w for w, *_ in ws)
        print(f"{cat:7s} weight open  Tue {sum(w * a for w, a, _, _ in ws) / tot:.0%}  Sat {sum(w * b for w, _, b, _ in ws) / tot:.0%}  Sun {sum(w * c for w, _, _, c in ws) / tot:.0%}")


if __name__ == "__main__":
    main()
