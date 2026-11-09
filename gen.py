"""One synthetic day (Tuesday, Saturday or Sunday) for N residents of the Rhein-Main box -> data/day_DAY.json.

Homes:        Zensus 2022 population on the ~200 m grid of rhein-main-transit-reach.
People:       MiD 2023 person types, who leaves home, how many trips and for what (survey.py),
              and what kind of place each errand or leisure trip goes to (MiD W4, W5, by day).
Times:        outbound start times fitted so that trip start times by purpose match MiD W7,
              stays from ZVE 2022 (calibrate()).
Destinations: OSM POIs open on that day (pois.json); visits go to homes on the population grid.
              Gravity choice P ~ weight * exp(-d / L), with L fitted per purpose to MiD W12 trip lengths.
Trips:        router.js, earliest arrival on that day's timetable + walking.
The same people (homes, types, workplaces) live all three days: those draws use fixed seeds.
Usage: gen.py DAY [N]    (DAY: weekday, saturday, sunday)
"""
import base64
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import nnls

import survey as S

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DAY = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
D, ALL = S.DAYS[DAY], S.ALL_DAYS
DOW = ["weekday", "saturday", "sunday"].index(DAY)          # column of the POI opening flags
rng = np.random.default_rng(DOW + 7)
LAT0 = 50.1
KX, KY = np.cos(np.radians(LAT0)) * 111320, 110574
H = 3600
TRAVEL = S.W9_MEAN_MIN * 60
DETOUR = 1.3                                                  # network length / straight line

TYPES = list(S.TYPES)
ACTS = ["home", "work", "school", "shop", "errand", "visit", "nature", "park", "water", "centre", "sport", "eat",
        "culture", "church"]                                  # then walk, ride
PLACE_SHOWN = {"forest": "nature", "fields": "nature", "hills": "nature", "park": "park", "zoo": "park",
               "lake": "water", "river": "water"}


def shown(kind):
    """State on the map for a kind of stop ("walk:forest" -> nature)."""
    if ":" in kind:
        return PLACE_SHOWN[kind.split(":")[1]]
    return {"uni": "school", "doctor": "errand", "escort": "errand", "daytrip": "nature"}.get(kind, kind)
ANCHOR_PURPOSE = {"work": "work", "school": "edu", "uni": "edu"}
SECONDARY = ["shop", "errand", "leisure", "escort"]          # W1 columns 3, 4, 5, 6
# decay lengths (m) relative to each other; one factor per purpose is fitted to MiD W12
BASE_DECAY = {"work": 9000, "school": 1500, "uni": 15000, "shop": 1200, "errand": 1500, "doctor": 2000,
              "escort": 1000, "visit": 5000, "sport": 2500, "eat": 3000, "culture": 8000, "church": 2000,
              "centre": 4000, "forest": 3000, "fields": 1500, "park": 1500, "lake": 5000, "river": 2500,
              "hills": 12000, "zoo": 8000}
DOG_REACH = 0.4                                               # dog walks stay closer to home (assumption)
DAYTRIP_KM = (25, 10)                                         # day trips: around 25 km away (assumption)
WEEKEND = "weekday" if DAY == "weekday" else "weekend"
F_SOCIAL = S.ZVE_SOCIAL[WEEKEND] / S.ZVE_SOCIAL["all"]
F_SPORT = S.ZVE_SPORT[WEEKEND] / S.ZVE_SPORT["all"]
STAY_KIND = {"visit": S.ZVE_VISIT_MIN * F_SOCIAL, "culture": S.ZVE_CULTURE_MIN * F_SOCIAL,
             "sport": S.ZVE_EXERCISE_MIN * F_SPORT, "walk": S.ZVE_EXERCISE_MIN * F_SPORT, **S.ASSUMED_MIN}
SHOP_KINDS = ("shop", "errand", "doctor", "centre")           # share the ZVE shopping minutes of the day
STAY_MEAN = {"work": D["work_min"] * 60, "school": S.ZVE_SCHOOL_MIN * 60, "uni": S.ZVE_UNI_MIN * 60}


def kind_mix(parent):
    """Kinds of place for a secondary purpose on this day, from MiD W4 (errands) and W5 (leisure)."""
    if parent in ("shop", "escort"):
        return {parent: 1.0}
    kinds, vals = (S.W4_KINDS, S.W4[DAY]) if parent == "errand" else (S.W5_KINDS, S.W5[DAY])
    mix = {}
    for k, v in zip(kinds, vals):
        mix[k] = mix.get(k, 0) + v
    tot = sum(mix.values())
    return {k: v / tot for k, v in mix.items() if v}


MIX = {p: kind_mix(p) for p in SECONDARY}


def stay_mean_kind(k):
    k = k.split(":")[0]
    return (S.ZVE_SHOP_MIN if k in SHOP_KINDS else STAY_KIND[k]) * 60


def place_mix(groups):
    """Kinds of place for a walk, BfN Naturbewusstsein 2009, averaged over the person's age groups."""
    v = np.mean([S.NB2009[g] for g in groups], axis=0)
    return v / v.sum()


def with_place(kind, groups, r):
    """Walks and dog walks get a kind of place: "walk" -> "walk:forest"."""
    if kind not in ("walk", "dog"):
        return kind
    return f"{kind}:{S.NB2009_PLACES[int(r.choice(len(S.NB2009_PLACES), p=place_mix(groups)))]}"


def bin_of(t):
    h = (t / H) % 24
    h = h + 24 if h < 5 else h
    return next(i for i, (a, b) in enumerate(S.BINS) if a <= h < b)


def sample_in_bins(w, n=None):
    i = rng.choice(len(w), size=n, p=w)
    lo = np.array([a for a, _ in S.BINS])[i]; hi = np.array([b for _, b in S.BINS])[i]
    return (lo + rng.random(np.shape(i)) * (hi - lo)) * H


def calibrate(purpose, stay_mean, stay_sd):
    """Outbound start-time weights per W7 bin such that outbound + return trips match W7.

    Return trips start after travel (MiD mean) and the stay; the map from outbound weights to
    observed shares is linear, so the weights are a non-negative least-squares fit.
    """
    y = np.array(S.W7[purpose], float); y /= y.sum()
    k = len(S.BINS); M = np.zeros((k, k)); m = 4000
    for j, (a, b) in enumerate(S.BINS):
        out = (a + rng.random(m) * (b - a)) * H
        stay = np.clip(rng.normal(stay_mean, stay_sd, m), 600, 14 * H)
        back = out + TRAVEL + stay
        M[j, j] += 0.5
        for t in back:
            M[bin_of(t), j] += 0.5 / m
    A = np.vstack([M, 10 * np.ones(k)]); bb = np.append(y, 10)       # weights sum to 1
    w, _ = nnls(A, bb)
    w /= w.sum()
    return w, float(np.abs(M @ w - y).sum() / 2)                     # share of trips in the wrong bin


def fit_times():
    """Per purpose: outbound weights; for work and education also the stay spread (grid search)."""
    fits = {}
    # work: mean stay from ZVE, spread fitted; school and university: ZVE counts lessons only
    # (no breaks, no afternoon care), so the mean presence is fitted too and reported next to ZVE
    for act, mean in STAY_MEAN.items():
        means = [mean] if act == "work" else [m * H for m in np.arange(4, 8.01, 0.25)]
        best = min(((calibrate(ANCHOR_PURPOSE[act], m, sd * H), m, sd) for m in means for sd in (0.5, 1, 1.5, 2, 2.5, 3)),
                   key=lambda r: r[0][1])
        (w, err), m, sd = best
        fits[act] = dict(w=w, mean=m, sd=sd * H, err=err)
    for p in SECONDARY:
        mean = sum(share * stay_mean_kind(k) for k, share in MIX[p].items())
        w, err = calibrate(p, mean, mean / 2)
        fits[p] = dict(w=w, err=err, mean=mean)
    for a, f in fits.items():
        print(f"{a:8s} outbound {np.round(f['w'] * 100).astype(int)}  misfit {f['err']:.3f}  stay {f['mean'] / H:.2f} h" +
              (f" (ZVE {STAY_MEAN[a] / H:.2f}) sd {f['sd'] / H:.1f} h" if "sd" in f else ""))
    return fits


class Places:
    """Destinations by kind, weighted by how likely they are open on the day."""

    def __init__(self, grid):
        P = json.load(open(HERE / "pois.json"))
        self.cat = {}
        for c in set(p[0] for p in P):
            a = np.array([[p[1], p[2], p[3], p[4 + DOW], p[4]] for p in P if p[0] == c])
            # lat, lon, weight on the day, weight on a weekday (workplaces are chosen on that), open on the day
            self.cat[c] = (a[:, 0], a[:, 1], a[:, 2] * a[:, 3], a[:, 2] * a[:, 4], a[:, 3])
        # visits: homes, i.e. population grid cells
        pop = np.frombuffer(base64.b64decode(grid["pop"]), np.uint16).astype(float)
        cells = np.nonzero(pop)[0]
        self.cell = (grid["dlat"], grid["dlon"])
        lat = grid["box"][0] + (cells // grid["cols"] + 0.5) * grid["dlat"]
        lon = grid["box"][1] + (cells % grid["cols"] + 0.5) * grid["dlon"]
        self.cat["visit"] = (lat, lon, pop[cells], pop[cells], np.ones(len(cells)))
        self.scale = {}

    def weights(self, kind, lat, lon, scale, weekday=False):
        base, _, place = kind.partition(":")
        plat, plon, w, w_wd, _ = self.cat[place or base]
        d = np.hypot((plon - lon) * KX, (plat - lat) * KY)
        if kind == "daytrip":
            mu, sd = DAYTRIP_KM
            return (w_wd if weekday else w) * np.exp(-0.5 * ((d / 1000 - mu) / sd) ** 2), d
        decay = BASE_DECAY[place or base] * (DOG_REACH if base == "dog" else 1)
        return (w_wd if weekday else w) * np.exp(-d / (decay * scale)), d

    def choose(self, kind, lat, lon, r, scale, weekday=False):
        """Index and coordinates of one destination."""
        p, _ = self.weights(kind, lat, lon, scale, weekday)
        i = r.choice(len(p), p=p / p.sum())
        base, _, place = kind.partition(":")
        plat, plon = self.cat[place or base][:2]
        la, lo = float(plat[i]), float(plon[i])
        if kind == "visit":                                   # a home somewhere in that cell
            la += (r.random() - 0.5) * self.cell[0]; lo += (r.random() - 0.5) * self.cell[1]
        return i, la, lo

    def fit(self, purpose, kinds, homes, r):
        """Decay factor whose straight-line x detour trip lengths best match MiD W12 (up to 50 km)."""
        y = np.array(S.W12[purpose], float); y /= y.sum()
        edges = np.array(S.W12_BANDS) * 1000
        best = None
        for scale in (0.25, 0.35, 0.5, 0.7, 1, 1.4, 2, 2.8, 4):
            dist = []
            for (lat, lon), k in zip(homes, kinds):
                p, d = self.weights(k, lat, lon, scale, weekday=k in ANCHOR_PURPOSE)
                dist.append(d[r.choice(len(p), p=p / p.sum())] * DETOUR)
            h = np.histogram(dist, bins=edges)[0].astype(float); h /= h.sum()
            err = np.abs(h - y).sum() / 2
            if best is None or err < best[1]:
                best = (scale, err, h)
        self.scale[purpose] = best[0]
        print(f"{purpose:8s} decay x{best[0]:<5} misfit {best[1]:.3f}  model {np.round(best[2] * 100).astype(int)}  MiD {np.round(y * 100).astype(int)}")


def plan(kind, fits):
    """List of tours [[(kind, stay s, purpose), ...], start s] for one person, or [] if they stay home.

    Per-type MiD values are for all days; each is scaled by the ratio of the day's row to the
    all-days row (share leaving home, trip-count distribution, purpose shares).
    """
    T = S.TYPES[kind]
    if rng.random() * 100 >= T["mobile"] * D["mobile"] / ALL["mobile"]:
        return []
    p = np.array(T["trips"], float) * np.array(D["dist"]) / np.array(ALL["dist"]); p /= p.sum()
    n = int(rng.choice([1, 2, 3, 4, 5], p=p))
    if n == 5:
        n = int(rng.choice([5, 6]))
    # chance that one of this day's trip pairs belongs to the anchor activity: W1 share x trips / 2,
    # scaled by the day's share of that purpose and trips per person
    col = 0 if T["anchor"] == "work" else 2
    fday = D["purpose"][col] * D["trips"] / (ALL["purpose"][col] * ALL["trips"])
    anchored = T["anchor"] and rng.random() < min(1, T["purpose"][col] / 100 * T["mean"] / 2 * fday)
    sec = np.array(T["purpose"][3:7], float) * np.array(D["purpose"][3:7]) / np.array(ALL["purpose"][3:7])
    sec /= sec.sum()

    def draw():
        purpose = SECONDARY[int(rng.choice(4, p=sec))]
        ks = list(MIX[purpose])
        k = ks[int(rng.choice(len(ks), p=list(MIX[purpose].values())))]
        return (with_place(k, S.AGE_GROUP[kind], rng), None, purpose)

    tours = []
    if anchored:
        n = max(n, 2)
        act = T["anchor"]; f = fits[act]
        stay = float(np.clip(rng.normal(f["mean"], f["sd"]), 1800, 13 * H))
        chain = [(act, stay, ANCHOR_PURPOSE[act])]
        rest = n - 2
        if rest % 2:
            chain.append(draw()); rest -= 1                       # a stop on the way home
        tours.append([chain, float(sample_in_bins(f["w"]))])
    else:
        rest = max(n, 2)
        if rest % 2:
            tours.append([[draw(), draw()], None]); rest -= 3
    while rest >= 2:
        tours.append([[draw()], None]); rest -= 2

    # shop, errand, doctor and town-centre stops share the ZVE shopping minutes of the day; others have their own
    k = sum(1 for t in tours for a, _, _ in t[0] if a in SHOP_KINDS)
    for t in tours:
        t[0] = [(a, s if s is not None else
                 rng.gamma(2, (S.ZVE_SHOP_MIN * 60 / k if a in SHOP_KINDS else stay_mean_kind(a)) / 2), p)
                for a, s, p in t[0]]

    # secondary tours start at times drawn for their purpose, in the gaps of the day
    busy = []
    for t in tours:
        if t[1] is not None:
            busy.append((t[1], t[1] + sum(s for _, s, _ in t[0]) + TRAVEL * (len(t[0]) + 1)))
    for t in tours:
        if t[1] is not None:
            continue
        need = sum(s for _, s, _ in t[0]) + TRAVEL * (len(t[0]) + 1)
        for _ in range(60):
            s0 = float(sample_in_bins(fits[t[0][0][2]]["w"]))
            if all(s0 + need <= a or s0 >= b for a, b in busy):
                break
        else:
            s0 = max(b for _, b in busy) + 1800
        t[1] = s0; busy.append((s0, s0 + need))
    return sorted(tours, key=lambda t: t[1])


def main():
    G = json.load(open(DATA / "grid.json"))
    S0, W0 = G["box"][0], G["box"][1]
    cols, dlat, dlon = G["cols"], G["dlat"], G["dlon"]
    pop = np.frombuffer(base64.b64decode(G["pop"]), np.uint16).astype(float)
    fits = fit_times()
    places = Places(G)

    people = np.random.default_rng(1)                       # same residents on every day
    cell = people.choice(len(pop), N, p=pop / pop.sum())
    hlat = S0 + (cell // cols + people.random(N)) * dlat
    hlon = W0 + (cell % cols + people.random(N)) * dlon
    share = np.array([S.TYPES[k]["n"] for k in TYPES], float)
    kind = people.choice(len(TYPES), N, p=share / share.sum())

    # how far: one decay factor per purpose, fitted to MiD W12 from 1,500 sample homes
    fr = np.random.default_rng(5)
    homes = list(zip(hlat[:1500], hlon[:1500]))
    places.fit("work", ["work"] * len(homes), homes, fr)
    places.fit("edu", ["school" if x < 0.83 else "uni" for x in fr.random(len(homes))], homes, fr)
    for p in SECONDARY:
        ks, pr = list(MIX[p]), list(MIX[p].values())
        places.fit(p, [with_place(ks[int(fr.choice(len(ks), p=pr))], ("total",), fr) for _ in homes], homes, fr)

    agents = []
    for i in range(N):
        home = (float(hlat[i]), float(hlon[i]))
        anchor = {}
        legs = []                                   # (kind, lat, lon, stay, earliest departure, purpose)
        for chain, start in plan(TYPES[kind[i]], fits):
            here = home
            for j, (act, stay, purpose) in enumerate(chain):
                if act in ANCHOR_PURPOSE:
                    # the same workplace, school or university on every day, chosen on weekday weights
                    if act not in anchor:
                        anchor[act] = places.choose(act, *home, np.random.default_rng(10_000 + i),
                                                    places.scale[purpose], weekday=True)
                    idx, la, lo = anchor[act]
                    # on a day the own workplace is closed, the person works somewhere that is open
                    if act == "work" and rng.random() >= places.cat["work"][4][idx]:
                        _, la, lo = places.choose("work", *home, rng, places.scale["work"])
                else:
                    _, la, lo = places.choose(act, *here, rng, places.scale[purpose])
                legs.append((act, la, lo, stay, start if j == 0 else None, purpose))
                here = (la, lo)
            legs.append(("home", *home, 0.0, None, None))
        agents.append({"k": int(kind[i]), "home": home, "t": 0, "stops": legs, "pos": home,
                       "states": [[0, 0]], "legs": [], "trips": [], "purposes": [], "dist": []})

    # route one trip of every active agent per round; a trip leaves at the later of the planned
    # start and the end of the previous stay
    for rnd in range(20):
        todo = [a for a in agents if a["stops"]]
        if not todo:
            break
        for a in todo:
            planned = a["stops"][0][4]
            if planned is not None:
                a["t"] = max(a["t"], planned)
        req = [[*a["pos"], a["stops"][0][1], a["stops"][0][2], int(a["t"])] for a in todo]
        json.dump(req, open(HERE / "req.json", "w"))
        subprocess.run(["node", str(HERE / "router.js"), str(DATA), DAY, str(HERE / "req.json"), str(HERE / "res.json")], check=True)
        res = json.load(open(HERE / "res.json"))
        for a, r in zip(todo, res):
            act, lat, lon, stay, _, purpose = a["stops"].pop(0)
            for mode, pts in r["legs"]:
                if pts[-1][2] <= pts[0][2]:
                    continue
                a["states"].append([pts[0][2], len(ACTS) + mode])
                a["legs"].append((mode, pts))
            a["states"].append([r["arr"], ACTS.index(shown(act))])
            a["trips"].append([int(a["t"]), r["arr"], sum(m for m, _ in r["legs"])])
            # MiD purpose of a trip home is the purpose of the trip before it
            a["purposes"].append(a["purposes"][-1] if act == "home" else purpose)
            a["dist"].append(float(np.hypot((lon - a["pos"][1]) * KX, (lat - a["pos"][0]) * KY)) * DETOUR)
            a["pos"], a["t"] = (lat, lon), r["arr"] + stay
        print("round", rnd, len(todo))

    check(agents, fits, places)
    pack(agents)


def check(agents, fits, places):
    """Compare the routed day with the survey: trips, start times and trip lengths by purpose."""
    n = len(agents)
    rows, dist = {}, {}
    edges = np.array(S.W12_BANDS) * 1000
    for p in list(S.W7) + ["all"]:
        h = np.zeros(len(S.BINS)); ds = []
        for a in agents:
            for (t0, _, _), q, d in zip(a["trips"], a["purposes"], a["dist"]):
                if p in ("all", q):
                    h[bin_of(t0)] += 1; ds.append(d)
        mid = np.array(D["start"] if p == "all" else S.W7[p], float)
        rows[p] = {"model": np.round(100 * h / max(1, h.sum())).astype(int).tolist(),
                   "mid": np.round(100 * mid / mid.sum()).astype(int).tolist(), "trips": int(h.sum())}
        if p in S.W12:
            hd = np.histogram(ds, bins=edges)[0].astype(float)
            y = np.array(S.W12[p], float)
            dist[p] = {"model": np.round(100 * hd / max(1, hd.sum())).astype(int).tolist(),
                       "mid": np.round(100 * y / y.sum()).astype(int).tolist(), "decay": places.scale[p]}
    out = {"day": DAY, "label": D["label"], "date": json.load(open(DATA / "stops.json"))["days"][DAY],
           "trips_per_person": {"model": round(sum(len(a["trips"]) for a in agents) / n, 2), "mid": D["trips"]},
           "share_mobile": {"model": round(100 * sum(1 for a in agents if a["trips"]) / n), "mid": D["mobile"]},
           "work_hours": round(D["work_min"] / 60, 2),
           "school_hours": round(fits["school"]["mean"] / H, 2),
           "fit_misfit": {a: round(f["err"], 3) for a, f in fits.items()},
           "kind_mix": {p: {k: round(v * 100) for k, v in MIX[p].items()} for p in ("errand", "leisure")},
           "bins": ["05-08", "08-10", "10-13", "13-16", "16-19", "19-22", "22-05"], "start": rows,
           "dist_bands": ["<0.5", "0.5-1", "1-2", "2-5", "5-10", "10-20", "20-50"], "distance": dist}
    json.dump(out, open(DATA / f"calibration_{DAY}.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k not in ("start", "distance")}))
    for p, r in rows.items():
        print(f"{p:8s} start model {r['model']}  MiD {r['mid']}  ({r['trips']} trips)")
    for p, r in dist.items():
        print(f"{p:8s} length model {r['model']}  MiD {r['mid']}")


def pack(agents):
    """Integer, delta-coded arrays: lat/lon in 1e-5 deg, time in s."""
    out = []
    for a in agents:
        hl, ho = round(a["home"][0] * 1e5), round(a["home"][1] * 1e5)
        legs, pl, po, pt = [], hl, ho, 0
        for mode, pts in a["legs"]:
            flat = [mode]
            for lat, lon, t in pts:
                la, lo = round(lat * 1e5), round(lon * 1e5)
                flat += [la - pl, lo - po, t - pt]; pl, po, pt = la, lo, t
            legs.append(flat)
        st = [x for s in a["states"] for x in s]
        out.append([a["k"], hl, ho, legs, st, [x for tr in a["trips"] for x in tr]])
    meta = {"types": TYPES, "acts": ACTS + ["walk", "ride"], "n": len(out),
            "calibration": json.load(open(DATA / f"calibration_{DAY}.json"))}
    f = DATA / f"day_{DAY}.json"
    json.dump({"meta": meta, "agents": out}, open(f, "w"), separators=(",", ":"))
    print(f.name, round(f.stat().st_size / 1e6, 1), "MB")


if __name__ == "__main__":
    main()
