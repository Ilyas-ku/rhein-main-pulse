"""One synthetic day (Tuesday, Saturday or Sunday) for N residents of the Rhein-Main box -> data/day_DAY.json.

Homes:        Zensus 2022 population on the ~200 m grid of rhein-main-transit-reach.
People:       MiD 2023 person types, who leaves home, how many trips and for what (survey.py).
Times:        outbound start times fitted so that trip start times by purpose match MiD W7,
              stays from ZVE 2022 (calibrate()).
Destinations: OSM POIs (pois.json), gravity choice  P ~ weight * exp(-d / L).
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
rng = np.random.default_rng(["weekday", "saturday", "sunday"].index(DAY) + 7)
LAT0 = 50.1
KX, KY = np.cos(np.radians(LAT0)) * 111320, 110574
H = 3600
TRAVEL = S.W9_MEAN_MIN * 60

TYPES = list(S.TYPES)
DECAY = {"work": 9000, "school": 1500, "uni": 15000, "shop": 1200, "errand": 1500, "escort": 1000, "leisure": 3000}
ACTS = ["home", "work", "school", "uni", "shop", "errand", "escort", "leisure"]   # then walk, ride
PURPOSE = {"work": "work", "school": "edu", "uni": "edu", "shop": "shop", "errand": "errand",
           "escort": "escort", "leisure": "leisure"}
SECONDARY = ["shop", "errand", "leisure", "escort"]          # W1 columns 3, 4, 5, 6
STAY_MEAN = {"work": D["work_min"] * 60, "school": S.ZVE_SCHOOL_MIN * 60, "uni": S.ZVE_UNI_MIN * 60}
ESCORT_STAY = 10 * 60                                         # no survey value: drop-off / pick-up


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


def fit_all():
    """Per purpose: outbound weights; for work and education also the stay spread (grid search)."""
    fits = {}
    # work: mean stay from ZVE, spread fitted; school and university: ZVE counts lessons only
    # (no breaks, no afternoon care), so the mean presence is fitted too and reported next to ZVE
    for act, mean in STAY_MEAN.items():
        means = [mean] if act == "work" else [m * H for m in np.arange(4, 8.01, 0.25)]
        best = min(((calibrate(PURPOSE[act], m, sd * H), m, sd) for m in means for sd in (0.5, 1, 1.5, 2, 2.5, 3)),
                   key=lambda r: r[0][1])
        (w, err), m, sd = best
        fits[act] = dict(w=w, mean=m, sd=sd * H, err=err)
    for act in ("shop", "errand", "leisure", "escort"):
        mean = {"shop": S.ZVE_SHOP_MIN, "errand": S.ZVE_SHOP_MIN, "leisure": S.ZVE_LEISURE_MIN}.get(act, 10) * 60
        w, err = calibrate(PURPOSE[act], mean, mean / 2)
        fits[act] = dict(w=w, err=err)
    for a, f in fits.items():
        print(f"{a:8s} outbound {np.round(f['w'] * 100).astype(int)}  misfit {f['err']:.3f}" +
              (f"  stay {f['mean'] / H:.2f} h (ZVE {STAY_MEAN[a] / H:.2f}) sd {f['sd'] / H:.1f} h" if "sd" in f else ""))
    return fits


def plan(kind, fits):
    """List of tours [[(activity, stay s), ...], start s] for one person, or [] if they stay home.

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
    draw = lambda: SECONDARY[int(rng.choice(4, p=sec))]

    tours = []
    if anchored:
        n = max(n, 2)
        act = T["anchor"]; f = fits[act]
        stay = float(np.clip(rng.normal(f["mean"], f["sd"]), 1800, 13 * H))
        chain = [(act, stay)]
        rest = n - 2
        if rest % 2:
            chain.append((draw(), None)); rest -= 1                  # a stop on the way home
        tours.append([chain, float(sample_in_bins(f["w"]))])
    else:
        rest = max(n, 2)
        if rest % 2:
            tours.append([[(draw(), None), (draw(), None)], None]); rest -= 3
    while rest >= 2:
        tours.append([[(draw(), None)], None]); rest -= 2

    # stays of shop/errand and leisure share the ZVE minutes per day between that day's stops
    for kinds, minutes in ((("shop", "errand"), S.ZVE_SHOP_MIN), (("leisure",), S.ZVE_LEISURE_MIN)):
        k = sum(1 for t in tours for a, _ in t[0] if a in kinds)
        for t in tours:
            t[0] = [(a, s if s is not None or a not in kinds else rng.gamma(2, minutes * 60 / k / 2)) for a, s in t[0]]
    for t in tours:
        t[0] = [(a, ESCORT_STAY if a == "escort" else s) for a, s in t[0]]

    # secondary tours start at times drawn for their first activity, in the gaps of the day
    busy = []
    for t in tours:
        if t[1] is not None:
            busy.append((t[1], t[1] + sum(s for _, s in t[0]) + TRAVEL * (len(t[0]) + 1)))
    for t in tours:
        if t[1] is not None:
            continue
        need = sum(s for _, s in t[0]) + TRAVEL * (len(t[0]) + 1)
        for _ in range(60):
            s0 = float(sample_in_bins(fits[t[0][0][0]]["w"]))
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
    fits = fit_all()

    people = np.random.default_rng(1)                       # same residents on every day
    cell = people.choice(len(pop), N, p=pop / pop.sum())
    hlat = S0 + (cell // cols + people.random(N)) * dlat
    hlon = W0 + (cell % cols + people.random(N)) * dlon
    share = np.array([S.TYPES[k]["n"] for k in TYPES], float)
    kind = people.choice(len(TYPES), N, p=share / share.sum())

    P = json.load(open(HERE / "pois.json"))
    cats = {}
    for c in DECAY:
        a = np.array([[p[1], p[2], p[3]] for p in P if p[0] == c])
        cats[c] = (a[:, 0], a[:, 1], a[:, 2])

    def choose(c, lat, lon, r=None):
        plat, plon, w = cats[c]
        d = np.hypot((plon - lon) * KX, (plat - lat) * KY)
        p = w * np.exp(-d / DECAY[c])
        i = (r or rng).choice(len(p), p=p / p.sum())
        return float(plat[i]), float(plon[i])

    agents = []
    for i in range(N):
        home = (float(hlat[i]), float(hlon[i]))
        anchor = {}
        legs = []                                   # (activity, lat, lon, stay, earliest departure)
        for chain, start in plan(TYPES[kind[i]], fits):
            here = home
            for j, (act, stay) in enumerate(chain):
                if act in ("work", "school", "uni"):
                    # the same workplace, school or university on every day
                    anchor.setdefault(act, choose(act, *home, np.random.default_rng(10_000 + i))); dest = anchor[act]
                else:
                    dest = choose(act, *here)
                legs.append((act, *dest, stay, start if j == 0 else None))
                here = dest
            legs.append(("home", *home, 0.0, None))
        agents.append({"k": int(kind[i]), "home": home, "t": 0, "stops": legs, "pos": home,
                       "states": [[0, 0]], "legs": [], "trips": [], "purposes": []})

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
            act, lat, lon, stay, _ = a["stops"].pop(0)
            for mode, pts in r["legs"]:
                if pts[-1][2] <= pts[0][2]:
                    continue
                a["states"].append([pts[0][2], len(ACTS) + mode])
                a["legs"].append((mode, pts))
            a["states"].append([r["arr"], ACTS.index(act)])
            a["trips"].append([int(a["t"]), r["arr"], sum(m for m, _ in r["legs"])])
            # MiD purpose of a trip home is the purpose of the trip before it
            a["purposes"].append(a["purposes"][-1] if act == "home" else PURPOSE[act])
            a["pos"], a["t"] = (lat, lon), r["arr"] + stay
        print("round", rnd, len(todo))

    check(agents, fits)
    pack(agents)


def check(agents, fits):
    """Compare the routed day with the survey: trips per person and start times by purpose."""
    n = len(agents)
    rows = {}
    for p in list(S.W7) + ["all"]:
        h = np.zeros(len(S.BINS))
        for a in agents:
            for (t0, _, _), q in zip(a["trips"], a["purposes"]):
                if p in ("all", q):
                    h[bin_of(t0)] += 1
        mid = np.array(D["start"] if p == "all" else S.W7[p], float)
        rows[p] = {"model": np.round(100 * h / max(1, h.sum())).astype(int).tolist(),
                   "mid": np.round(100 * mid / mid.sum()).astype(int).tolist(), "trips": int(h.sum())}
    out = {"day": DAY, "label": D["label"], "date": json.load(open(DATA / "stops.json"))["days"][DAY],
           "trips_per_person": {"model": round(sum(len(a["trips"]) for a in agents) / n, 2), "mid": D["trips"]},
           "share_mobile": {"model": round(100 * sum(1 for a in agents if a["trips"]) / n), "mid": D["mobile"]},
           "work_hours": round(D["work_min"] / 60, 2),
           "school_hours": round(fits["school"]["mean"] / H, 2),
           "fit_misfit": {a: round(f["err"], 3) for a, f in fits.items()},
           "bins": ["05-08", "08-10", "10-13", "13-16", "16-19", "19-22", "22-05"], "start": rows}
    json.dump(out, open(DATA / f"calibration_{DAY}.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "start"}))
    for p, r in rows.items():
        print(f"{p:8s} model {r['model']}  MiD {r['mid']}  ({r['trips']} trips)")


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
