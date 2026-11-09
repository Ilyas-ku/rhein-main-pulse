// Earliest-arrival public transport router over one day of the Rhein-Main timetable (data/).
// Same model as that map: walk 4.8 km/h on a 1.3x detour, access/egress <= 1 km,
// transfers <= 400 m between stops plus 2 min, timetable (no delay buffers).
// Usage: node router.js DATA_DIR DAY requests.json out.json   (DAY: weekday, saturday, sunday)
// requests: [[lat0, lon0, lat1, lon1, depart_s], ...]
// out: per request {arr, legs: [[mode, [[lat, lon, t], ...]], ...]}, mode 0 walk, 1 ride
const fs = require("fs");
const [dataDir, day, reqFile, outFile] = process.argv.slice(2);

const WALK = 80 / 60 / 1.3, ACCESS_M = 1000, TRANSFER_M = 400, BUFFER_S = 120, HORIZON_S = 3 * 3600;
const LAT0 = 50.1, KX = Math.cos(LAT0 * Math.PI / 180) * 111320, KY = 110574;
const b64 = (s, T) => { const b = Buffer.from(s, "base64"); return new T(b.buffer.slice(b.byteOffset, b.byteOffset + b.length)); };

const M = JSON.parse(fs.readFileSync(`${dataDir}/stops.json`));
const sLat = b64(M.lat, Float32Array), sLon = b64(M.lon, Float32Array), NS = sLat.length;
const sx = new Float64Array(NS), sy = new Float64Array(NS);
for (let i = 0; i < NS; i++) { sx[i] = sLon[i] * KX; sy[i] = sLat[i] * KY; }

// whole day as one array of connections sorted by departure (chunks are already sorted by hour)
const parts = fs.readdirSync(`${dataDir}/c/${day}`).sort().map(f => JSON.parse(fs.readFileSync(`${dataDir}/c/${day}/${f}`)));
const NC = parts.reduce((n, p) => n + Buffer.from(p.u, "base64").length / 2, 0);
const U = new Uint16Array(NC), V = new Uint16Array(NC), DEP = new Int32Array(NC), ARR = new Int32Array(NC), TRIP = new Uint16Array(NC);
let o = 0;
for (const p of parts) {
  const u = b64(p.u, Uint16Array), v = b64(p.v, Uint16Array), d = b64(p.dep, Uint16Array), a = b64(p.arr, Uint16Array), t = b64(p.trip, Uint16Array);
  for (let k = 0; k < u.length; k++, o++) { U[o] = u[k]; V[o] = v[k]; DEP[o] = d[k] * 10; ARR[o] = a[k] * 10; TRIP[o] = t[k]; }
}
const firstAt = s => { let lo = 0, hi = NC; while (lo < hi) { const m = (lo + hi) >> 1; if (DEP[m] < s) lo = m + 1; else hi = m; } return lo; };

// next connection of the same trip, to walk a ride leg stop by stop
const NT = TRIP.reduce((m, t) => Math.max(m, t), 0) + 1;
const nextOfTrip = new Int32Array(NC).fill(-1), lastOfTrip = new Int32Array(NT).fill(-1);
for (let k = 0; k < NC; k++) { const t = TRIP[k]; if (lastOfTrip[t] >= 0) nextOfTrip[lastOfTrip[t]] = k; lastOfTrip[t] = k; }

// footpaths between stops
const bucket = new Map(), key = (x, y) => x + "," + y;
for (let i = 0; i < NS; i++) { const k = key(Math.floor(sx[i] / TRANSFER_M), Math.floor(sy[i] / TRANSFER_M)); (bucket.get(k) || bucket.set(k, []).get(k)).push(i); }
const fp = Array.from({ length: NS }, () => []);
for (let i = 0; i < NS; i++) {
  const bx = Math.floor(sx[i] / TRANSFER_M), by = Math.floor(sy[i] / TRANSFER_M);
  for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++)
    for (const j of bucket.get(key(bx + a, by + b)) || []) {
      const d = Math.hypot(sx[i] - sx[j], sy[i] - sy[j]);
      if (d <= TRANSFER_M) fp[i].push(j, d / WALK);
    }
}
const near = (x, y) => {
  const out = [], bx = Math.floor(x / TRANSFER_M), by = Math.floor(y / TRANSFER_M), r = Math.ceil(ACCESS_M / TRANSFER_M);
  for (let a = -r; a <= r; a++) for (let b = -r; b <= r; b++)
    for (const j of bucket.get(key(bx + a, by + b)) || []) {
      const d = Math.hypot(x - sx[j], y - sy[j]);
      if (d <= ACCESS_M) out.push(j, d / WALK);
    }
  return out;
};

const ready = new Float64Array(NS), readyFrom = new Int32Array(NS), alight = new Float64Array(NS), alightIn = new Int32Array(NS), alightOut = new Int32Array(NS);
const tripEnter = new Int32Array(NT), egressW = new Float64Array(NS);
const touched = [];
function route(lat0, lon0, lat1, lon1, T) {
  ready.fill(Infinity); alight.fill(Infinity); tripEnter.fill(-1); egressW.fill(Infinity);
  const ox = lon0 * KX, oy = lat0 * KY, dx = lon1 * KX, dy = lat1 * KY;
  const acc = near(ox, oy), egr = near(dx, dy);
  for (let j = 0; j < acc.length; j += 2) { ready[acc[j]] = T + acc[j + 1]; readyFrom[acc[j]] = -1; }
  for (let j = 0; j < egr.length; j += 2) egressW[egr[j]] = egr[j + 1];
  let best = T + Math.hypot(ox - dx, oy - dy) / WALK, bestStop = -1;
  const end = T + HORIZON_S;
  for (let k = firstAt(T); k < NC; k++) {
    const dep = DEP[k];
    if (dep >= best || dep > end) break;
    const t = TRIP[k], u = U[k];
    if (tripEnter[t] < 0) { if (dep < ready[u]) continue; tripEnter[t] = k; }
    const a = ARR[k], v = V[k];
    if (a >= alight[v]) continue;
    alight[v] = a; alightIn[v] = tripEnter[t]; alightOut[v] = k;
    if (a + egressW[v] < best) { best = a + egressW[v]; bestStop = v; }
    const f = fp[v];
    for (let j = 0; j < f.length; j += 2) {
      const r = a + f[j + 1] + BUFFER_S;
      if (r < ready[f[j]]) { ready[f[j]] = r; readyFrom[f[j]] = v; }
    }
  }
  const P = (s, t) => [+sLat[s].toFixed(5), +sLon[s].toFixed(5), Math.round(t)];
  if (bestStop < 0) return { arr: Math.round(best), legs: [[0, [[lat0, lon0, T], [lat1, lon1, Math.round(best)]]]] };
  // walk back from the egress stop: ride, transfer walk, ride, ..., access walk
  const legs = [[0, [P(bestStop, alight[bestStop]), [lat1, lon1, Math.round(best)]]]];
  let s = bestStop;
  for (;;) {
    const kin = alightIn[s], kout = alightOut[s], pts = [];
    for (let k = kin; ; k = nextOfTrip[k]) { pts.push(P(U[k], DEP[k])); if (k === kout) { pts.push(P(V[k], ARR[k])); break; } }
    legs.unshift([1, pts]);
    const b = U[kin], from = readyFrom[b], leave = DEP[kin];
    if (from < 0) { legs.unshift([0, [[lat0, lon0, T], P(b, ready[b])]]); break; }
    if (from !== b) legs.unshift([0, [P(from, alight[from]), P(b, ready[b] - BUFFER_S)]]);
    s = from;
  }
  return { arr: Math.round(best), legs };
}

const req = JSON.parse(fs.readFileSync(reqFile));
const t0 = Date.now();
const out = req.map(r => route(...r));
fs.writeFileSync(outFile, JSON.stringify(out));
console.error(`${NS} stops, ${NC} connections, ${req.length} routes in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
