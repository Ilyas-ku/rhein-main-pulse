"""Video and still maps of the synthetic day, in the look of the web page.

Usage:
  render.py video DAY OUT.mp4 [FROM_H TO_H SECONDS]     a 1080 x 1350 video of one day
  render.py still DAY HH:MM OUT.png                     one moment, 1080 x 1350
  render.py compare HH:MM OUT.png                       the same moment on Tuesday, Saturday, Sunday
Fonts: Barlow Condensed SemiBold, IBM Plex Sans and IBM Plex Mono TTFs (Google Fonts) in FONT_DIR (default fonts/).
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import LineCollection
import numpy as np

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"
FONT_DIR = Path(os.environ.get("FONT_DIR", HERE / "fonts"))
for f in FONT_DIR.glob("*.ttf"):
    font_manager.fontManager.addfont(str(f))
DISPLAY, BODY, MONO = "Barlow Condensed SemiBold", "IBM Plex Sans", "IBM Plex Mono"

GROUND, INK, MUTED, LINE = "#0a0e16", "#e6e9f0", "#8b94a8", "#232b3b"
COLORS = {"home": "#4a5570", "work": "#f59e0b", "school": "#c5e478", "shop": "#ff6fa3", "errand": "#f08a5d",
          "visit": "#a98bff", "nature": "#2f9e44", "centre": "#e599f7", "sport": "#ff4d4d", "eat": "#fde68a",
          "culture": "#9c36b5", "church": "#b5838d", "walk": "#f4f1e8", "ride": "#39d0e8"}
LABEL = {"home": "Home", "work": "Work", "school": "School", "shop": "Shopping", "errand": "Errands",
         "visit": "Visiting", "nature": "Nature, parks", "centre": "Town stroll", "sport": "Sport",
         "eat": "Eating out", "culture": "Culture", "church": "Church", "walk": "On foot", "ride": "On transit"}
GROUP = {"park": "nature", "water": "nature"}
ORDER = ["work", "school", "shop", "errand", "visit", "nature", "centre", "sport", "eat", "culture", "church",
         "walk", "ride"]
DAY_LABEL = {"weekday": "Tuesday 17 Nov 2026", "saturday": "Saturday 21 Nov 2026", "sunday": "Sunday 22 Nov 2026"}
TOWNS = [("Frankfurt", 50.1109, 8.6821, 15), ("Offenbach", 50.0956, 8.7761, 12), ("Wiesbaden", 50.0782, 8.2398, 13),
         ("Mainz", 49.9929, 8.2473, 13), ("Darmstadt", 49.8728, 8.6512, 13), ("Hanau", 50.1264, 8.9283, 12),
         ("Bad Homburg", 50.2268, 8.6182, 12), ("Eschborn", 50.1436, 8.5703, 11), ("Rüsselsheim", 49.9947, 8.4122, 11),
         ("Airport", 50.0379, 8.5622, 11), ("Bad Vilbel", 50.1786, 8.7364, 11), ("Langen", 49.9894, 8.6686, 11)]
BIG = 1_000_000                                               # > any time of day in seconds


class Day:
    """One day of agents in flat arrays; vectorised position and state at any time."""

    def __init__(self, day):
        J = json.load(open(DATA / f"day_{day}.json"))
        self.day, A = day, J["agents"]
        acts = J["meta"]["acts"]
        self.group = [GROUP.get(a, a) for a in acts]
        self.N = len(A)
        x, y, t, aid, st, ss, sid = [], [], [], [], [], [], []
        self.home = np.zeros((self.N, 2))
        for i, a in enumerate(A):
            k, hl, ho, L, states, _ = a
            self.home[i] = (ho / 1e5, hl / 1e5)
            la, lo, tt = hl, ho, 0
            for l in L:
                for j in range(1, len(l), 3):
                    la += l[j]; lo += l[j + 1]; tt += l[j + 2]
                    x.append(lo / 1e5); y.append(la / 1e5); t.append(tt); aid.append(i)
            for j in range(0, len(states), 2):
                st.append(states[j]); ss.append(states[j + 1]); sid.append(i)
        self.x, self.y, self.t = np.array(x), np.array(y), np.array(t, float)
        self.pkey = np.array(aid) * BIG + self.t
        self.poff = np.searchsorted(np.array(aid), np.arange(self.N + 1))
        self.skey = np.array(sid) * BIG + np.array(st, float)
        self.ss = np.array(ss)
        self.soff = np.searchsorted(np.array(sid), np.arange(self.N + 1))
        gi = {g: n for n, g in enumerate(dict.fromkeys(self.group))}
        self.gnames = list(gi)
        self.sgroup = np.array([gi[g] for g in self.group])[self.ss]

    def at(self, t):
        """Positions (N x 2) and display group index per agent at time t."""
        ids = np.arange(self.N)
        j = np.searchsorted(self.skey, ids * BIG + t, side="right") - 1
        j = np.maximum(j, self.soff[:-1])
        g = self.sgroup[j]
        pos = self.home.copy()
        has = self.poff[1:] > self.poff[:-1]
        k = np.searchsorted(self.pkey, ids * BIG + t, side="right") - 1
        last = len(self.t) - 1                                # agents without trips have lo > hi
        lo = np.minimum(self.poff[:-1], last); hi = np.maximum(self.poff[1:] - 1, lo)
        k = np.clip(k, lo, hi)
        k2 = np.minimum(k + 1, hi)
        t1, t2 = self.t[k], self.t[k2]
        before = t < self.t[lo]
        f = np.where((t2 > t1) & ~before, np.clip((t - t1) / np.where(t2 > t1, t2 - t1, 1), 0, 1), 0)
        px = np.where(before, self.x[lo], self.x[k] + f * (self.x[k2] - self.x[k]))
        py = np.where(before, self.y[lo], self.y[k] + f * (self.y[k2] - self.y[k]))
        pos[has, 0], pos[has, 1] = px[has], py[has]
        return pos, g

    def shares(self, step=300, t0=4 * 3600, t1=28 * 3600):
        """Share of agents per display group over the day (for the stacked chart)."""
        ts = np.arange(t0, t1 + 1, step)
        out = np.zeros((len(ts), len(self.gnames)))
        for n, t in enumerate(ts):
            _, g = self.at(t)
            out[n] = np.bincount(g, minlength=len(self.gnames)) / self.N
        return ts, out


def basemap(ax):
    G = json.load(open(DATA / "grid.json"))
    pop = np.frombuffer(base64.b64decode(G["pop"]), np.uint16).astype(float).reshape(G["rows"], G["cols"])
    S, W = G["box"][0], G["box"][1]
    E, N = W + G["cols"] * G["dlon"], S + G["rows"] * G["dlat"]
    img = np.zeros((G["rows"], G["cols"], 4))
    img[..., 0], img[..., 1], img[..., 2] = 120 / 255, 110 / 255, 170 / 255
    img[..., 3] = np.sqrt(pop / pop.max()) * 0.47
    ax.imshow(img, origin="lower", extent=[W, E, S, N], interpolation="bilinear", zorder=1)
    seg = json.load(open(DATA / "network.json"))["seg"]
    s = np.array(seg, float).reshape(-1, 5)
    lines = [[(a, b), (c, d)] for a, b, c, d, _ in s]
    alpha = np.minimum(150, 25 + s[:, 4] / 3) / 255
    cols = np.column_stack([np.full(len(s), 96 / 255), np.full(len(s), 112 / 255), np.full(len(s), 140 / 255), alpha])
    ax.add_collection(LineCollection(lines, colors=cols, linewidths=np.where(s[:, 4] > 300, 0.9, 0.5), zorder=2))
    for name, lat, lon, size in TOWNS:
        ax.text(lon, lat + 0.009, name.upper(), color=(200 / 255, 206 / 255, 220 / 255, 0.6), fontsize=size * 0.62,
                family=DISPLAY, weight=600, ha="center", va="bottom", zorder=3)
    ax.set_xlim(8.2, 9.0); ax.set_ylim(49.85, 50.35)
    ax.set_aspect(1 / np.cos(np.radians(50.1)))
    ax.axis("off")


def rgba(hexes, alpha):
    c = np.array([matplotlib.colors.to_rgb(h) for h in hexes])
    return np.column_stack([c, np.full(len(c), alpha)])


class Frame:
    """Map plus dots and trails of one day; draw(t) updates them."""

    def __init__(self, ax, D, size=3.0, trail_min=12):
        self.ax, self.D, self.size = ax, D, size
        basemap(ax)
        self.palette = rgba([COLORS[g] for g in D.gnames], 1)
        self.home_i = D.gnames.index("home")
        self.moving = [D.gnames.index(g) for g in ("walk", "ride") if g in D.gnames]
        self.trail_steps = np.linspace(0, trail_min * 60, 7)
        self.trails = [LineCollection([], linewidths=1.1, zorder=4) for _ in range(len(self.trail_steps) - 1)]
        for lc in self.trails:
            ax.add_collection(lc)
        self.home_dots = ax.scatter([], [], s=size * 0.35, c=[COLORS["home"]], alpha=0.35, linewidths=0, zorder=5)
        self.dots = ax.scatter([], [], s=size, linewidths=0, zorder=6)

    def draw(self, t):
        D = self.D
        pos, g = D.at(t)
        home = g == self.home_i
        self.home_dots.set_offsets(pos[home])
        self.dots.set_offsets(pos[~home]); self.dots.set_color(self.palette[g[~home]])
        # trails: where each travelling agent was over the last minutes, fading with age
        mov = np.isin(g, self.moving)
        prev = pos[mov]
        ride = g[mov] == D.gnames.index("ride")
        for n, lc in enumerate(self.trails):
            p, _ = D.at(t - self.trail_steps[n + 1])
            p = p[mov]
            segs = np.stack([prev, p], axis=1)
            fade = 0.55 * (1 - n / len(self.trails))
            cols = np.where(ride[:, None], [57 / 255, 208 / 255, 232 / 255, fade], [244 / 255, 241 / 255, 232 / 255, fade * 0.7])
            lc.set_segments(segs); lc.set_color(cols)
            prev = p
        return pos, g


def hhmm(t):
    m = int(t // 60) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def poster(day, size=(10.8, 13.5)):
    """Figure with header, map and footer (legend + day chart); returns figure and updater."""
    D = Day(day)
    fig = plt.figure(figsize=size, dpi=100, facecolor=GROUND)
    ax = fig.add_axes([0.0, 0.17, 1.0, 0.70], facecolor=GROUND)
    F = Frame(ax, D)
    fig.text(0.045, 0.955, "RHEIN-MAIN PULSE", color=INK, fontsize=34, family=DISPLAY, weight=600, va="center")
    fig.text(0.045, 0.918, "20,000 synthetic residents  ·  every trip routed on the real transit timetable",
             color=MUTED, fontsize=12.5, family=BODY, va="center")
    fig.text(0.045, 0.888, DAY_LABEL[day].upper(), color="#39d0e8", fontsize=15, family=DISPLAY, weight=600, va="center")
    clock = fig.text(0.955, 0.93, "", color=INK, fontsize=64, family=DISPLAY, weight=600, ha="right", va="center")
    # day chart
    cax = fig.add_axes([0.045, 0.085, 0.91, 0.07], facecolor="#2b3245")
    ts, sh = D.shares()
    stack = ["ride", "walk", "church", "culture", "eat", "sport", "centre", "nature", "visit", "errand", "shop",
             "school", "work", "home"]
    base = np.zeros(len(ts))
    for g in stack:
        if g in D.gnames:
            v = sh[:, D.gnames.index(g)]
            cax.fill_between(ts, base, base + v, color=COLORS[g], alpha=0.55 if g == "home" else 0.9, linewidth=0)
            base += v
    cax.set_xlim(ts[0], ts[-1]); cax.set_ylim(0, 1); cax.axis("off")
    cursor = cax.axvline(ts[0], color=INK, lw=1.2)
    for h in range(4, 29, 4):
        fig.text(0.045 + 0.91 * (h - 4) / 24, 0.072, f"{h % 24:02d}", color=MUTED, fontsize=10, family=MONO,
                 ha="center", va="top")
    # legend
    lx, ly = 0.045, 0.035
    for n, g in enumerate(ORDER):
        x = lx + (n % 7) * 0.132; y = ly - (n // 7) * 0.022
        fig.patches.append(matplotlib.patches.Circle((x + 0.005, y), 0.0045, transform=fig.transFigure,
                                                     color=COLORS[g], figure=fig))
        fig.text(x + 0.015, y, LABEL[g], color=INK, fontsize=10.5, family=BODY, va="center")

    def update(t):
        F.draw(t)
        clock.set_text(hhmm(t) + ("⁺¹" if t >= 86400 else ""))
        cursor.set_xdata([t, t])
    return fig, update


def video(day, out, h0=4.5, h1=25.5, seconds=45, fps=30):
    fig, update = poster(day)
    W, H = fig.canvas.get_width_height()
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
                           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
                           "-preset", "slow", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    n = int(seconds * fps)
    for i in range(n):
        update((h0 + (h1 - h0) * i / (n - 1)) * 3600)
        fig.canvas.draw()
        ff.stdin.write(np.asarray(fig.canvas.buffer_rgba()).tobytes())
        if i % 150 == 0:
            print(f"frame {i}/{n}", flush=True)
    ff.stdin.close(); ff.wait()


def still(day, when, out):
    fig, update = poster(day)
    h, m = map(int, when.split(":"))
    update(h * 3600 + m * 60)
    fig.savefig(out, dpi=200, facecolor=GROUND)


def compare(when, out):
    h, m = map(int, when.split(":"))
    t = h * 3600 + m * 60
    fig = plt.figure(figsize=(19.2, 10.05), dpi=100, facecolor=GROUND)
    fig.text(0.03, 0.935, "RHEIN-MAIN PULSE", color=INK, fontsize=34, family=DISPLAY, weight=600, va="center")
    fig.text(0.03, 0.885, f"Where 20,000 synthetic residents are at {when}: the same people, three different days",
             color=MUTED, fontsize=15, family=BODY, va="center")
    for n, day in enumerate(["weekday", "saturday", "sunday"]):
        D = Day(day)
        ax = fig.add_axes([0.02 + n * 0.325, 0.12, 0.31, 0.70], facecolor=GROUND)
        F = Frame(ax, D, size=2.4)
        pos, g = F.draw(t)
        cnt = np.bincount(g, minlength=len(D.gnames))
        share = lambda *gs: 100 * sum(cnt[D.gnames.index(x)] for x in gs if x in D.gnames) / D.N
        title = {"weekday": "TUESDAY", "saturday": "SATURDAY", "sunday": "SUNDAY"}[day]
        x0 = 0.02 + n * 0.325 + 0.155
        fig.text(x0, 0.785, title, color="#39d0e8", fontsize=24, family=DISPLAY, weight=600, ha="center")
        stats = [("at home", share("home")), ("at work", share("work")), ("shopping", share("shop", "centre")),
                 ("nature, parks", share("nature")), ("on the move", share("walk", "ride"))]
        for k, (lab, v) in enumerate(stats):
            xx = x0 - 0.13 + k * 0.065
            fig.text(xx, 0.085, f"{v:.0f}%", color=INK, fontsize=20, family=DISPLAY, weight=600, ha="center")
            fig.text(xx, 0.058, lab, color=MUTED, fontsize=10, family=BODY, ha="center")
    lx = 0.03
    for k, g in enumerate(ORDER):
        x = lx + k * 0.072
        fig.patches.append(matplotlib.patches.Circle((x, 0.022), 0.0035, transform=fig.transFigure, color=COLORS[g], figure=fig))
        fig.text(x + 0.007, 0.022, LABEL[g], color=INK, fontsize=10, family=BODY, va="center")
    fig.text(0.97, 0.935, "Model, not measurement · MiD 2023, ZVE 2022, Zensus 2022, OSM, DELFI GTFS",
             color=MUTED, fontsize=10, family=BODY, ha="right", va="center")
    fig.savefig(out, dpi=125, facecolor=GROUND)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "video":
        args = [float(a) for a in sys.argv[4:7]]
        video(sys.argv[2], sys.argv[3], *args)
    elif cmd == "still":
        still(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "compare":
        compare(sys.argv[2], sys.argv[3])
