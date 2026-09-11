# Stage 4 tasks — demo frontend: sensor summary, dummy soldiers, military theme, plain dashes

> Scope: frontend-only changes under `frontend/` (plus `docs/`), decided in the
> 2026-09-12 requirements interview. Required reading: [../UIUX.md](../UIUX.md)
> (binding spec; §§1, 3, 4, 6, 7, 11, 13 are touched), [../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §3
> (the JSON shapes the synthetic layer must reproduce), `backend/api/jobs/insights.py`
> (`ACTIONS`, `RULES`, `group_actions` — mirrored in TypeScript for dummy soldiers).
> Precondition: stage 3 shipped (it is). Backend is NOT modified by any task here.
>
> This file is the implementation plan of record. It was written before any code
> and is copied into the repo as the first step of S4-T00. Code is ground truth
> once shipped; keep this file's "as built" notes honest.

---

## 0. Locked requirements (from the interview; do not re-open)

| # | Area | Decision |
|---|---|---|
| R1 | Sensor summary | Only the quality meter + sensor dots collapse. Static literal `4 sensors \| 6400Hz logging`. Detail page: `»` toggle swaps it in place for the real readout, flips to `«`. Overview cards: static text only, no expand, no tooltip. Never persisted (every mount collapsed). Entire summary hidden while the device is offline. |
| R2 | Dummy soldiers | Always exactly 5, alongside real devices, even with zero real devices. Frontend-only synthetic, zero HTTP for dummy ids. Ids `demo-1`..`demo-5`. US Army rank + surname. Real devices always first, dummies in fixed scripted order. No marker anywhere. Deterministic scripted stories, "2 h into a session". All six live series present (m4/m5 never blank). Windows `5m,30m,2h`, horizons `10m,30m,1h`. Battery % slowly draining; scripted flag chips; no calibration badge. Rename and Adopt/Override look enabled but do nothing. Hero stats and sidebar include dummies. Login page untouched. |
| R3 | Military theme | athlete(s) -> soldier(s) everywhere user-facing (text, tooltips, aria, HTML title/meta), comments untouched. "Coaching cue" -> "PTI cue"; Adopt/Override unchanged. Backend text untouched. Hero eyebrow, headline, paragraph rewritten (readiness for a commander, load carriage/march, "Built for units like the 1st Cavalry Division"). Navigation + empty states re-themed. HIPPOS / "Motion Intelligence" / login unchanged; meta "trainers" -> "commanders". Metric names and "session" unchanged. |
| R4 | Plain dashes | Scope: frontend on-screen text, tooltips, aria, `index.html`. Em dash -> ` - `. Missing-value placeholder -> `--`. En dash in ranges -> `-`. Ellipsis -> `...`. Middle dot -> ` \| ` (my choice; history null-bucket axis label -> `-`). Curly quotes untouched. Comments, docs prose and backend untouched. |
| R5 | Process | Vitest pure-module unit tests. Plan lives at `docs/tasks/STAGE4.md`; `docs/UIUX.md` updated to match. Branch `feat/demo-frontend`, one commit per phase, nothing pushed. |

---

## 1. Architecture decisions

**D1. Synthetic soldiers are injected at the three seams the UI already reads through, never in components.**
`useMergedDevices()` appends them to the registry; `LiveProvider` serves their ring buffers through the existing `getBuffer()` / `latest`; every `lib/api.ts` helper branches on `isDemoId(dev)`. Every page and component keeps its current props and hooks. This is what makes "indistinguishable" cheap and safe: there is no second rendering path to drift.

**D2. Dummy buffers live in their own ref inside `LiveProvider`, not in `buffers.current`.**
`rebackfillAll()` wipes `buffers.current = {}` on every WebSocket open and tab-return, and the backfill path calls `/api/metrics/recent` for any device it sees ticks for. A separate `demoBuffers` ref is immune to both. `getBuffer` and the 250 ms snapshot read both maps; the WS message handler is untouched.

**D3. The generator is a pure function of wall-clock time; the feed timer only "catches up to now".**
`SESSION_START_MS = Date.now() - 2h05m` is captured once per page load. Every value (live sample, history bucket, window aggregate, forecast, insight row) is computed from session-relative seconds with hashed (index-keyed) noise, never a sequential PRNG stream. Consequences: identical picture on every reload; StrictMode double-mount, hidden-tab throttling and long-open tabs cannot desync anything, because each timer tick regenerates from the buffer's last timestamp up to now.

**D4. Query short-circuits live in `lib/api.ts`, not at call sites and not in a `queryClient` default.**
Every call site passes an explicit `queryFn`, so a default `queryFn` would never run. Branching in the eight helpers is one choke point that also covers `useQueries` in the hero and any future caller. Return types are unchanged, so `retry`, `refetchInterval` and `placeholderData` keep working, which is what makes "Made 23s ago" and bucket ageing tick on the existing poll cadences.

**D5. The insight catalogue and grouping are ported verbatim, then re-worded.**
`ACTIONS`, the rule `message`/`rationale`/`reason` templates and `group_actions()` are copied into TypeScript with athlete -> soldier and em dash -> ` - ` applied (they are frontend strings on dummies; the backend copy stays as is). Timeline reasons and the event log are derived from the same row list, so the `${rule_id}|${created_at}` join in `InsightsPanel` is exact by construction, and evidence keys match `_base_evidence` so `lib/evidence.ts` labels resolve.

**D6. Ordering is decided in one hook.** `useVisibleDevices` sorts by `demoRank` (0 for real ids) before the existing online-first / name order. Sidebar and Overview both consume it, so they cannot disagree.

**D7. Mutations are neutralised at the lowest layer that keeps the controls untouched.** Rename: `RenameInline.commit` returns early for demo ids (so the optimistic cache write and the `['devices']` invalidation never run). Decisions: `postInsightDecision` resolves a fake echo for demo ids; the existing `onSuccess` invalidation just re-runs the generator, which still returns `decision: null`, so the card stays undecided.

**D8. Text changes are mechanical and tabulated (Appendix A).** No sentence is re-written except the hero copy (Appendix B), which is drafted here for review. A vitest test asserts that every exported user-facing string table (`METRICS`, `COMPOSITE`, `FLAG_META`, `RISK_BAND_META`, demo catalogue) contains none of `— – … ·`.

**D9. Tests are unit tests over pure modules; the UI is verified by `tsc -b` and a manual E2E checklist run against the local Docker stack.** The app cannot be exercised without the backend (the auth guard redirects to `/login` and login needs the API), so "backend stopped" is not a supported demo state and is not tested beyond the alert-feed fallback.

**Assumptions (call out if wrong):**
- A1. The demo VPS runs the backend; dummies must coexist with a live API, not replace it.
- A2. Vite dev server + the compose stack (api on 127.0.0.1:8000) is the verification environment; the final production check is `docker compose up -d --build caddy` on this machine, not a deploy.
- A3. `npm ci` works offline-free on this machine (node 24, npm 11 present; `node_modules` currently absent).
- A4. Multiplication sign `×` and minus sign `−` used in evidence text are not "dashes" and stay.
- A5. The `-` axis label for empty history buckets and `--` placeholders are acceptable in tables (they were `·` and `—`).

---

## 2. Module map (new code)

```
frontend/src/lib/demo/
  ids.ts        DEMO_PREFIX, DEMO_COUNT, isDemoId(id), demoRank(id)
  noise.ts      hash32(seed,k) -> [0,1); valueNoise(seed,x,period); fbm(seed,x,period,octaves)
  profiles.ts   SESSION_AGE_S, SESSION_START_MS, DEMO_WINDOWS, DEMO_HORIZONS, DEMO_MODEL_VERSION,
                Keyframe, Episode, DemoProfile, DEMO_PROFILES[5], profileFor(id)
  signal.ts     envelopeAt(keyframes,s); sampleAt(profile,s) -> {m[5],c}; qualityAt(profile,s);
                bucketStats(profile,s0,s1,n) -> {m,sd,composite{avg,min,max,sd},quality,n};
                slopePerMin(profile,s,spanS)
  live.ts       fillDemoBuffer(buf,profile,nowMs); startDemoFeed(buffers,now?) -> stop();
                demoLatestMeta(id,nowMs) -> {q,flags,cal:null}
  insights.ts   DEMO_ACTIONS, thresholds, ruleTemplates, demoInsightRows(profile,nowMs),
                groupActions(rows,max), bucketTimeline(rows,nowMs), demoAdviceTimeline(id,nowMs),
                demoCurrentAdvice(id,nowMs)
  api.ts        demoDevices(nowMs), demoWindows(id,nowMs), demoHistory(id,window,buckets,nowMs),
                demoForecasts(id,nowMs), demoInsights(id|undefined,limit,nowMs),
                demoRecent(id,seconds,nowMs), demoDecision(body)
  *.test.ts     one per module (vitest, node environment)
frontend/src/components/SensorSummary.tsx
frontend/vitest.config.ts
docs/tasks/STAGE4.md  (this file)
```

Import direction: `lib/demo/*` imports only **types** from `../api` and `../ws` and values from `../config`/`../metrics`; `lib/api.ts`, `lib/ws.tsx`, `lib/devices.ts`, `components/bits.tsx` import values from `lib/demo`. No runtime cycle.

---

## 3. Phases

Each phase ends with: `npm run build` green, `npm test` green, the listed manual checks, one commit on `feat/demo-frontend` with the given message (plus the Co-Authored-By trailer).

### Phase 0 — S4-T00 Preflight and tooling

**Steps**
1. `git checkout -b feat/demo-frontend` from `main` (clean tree confirmed).
2. `cd frontend && npm ci` (lockfile present). Baseline `npm run build` must pass before any change; if it does not, stop and report.
3. Add vitest: `npm install -D vitest`. Create `frontend/vitest.config.ts`:
   `defineConfig({ test: { environment: 'node', include: ['src/**/*.test.ts'] } })` from `vitest/config`.
   Add `"test": "vitest run"` to `package.json` scripts. Tests import `describe/it/expect` explicitly (no globals), so `tsc -b` type-checks them via `include: ["src"]` without extra types.
4. Add `frontend/src/lib/format.test.ts` as the smoke test (placeholders are still `—` at this point; assert current behaviour, updated in Phase 1).
5. Copy this plan to `docs/tasks/STAGE4.md`.

**Edge cases**: `npm ci` needs network; `core.autocrlf=true` with `* text=auto` means new files are written LF and normalised by git (no CRLF noise in diffs). `tsconfig.tsbuildinfo` is gitignored.

**Verify**: `npm run build` and `npm test` both exit 0; `git status` shows only `package.json`, `package-lock.json`, `vitest.config.ts`, the test, and the plan.

**Commit**: `frontend: vitest scaffold + stage-4 plan`

---

### Phase 1 — S4-T01 Plain dashes and military wording (R3 + R4, everything except the hero copy)

**Files**: `components/Sidebar.tsx`, `pages/Overview.tsx` (labels only; hero copy is Phase 7), `pages/Device.tsx`, `components/DeviceCard.tsx`, `components/bits.tsx`, `components/CalibrationBadge.tsx`, `components/HumanoidFigure.tsx`, `components/InsightsPanel.tsx`, `components/HistoryBars.tsx`, `components/ForecastChart.tsx`, `pages/Login.tsx`, `lib/format.ts`, `lib/evidence.ts`, `lib/metrics.ts`, `index.html`.

**Steps**
1. Apply Appendix A row by row. Rules: em dash in prose -> ` - `; placeholder `—` -> `--`; en dash range -> `-`; `…` -> `...`; `·` -> ` | ` (HistoryBars null label -> `-`); athlete -> soldier; "Coaching cue" -> "PTI cue"; nav/empty-state labels per the table. Do not touch comments, `format.ts:89` curly quotes, `evidence.ts` `×`/`−` signs, or any backend file.
2. `Overview.tsx`: also change the loading notice condition to `isLoading && visible.length === 0` (prepares for dummies; with none present today it is behaviour-neutral).
3. Update `format.test.ts` to assert `metricValue(null) === '--'` and `pct(null) === '--'`.
4. Add `lib/text.test.ts`: imports `METRICS`, `COMPOSITE`, `FLAG_META`, `RISK_BAND_META` and asserts no string field contains `—`, `–`, `…` or `·`.

**Edge cases**: `metricValue` output feeds ECharts tooltips and table cells; `--` is two ASCII characters, no layout impact. The sidebar risk column uses `metricValue(c)` for offline devices, so it shows `--`. `windowLabel()`/`horizonLabel()` are unchanged. `aria-label` strings count as user-facing (screen readers).

**Verify**: `npm run build`; `npm test`; `rg "—|–|…|·" frontend/src frontend/index.html` returns only comment lines and `format.ts:89`; `rg -i "athlete|coaching cue|trainer" frontend/src frontend/index.html` returns only comment lines and `lib/format.ts` comment text. Manual: open the app, check sidebar reads Command / Unit overview / Soldiers, detail page tab list is "Soldier analysis", card labels use ` | `.

**Commit**: `frontend: military wording and plain-ASCII punctuation in all user-facing text`

---

### Phase 2 — S4-T02 Sensor summary (R1)

**Files**: new `components/SensorSummary.tsx`; `lib/config.ts` (+`SENSOR_SUMMARY_TEXT`); `pages/Device.tsx`; `components/DeviceCard.tsx`; `app.css`.

**Steps**
1. `config.ts`: `export const SENSOR_SUMMARY_TEXT = '4 sensors | 6400Hz logging'` with a comment that it is a deliberate literal (R1).
2. `SensorSummary.tsx`:
   - Props `{ device: LiveDevice; quality: number | null; expandable?: boolean }`.
   - `if (!device.online) return null`.
   - `const [open, setOpen] = useState(false)`.
   - Collapsed: `<span className="sensor-summary">` containing, when `expandable`, `<button type="button" className="sensor-summary-toggle" aria-expanded={false} aria-label="Show sensor detail" onClick={toggle}><ChevronsRight size={13} aria-hidden/></button>`, then `<span className="sensor-summary-text">{SENSOR_SUMMARY_TEXT}</span>`.
   - Expanded: the button with `ChevronsLeft`, `aria-expanded`, `aria-label="Hide sensor detail"`, then `<QualityMeter quality={quality}/>` and `<SensorDots sensors={device.sensors} detailed/>` (both imported from `./bits`, unchanged).
   - `toggle` calls `e.preventDefault(); e.stopPropagation()` before `setOpen` (defensive; the detail header is not a link, but the component must stay safe if ever placed in the card).
3. `Device.tsx:120-121`: replace `<QualityMeter …/><SensorDots … detailed/>` with `<SensorSummary key={id} device={device} quality={live?.q ?? device.quality} expandable />`. `key={id}` remounts on soldier switch (React Router keeps the page mounted across `:id` changes), which is what enforces "never persisted".
4. `DeviceCard.tsx:76-77`: replace with `<SensorSummary device={device} quality={live?.q ?? device.quality} />`.
5. `app.css`: `.sensor-summary { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--ink-2); font-variant-numeric:tabular-nums }`, `.sensor-summary-toggle` mirrors `.rename-btn` (`color:var(--ink-3); padding:3px; border-radius:var(--radius-s)`; hover `color:var(--ink); background:var(--surface-2)`; visible `--accent` focus ring per UIUX §12).

**Edge cases**: `QualityMeter` and `SensorDots` are now unused outside `SensorSummary` and `HumanoidFigure`'s limb logic; `noUnusedLocals` only flags locals, so no dead-export error, but remove now-unused imports in `Device.tsx` and `DeviceCard.tsx` or `tsc` fails. Offline device on the detail page: the summary disappears together with its expanded state (state is irrelevant once hidden; on return the component is still mounted so `open` persists until navigation, which is acceptable because the device page for the same soldier was never left). Dummy soldiers are always online, so the summary always shows for them.

**Verify**: `npm run build`. Manual: overview cards show `4 sensors | 6400Hz logging` with no button; detail header shows `» 4 sensors | 6400Hz logging`; click -> real meter + four `limb NNNHz` entries, icon `«`; click -> collapsed; navigate to another soldier -> collapsed; stop the simulator -> summary disappears with the offline badge; restart -> reappears collapsed. Keyboard: Tab reaches the toggle, Enter/Space toggles, `aria-expanded` flips (inspect in DevTools).

**Commit**: `frontend: collapse the quality/sensor readout behind a static sensor summary`

---

### Phase 3 — S4-T03 Synthetic signal foundations (ids, noise, profiles, signal)

**Files**: new `lib/demo/ids.ts`, `noise.ts`, `profiles.ts`, `signal.ts` and their `.test.ts`.

**Steps**
1. `ids.ts`: `DEMO_PREFIX='demo-'`, `DEMO_COUNT=5`, `isDemoId = id.startsWith(DEMO_PREFIX)`, `demoRank(id)` = `0` for non-demo, else the parsed integer (`99` if unparsable).
2. `noise.ts`: `hash32(seed,k)`: integer mix (xorshift/multiply, `>>> 0`) of `seed ^ (k * 0x9E3779B1)` -> `/ 2**32`. `valueNoise(seed,x,period)`: `i = floor(x/period)`, `f = smoothstep((x - i*period)/period)`, lerp between `hash32(seed,i)` and `hash32(seed,i+1)`. `fbm` sums two octaves (weights 0.7/0.3) normalised to [0,1]. All inputs may be negative; use `Math.floor`, never `|0` truncation.
3. `profiles.ts`: constants (§0 R2); types `Keyframe {s,v}`, `Episode {rule_id, from_s, to_s|null, every_s=120, severity?, metric?}`, `DemoProfile` (id, rank, display_name, seed, qualityBase, flags, soc0, socDrainPerHour, envelopes for `c,m1,m2,m3,m4,m5`, texture amp/period per series, episodes). `DEMO_PROFILES` per Appendix C. Sensors built by `demoSensors(profile, nowMs)` in Phase 5 (needs `last_seen`).
4. `signal.ts`:
   - `envelopeAt(kf, s)`: clamp `s` to `[kf[0].s, kf[last].s]`, find segment, smoothstep interpolation.
   - `sampleAt(profile, s)`: per series `env + amp*(2*fbm(seed+series, s, period)-1) + microJitter` where `microJitter = 0.15*(hash32(seed+series+100, floor(s*60))-0.5)`; clamp m1..m4 and c to `[0,100]`, m5 to `[-100,100]`; round to 2 dp (matches the backend's `round(v, 4)` visually).
   - `qualityAt(profile, s)`: `qualityBase + 0.02*(valueNoise-0.5)`, clamp `[0,1]`.
   - `bucketStats(profile, s0, s1, n=24)`: if `s1 <= 0` return `null` (before the session); sub-sample `n` points evenly in `[s0, s1)`, compute mean/sd per series and composite min/max/sd; `quality` mean; `n` = `round((s1-s0)*60)` (rows the backend would have had).
   - `slopePerMin(profile, s, spanS=300)`: `(envelopeAt(c, s) - envelopeAt(c, s-spanS)) / (spanS/60)` on the composite envelope only (noise-free, so forecasts do not jitter).
5. Tests: `ids.test.ts` (prefix, rank, non-demo -> 0); `noise.test.ts` (determinism, range, continuity: `|valueNoise(x+ε) - valueNoise(x)| < ε*k`); `signal.test.ts` (ranges for `s ∈ {-1e5, -1, 0, 1, 3600, 7500, 1e6}`; determinism; `bucketStats` avg within `[min,max]`, `null` before session; `slopePerMin` sign matches the envelope; no `NaN` anywhere).

**Edge cases**: keyframes must be strictly increasing in `s` (assert in a test over `DEMO_PROFILES`); `hash32` on non-integer `k` must floor first; `fbm` with `period=0` guarded (throw in dev via assertion test). Rounding to 2 dp must not push m5 past ±100 (clamp after rounding).

**Verify**: `npm test`; `npm run build` (unused-export modules compile).

**Commit**: `frontend(demo): deterministic synthetic signal foundations`

---

### Phase 4 — S4-T04 Live feed + registry integration (soldiers appear, charts move)

**Files**: new `lib/demo/live.ts` + test; `lib/demo/api.ts` (only `demoDevices` this phase); `lib/ws.tsx`; `lib/devices.ts`; `pages/Device.tsx` (loading guard); `pages/Overview.tsx` (loading notice already prepared).

**Steps**
1. `live.ts`:
   - `fillDemoBuffer(buf, profile, nowMs)`: `nowK = floor(nowMs/1000*60)`; `lastK = buf[0].length ? round(buf[0][last]*60) : nowK - 60*LIVE_BUFFER_S - 1`; if `nowK - lastK > 60*LIVE_BUFFER_S` then clear all seven arrays and set `lastK = nowK - 60*LIVE_BUFFER_S`; for `k in (lastK, nowK]`: `t = k/60`, `s = t - SESSION_START_MS/1000`, push `t`, `m[0..4]`, `c` from `sampleAt`; then trim: find first index with `t >= nowK/60 - LIVE_BUFFER_S` and `splice(0, idx)` on all seven arrays (one splice per series per step, not per-sample `shift`).
   - `startDemoFeed(buffers, now = Date.now)`: `for each profile: buffers[id] ??= emptyData()`; `const id = setInterval(() => { for each profile fillDemoBuffer(...) }, 50)`; run once immediately; return `() => clearInterval(id)`.
   - `demoLatestMeta(id, nowMs)`: `{ q: qualityAt(...), flags: profile.flags, cal: null }`.
   - `emptyData()` duplicated locally as a tiny helper (ws.tsx's is not exported; do not export it to avoid a value import from ws).
2. `ws.tsx`:
   - `const demoBuffers = useRef<Record<string, LiveData>>({})`.
   - `getBuffer = useMemo(() => (dev) => isDemoId(dev) ? demoBuffers.current[dev] : buffers.current[dev], [])`.
   - In the 250 ms snapshot, after the existing loop: iterate `demoBuffers.current`, `snap[dev] = { t, m, c, ...demoLatestMeta(dev, Date.now()) }` from the newest row (skip if empty).
   - New `useEffect(() => startDemoFeed(demoBuffers.current), [])`.
   - Nothing else changes (`push`, `backfill`, `rebackfillAll`, `paused`, `status` untouched).
3. `lib/demo/api.ts` `demoDevices(nowMs): Device[]`: for each profile `{ device_id, display_name, online: true, last_seen: iso(nowMs), quality: qualityAt(...), soc: clamp(round(soc0 - drain*s/3600), 5, 100), sensors: demoSensors(profile, nowMs) }` where sensors are the four `LIMB_MAP` entries `(0,1) left_shin, (0,2) left_thigh, (1,1) right_thigh, (1,2) right_shin`, `rate_hz = 639.5 + 1.5*(valueNoise(seed+limb, s, 30)-0.5)`, `last_seen = iso(nowMs)`.
4. `devices.ts`: in `useMergedDevices`, `devices = [...real, ...demoDevices(now).map(d => ({...d, lastSignalMs: now}))]`; in `useVisibleDevices`, comparator `demoRank(a.device_id) - demoRank(b.device_id) || Number(b.online) - Number(a.online) || a.display_name.localeCompare(b.display_name)`; `onlineCount` unchanged (counts dummies, per R2).
5. `Device.tsx:92`: `if (isLoading && !isDemoId(id)) return <p className="notice">Loading...</p>`.
6. `live.test.ts` with `vi.useFakeTimers()` + `vi.setSystemTime`: (a) first fill produces exactly `60*LIVE_BUFFER_S` rows ending at `floor(now*60)/60`; (b) advancing 1 s and filling adds 60 rows, no duplicates, still trimmed; (c) a 10-minute gap regenerates (length stays `60*LIVE_BUFFER_S`, first `t` is `now - LIVE_BUFFER_S`); (d) `startDemoFeed` returns a stop that clears the interval (no growth after stop); (e) times strictly increasing.

**Edge cases / races**: StrictMode mounts the effect twice: the second `startDemoFeed` sees a filled buffer and resumes; the first's cleanup cleared its interval, so exactly one timer survives. Hidden tab: browsers throttle the timer to 1 Hz or slower; `LiveChart` does not draw while hidden; on return the first tick catches up (≤ 60 s) or regenerates (> 60 s). `useMergedDevices` runs `demoDevices(Date.now())` on every render (cheap: 5 objects); `Date.now()` per render keeps battery drain honest. The 250 ms snapshot already re-renders all `useLive` consumers; dummies add 5 entries, no new re-render source. Sidebar/Overview: `isLoading` stays the real registry's; the overview loading notice is suppressed because `visible.length > 0`.

**Verify**: `npm test`; `npm run build`. Manual (stack up, `npm run dev`, logged in): sidebar shows the five soldiers with live risk numbers; overview shows five cards with moving sparklines, `now` values updating, battery, flag chips (`carried calibration` on CPL Nguyen, `warming up` on SPC Ramirez), no calibration badge, `4 sensors | 6400Hz logging`; detail page for each: six live charts scrolling, humanoid limbs lit, m5 side label; DevTools Network filtered on `demo-`: **zero** requests. With the simulator streaming one real device: it sorts first everywhere. (Projections/insights/history tabs still call the API for demo ids at this point and 404/empty: expected until Phase 5/6.)

**Commit**: `frontend(demo): five synthetic soldiers with 60 Hz live buffers`

---

### Phase 5 — S4-T05 Synthetic REST data (windows, history, forecasts, recent) + api branches

**Files**: `lib/demo/api.ts` (complete), `lib/demo/api.test.ts`, `lib/api.ts`.

**Steps**
1. `demoWindows(id, nowMs)`: for each label in `DEMO_WINDOWS`: `W = durationToSeconds(label)`, `cur = bucketStats(profile, sNow-W, sNow)`, `prev = bucketStats(profile, sNow-2W, sNow-W)`; entry `{ window, from: iso(nowMs - W*1000), m: cur.m, sd: cur.sd, composite: cur.composite, quality: cur.quality, coverage: min(1, max(0, (sNow - max(0, sNow-W)) / W)), trend }` with `trend` from `delta = cur.avg - prev.avg`, dead band `max(2, 0.5 * pooledSd)`, `'flat'` when `prev` is null.
2. `demoHistory(id, window, buckets, nowMs)`: `W`, `span = W / buckets` (integer by `evenBucketCount`; if the caller passes a non-dividing count, fall back to `evenBucketCount(window)`), `from = nowMs - W*1000`, bucket `k`: `t = iso(from + k*span*1000)`, stats over `[sFrom + k*span, sFrom + (k+1)*span)`, `null` when `bucketStats` returns null; `bucket_s = span`.
3. `demoForecasts(id, nowMs)`: `made_at = floor(nowMs/60000)*60000` (mimics `PREDICT_INTERVAL_S`), `model_version = DEMO_MODEL_VERSION`, `provisional: false`, points for each horizon `h`: `pred = clamp(envelopeAt(c, sNow) + slopePerMin(profile, sNow) * h/60, 0, 100)`, `half = 3 + 2.2*sqrt(h/600)`, `ci_low = clamp(pred - half)`, `ci_high = clamp(pred + half)`, `target_time = iso(made_at + h*1000)`. Profiles that must trigger `rising_risk`/`residual_load` (demo-1, demo-4) get their envelopes tuned so the rule preconditions hold (checked in tests).
4. `demoRecent(id, seconds, nowMs)`: `t0 = iso(nowMs - seconds*1000)`, rows `[offsetMs, m1..m5, c, q]` at 60 Hz from `sampleAt` (defensive only; never reached from the WS backfill path).
5. `demoInsights`, `demoAdviceTimeline`, `demoDecision` are stubs returning empty/neutral values this phase (filled in Phase 6) so `lib/api.ts` compiles.
6. `lib/api.ts`: prefix each helper: `fetchWindows`, `fetchHistory`, `fetchForecasts`, `fetchRecent`, `fetchAdviceTimeline`, `fetchCurrentAdvice`, `renameDevice` (returns the unchanged device), `postInsightDecision`, `fetchInsights(dev)` with `if (isDemoId(dev)) return Promise.resolve(demoX(...))`. `fetchInsights(undefined, limit)`: `real = request(...).catch(e => { if (e instanceof ApiError && e.status === 401) throw e; return [] })`, merge with `demoInsights(undefined, limit)`, sort by `created_at` desc, slice `limit`.
7. `api.test.ts` (demo): three windows with the configured labels and `from` in the past; history honours `buckets`, `bucket_s * buckets === W`, all 30 buckets non-null for every window at `sNow = SESSION_AGE_S`; forecasts sorted by horizon, `ci_low <= pred <= ci_high`, all within `[0,100]`, `made_at` on a minute boundary; devices: 5, ids `demo-1..5`, soc in `[5,100]`, four sensors with the `LIMB_MAP` limb names. `lib/api.test.ts`: stub `globalThis.fetch` with a spy that throws; call every helper with `demo-3`; assert the spy was never called and each resolves to the demo shape; call `fetchInsights(undefined, 20)` with fetch rejecting -> resolves with only dummy rows.

**Edge cases**: `evenBucketCount('2h')` = 30 buckets of 240 s and `'30m'` = 30 of 60 s; `HistoryBars` keys buckets by index and `ForecastChart` anchors the band at the last actual, both satisfied. `fetchInsights` merge changes error semantics for `['insights','all']` only (Overview never renders its error). `authExpired()` still fires inside `request()` on 401 before the catch. Never call `Date.now()` inside the pure demo functions; take `nowMs` as a parameter (default `Date.now()` only at the `lib/api.ts` boundary) so tests are deterministic.

**Verify**: `npm test`; `npm run build`. Manual: detail page History tab shows three windows with 30 bars each and the table; Projections tab shows `+10m/+30m/+1h`, band, footer `Shaded band shows the projection interval. Made 12s ago | trend-ols-1`; overview cards show a projected headline with two smaller horizons and `made Ns ago`; hero `Highest projected | +10m` names a soldier; Network still has no `demo-` requests.

**Commit**: `frontend(demo): synthetic windows, history, projections and api short-circuits`

---

### Phase 6 — S4-T06 Synthetic insights, advice timeline, evidence, no-op mutations

**Files**: `lib/demo/insights.ts` + test; `lib/demo/api.ts` (`demoInsights`, `demoDecision` real); `components/bits.tsx` (`RenameInline.commit`).

**Steps**
1. `DEMO_ACTIONS`: verbatim port of `ACTIONS` (ids `ease_off`, `cap_session`, `plan_recovery`, `lower_landings`, `soften_landings`, `flag_review`; `text`, `rank`, `tip`) with ` - ` for em dashes.
2. Rule templates: `message`, `rationale`, `reason` and `action` per rule as functions of `(display_name, ev)`, ported from `RULES` and `_deviation_rationale`/`_deviation_reason`, with "this athlete's" -> "this soldier's", em dashes -> ` - `. `×` stays. `data_quality` rows are event-log only (no `action_id`, no `reason`) exactly as the backend.
3. `demoInsightRows(profile, nowMs)`: for each episode, `t = from_s, from_s+every_s, …` while `t <= min(to_s ?? sNow, sNow)` and `t >= sNow - 7200`; per row: `ev` = `_base_evidence` equivalent from `sampleAt`/`bucketStats(t-30, t)` for the rule's metric (`window:'30s'`, `baseline_window:'2h'`, `value`, `baseline` = 2h mean up to `t`, `sd` = 2h sd floored at 3, `z = (value-baseline)/sd` rounded 2 dp, `quality`, `coverage`) plus per-rule keys (`horizon`/`projected`, `trend`/`settles_at`, `threshold`, `composite_avg`, `pred`, `also`, `unvalidated` for `movement_quality`); `severity` = episode override, else `alert` if `z >= 3` else rule default; `action_id` resolved (m1 -> `lower_landings`, else `soften_landings`); `created_at = new Date(SESSION_START_MS + t*1000).toISOString()`; `insight_id = 900000 + rank*10000 + i`; `device_id = profile.id`. Return newest-first. Rows carry `action_id` and `reason` as extra fields (structurally compatible with `Insight`).
4. `groupActions(rows, max)`: line-for-line port of `group_actions` (drop `data_quality`; key `action_id ?? action ?? rule_id`; newest per `rule_id`; reasons sorted by severity desc then `rule_id`; `text = reason ?? rationale ?? message`; `unvalidated = all(...)`; `updated_at = max(created_at)`; sort by severity desc then catalogue rank; slice).
5. `bucketTimeline(rows, nowMs)`: edges `[('live',150), ('5m',300), ('30m',1800), ('2h',7200)]`; assign each row to the first edge with `age <= edge`; per bucket `groupActions(...)` then sort `updated_at` desc; `decision: null` on every action. `demoAdviceTimeline(id)` returns `{ device_id, generated_at, hold_s:150, max_actions:3, windows:['live','5m','30m','2h'], buckets }`; `demoCurrentAdvice(id)` = the live bucket only.
6. `demoInsights(id|undefined, limit)`: per-device rows newest-first sliced to `limit`; `undefined` = all five profiles merged and sliced.
7. `demoDecision(body)`: returns `{ decision, note: decision==='adopted' ? null : (note ?? null), decided_by: null, decided_at: iso(now) }` without storing anything.
8. `bits.tsx` `RenameInline.commit`: after `setEditing(false)`: `if (isDemoId(device.device_id)) { setName(device.display_name); return }`.
9. `insights.test.ts`: rows newest-first with strictly decreasing `created_at`; every timeline reason's `(rule_id, created_at)` exists in `demoInsights(id, 100)` (join exactness); `groupActions` parity cases (drops `data_quality`; two rows same rule -> one reason; cap 3; alert before warning; `unvalidated` only when all reasons are m4/m5); every `action_id` in the timeline has a `DEMO_ACTIONS` entry with a `tip`; no `— – … ·` in any generated string; story assertions: demo-1 live bucket contains `cap_session`, demo-5 live bucket contains `ease_off`, demo-3 live bucket is empty; consistency: any `composite_high` row has `composite_avg >= 85`, any `rising_risk` row has `pred >= 92`, any `residual_load` row has `settles_at >= 85`, deviation rows have `|z| >= 2`; at least one and at most 5 alert-severity rows in the last 30 min across all dummies (hero count stays believable).

**Edge cases**: `InsightsPanel` keys cards by `${bucket.window}:${action_id}`; the same action may recur across buckets (fine). `DecideRow` `onSuccess` invalidates `['advice-timeline', demo-N]` -> regenerator returns `decision: null` -> the card returns to the undecided state, matching "does nothing". Evidence `context` must not include keys that `formatEvidence` would render badly (`also` must be an array of metric names). `limit=100` per device: an episode with `to_s: null` over 2 h emits 60 rows; two such episodes would exceed 100 and the evidence join would miss the oldest rows (fallback is the short reason, harmless), so keep at most one open-ended episode per soldier.

**Verify**: `npm test`; `npm run build`. Manual: each soldier's Insights tab matches Appendix C (cards, age labels `live` / `past 5m` / `past 30m` / `past 2h`, fading left edges, reasons, `PTI cue`, Evidence expander with `vs their normal range +2.3× their usual spread`); overview cards show the top insight line; hero `Alerts | 30 min` shows a small non-zero number; Adopt -> nothing recorded, buttons return; Override + note + Save -> form closes, card unchanged; rename a dummy (Enter, blur, Esc) -> name unchanged; Network: no `demo-` requests, no PATCH/POST for dummies.

**Commit**: `frontend(demo): scripted advice timeline with evidence; rename and decisions are no-ops on soldiers`

---

### Phase 7 — S4-T07 Hero copy, docs, final verification

**Files**: `pages/Overview.tsx` (hero), `docs/UIUX.md`, `docs/tasks/STAGE4.md` (as-built notes), optionally `docs/APPFLOW.md`/`docs/PRD.md` where they name changed labels.

**Steps**
1. Replace the hero eyebrow, `<h1>` and `<p>` with Appendix B (keep the `<br/>` + `.hero-accent` structure and the bold `Injury Risk`). Keep the legend.
2. `docs/UIUX.md`: §1 sidebar labels (Command / Unit overview / Soldiers / "no soldiers registered"); §3 hero copy summary and panel header (static sensor summary replaces meter + dots), "Soldiers online"; §4 header (`»` toggle, hidden offline), tabs label; §6 quality row note (reachable via the toggle only); §7 empty-state strings; §11 add "plain ASCII punctuation; military vocabulary; PTI cue"; §13 add a **§14 Demo soldiers** section: purpose, `demo-` ids, `lib/demo/` map, injection seams, no HTTP, no marker (user decision 2026-09-12), how to change names/stories. Also update the `Coaching cue` and `Athletes · online` mentions in place. `rg -n "Athlete|Coaching cue|·" docs/APPFLOW.md docs/PRD.md` and fix only label mentions.
3. `docs/tasks/STAGE4.md`: append "as built" notes per task (deviations, if any).
4. Full verification (below), then commit.

**Final verification checklist**
1. `cd frontend && npm run build && npm test` green.
2. `rg "—|–|…|·" frontend/src frontend/index.html` -> comments and `format.ts:89` only. `rg -i "athlete|coaching cue" frontend/src frontend/index.html` -> comments only.
3. Stack: `docker compose --profile debug up -d --build` (api on 127.0.0.1:8000), `npm run dev`, sign in with a `SEED_USERS` account.
4. Overview: hero shows the new eyebrow/headline/paragraph, `Soldiers online 5`, `Highest projected | +10m` naming a soldier, `Alerts | 30 min` > 0; five cards in order SGT Alvarez, CPL Nguyen, PFC Okafor, SSG Brooks, SPC Ramirez; each card: static summary, battery, projected headline + two horizons, moving sparkline, top insight, flags where scripted.
5. Detail page per soldier: header (`»` summary, online, battery), six live charts at 60 Hz, risk hero with trend arrow, Insights / History / Projections as in Phases 5–6; `Soldier analysis` tab list; keyboard navigation of tabs and the summary toggle.
6. Real device: `uv run python simulator/simulate.py --devices 1 --base-id 100 --duration 300 --target 127.0.0.1:<UDP_PORT from .env>`; it appears first in sidebar and grid; while online its summary shows and expands to real numbers; after it stops, the offline badge shows and the summary hides; its rename and decisions still hit the API (Network shows PATCH/POST).
7. Reload three times: identical dummy stories. Hide the tab for 3 minutes: charts resume with no gap. `docker compose restart api`: WS reconnects, dummies unaffected.
8. Production-like: `docker compose up -d --build caddy`, open `http://localhost`, repeat steps 4–5 quickly.
9. `git log --oneline main..feat/demo-frontend` shows the seven phase commits; nothing pushed.

**Commit**: `frontend: commander-readiness hero copy; docs: UIUX and stage-4 as-built`

---

## Appendix A — User-facing string changes (Phase 1)

| File:line | Before | After |
|---|---|---|
| `Sidebar.tsx:28` | `Dashboard` | `Command` |
| `Sidebar.tsx:31` | `Overview` | `Unit overview` |
| `Sidebar.tsx:35` | `Athletes` | `Soldiers` |
| `Sidebar.tsx:36` | `none registered` | `no soldiers registered` |
| `Sidebar.tsx:67` | `reconnecting…` | `reconnecting...` |
| `Overview.tsx:107` | `Athletes online` | `Soldiers online` |
| `Overview.tsx:113` | `` `Highest projected · ${…}` `` | `` `Highest projected \| ${…}` `` |
| `Overview.tsx:120` | `'—'` | `'--'` |
| `Overview.tsx:127` | `Alerts · 30 min` | `Alerts \| 30 min` |
| `Overview.tsx:168` | `Failed to load devices — retrying…` | `Failed to load soldiers - retrying...` |
| `Overview.tsx:169` | `Loading devices…` | `Loading soldiers...` (shown only when `visible.length === 0`) |
| `Overview.tsx:176` | `Waiting for devices… point wearables at this server's UDP port.` | `Waiting for soldiers... point wearables at this server's UDP port.` |
| `Overview.tsx:82-95` | eyebrow / h1 / paragraph | Appendix B (Phase 7) |
| `Device.tsx:92,239` | `Loading…` | `Loading...` |
| `Device.tsx:96` | `Unknown device.` | `Unknown soldier.` |
| `Device.tsx:98,114` | `Back to overview` | `Back to unit overview` |
| `Device.tsx:135` | ` — last seen ` | ` - last seen ` |
| `Device.tsx:152` | `Injury risk · now` | `Injury risk \| now` |
| `Device.tsx:223` | `Device analysis` | `Soldier analysis` |
| `DeviceCard.tsx:88` | `` `Projected risk · ${…}` `` | `` `Projected risk \| ${…}` `` |
| `DeviceCard.tsx:110` | `Injury risk · now` | `Injury risk \| now` |
| `DeviceCard.tsx:112` | `waiting for the first projection…` | `waiting for the first projection...` |
| `bits.tsx:34` | `quality —` | `quality --` |
| `bits.tsx:42` | `Data quality ${…} — share of expected sensor samples arriving` | ` - ` |
| `bits.tsx:80` | `` `${label} · ${…} Hz · ${…}` `` | `` `${label} \| ${…} Hz \| ${…}` `` |
| `bits.tsx:195` | `Athlete name` | `Soldier name` |
| `CalibrationBadge.tsx:120` | `Calibrating — keep the athlete standing still for the countdown.` | `Calibrating - keep the soldier standing still for the countdown.` |
| `CalibrationBadge.tsx:124` | `Stand still · {n}s` | `Stand still \| {n}s` |
| `CalibrationBadge.tsx:133` | `…not the athlete.` | `…not the soldier.` |
| `CalibrationBadge.tsx:141` | `Calibrated on this athlete, this session.` | `Calibrated on this soldier, this session.` |
| `HumanoidFigure.tsx:403` | `Athlete wearing four leg sensors streaming live motion data` | `Soldier wearing four leg sensors streaming live motion data` |
| `InsightsPanel.tsx:93` | ` — {note}` | ` - {note}` |
| `InsightsPanel.tsx:99` | `… — change it` | `… - change it` |
| `InsightsPanel.tsx:212` | `${updated_at} — updated …` | ` - ` |
| `InsightsPanel.tsx:230` | `Coaching cue` | `PTI cue` |
| `InsightsPanel.tsx:279,280` | `Loading insights…` / `Couldn't load insights — retrying…` | `Loading insights...` / `Couldn't load insights - retrying...` |
| `HistoryBars.tsx:37` | `'·'` | `'-'` |
| `HistoryBars.tsx:85` | `range a–b` | `range a-b` |
| `HistoryBars.tsx:89` | ` · ${side}` | ` \| ${side}` |
| `HistoryBars.tsx:93,269` | `'—'` | `'--'` |
| `HistoryBars.tsx:182` | `… has data — these averages …` | ` - ` |
| `HistoryBars.tsx:184` | `partial · {pct}` | `partial \| {pct}` |
| `HistoryBars.tsx:197,198,201` | `Loading history…` / `Couldn't load history — retrying…` / `collecting… no data yet in` | `...` / ` - ` / `collecting... no data yet in` |
| `ForecastChart.tsx:54,58` | `Loading projections…` / `First projection in a couple of minutes…` | `...` |
| `ForecastChart.tsx:174,210` | `${lo}–${hi}` | `${lo}-${hi}` |
| `ForecastChart.tsx:211` | `'—'` | `'--'` |
| `ForecastChart.tsx:221` | `Made {ago} · {model}` | `Made {ago} \| {model}` |
| `Login.tsx:30` | `Too many attempts — wait a minute and try again.` | `Too many attempts - wait a minute and try again.` |
| `Login.tsx:74` | `Signing in…` | `Signing in...` |
| `format.ts:34,60` | `'—'` | `'--'` |
| `evidence.ts:47` | `'—'` | `'--'` |
| `metrics.ts:47` | `Movement tremor vs. this athlete when fresh — rises as control degrades` | `Movement tremor vs. this soldier when fresh - rises as control degrades` |
| `metrics.ts:57` | `… in this session — magnitude, …` | ` - ` |
| `metrics.ts:150,155,162` | `… — hardware fault` / `… — the affected metric …` / `… — Impact and Loading Rate …` | ` - ` |
| `metrics.ts:188` | `… learning this athlete’s baseline — a value is coming` | `… learning this soldier's baseline - a value is coming` |
| `index.html:10` | `HIPPOS Motion Intelligence — live injury-risk monitoring for trainers` | `HIPPOS Motion Intelligence - live injury-risk monitoring for commanders` |
| `index.html:12` | `HIPPOS — Motion Intelligence` | `HIPPOS - Motion Intelligence` |

Unchanged on purpose: `format.ts:89` curly quotes; `evidence.ts:51` `×`/`−`; metric names; Adopt/Override; all comments.

## Appendix B — Hero copy (Phase 7, for review)

Eyebrow: `Lower-limb telemetry | 4 sensors | live`

Headline:
```
Every stride under load -
<span class="hero-accent">turned into readiness a commander can act on.</span>
```

Paragraph:

> Four sensors on each soldier's thighs and shins stream motion hundreds of times a second, under load, on the march and in training. We distill it into six scores: how hard each footfall lands, how abruptly load is applied, how much has accumulated, how steady the movement stays and how evenly left and right share the work, combined into one **Injury Risk** trend that rises when the same task starts costing more than it should. Built for units like the 1st Cavalry Division, it shows a commander who is fit to task now, who needs a rest day before an injury takes them off strength, and where each soldier is heading over the coming session.

## Appendix C — Soldier profiles (Phase 3/6)

| id | name | composite story (session seconds `s`; now ≈ 7500) | flags | battery | episodes (rule -> card) |
|---|---|---|---|---|---|
| demo-1 | SGT Alvarez | 25 at s=0, 40 by 3600, 70 by 6300, 86-90 from 6900 on; m3 climbing to ~80; m1/m2 high | none | 75%, -6/h | `accumulated_load` (warning from 5400, alert from 7080, open) -> **cap_session** live; `composite_high` (warning from 6900, open) -> ease_off live; `rising_risk` (warning from 6600, open) -> plan_recovery; `load_spike` (warning 4200-4800) -> ease_off in past 30m/2h |
| demo-2 | CPL Nguyen | steady 18-22 all session; m1 step +2.3 sd at 6300 and 7320 | `carried_over` | 93%, -5/h | `impact_deviation` metric m1 (info, 6300-6420) and (info, 7320-7440) -> **lower_landings** in past 30m and past 5m |
| demo-3 | PFC Okafor | 5-8 all session, quality 0.97 | none | 98%, -4/h | `movement_quality` metric m4 (info, 2400-2520) -> flag_review in past 2h only; live bucket empty ("Nothing to flag right now") |
| demo-4 | SSG Brooks | 20 -> 62 peak at 6000, easing to 34 by 7500 (5m trend down); m5 ≈ -18 (more load right) | none | 60%, -7/h | `residual_load` (warning, 6600-6900) -> plan_recovery past 30m; `movement_quality` metric m5 (info, 7020-7140) -> flag_review past 5m |
| demo-5 | SPC Ramirez | 12 until 7200, then rising to 30 by 7500; m1/m2 jump | `warming_up` | 58%, -6/h | `load_spike` (warning, from 7460, open) -> **ease_off** live |

Rank/surname strings, envelopes and episode timings are data in `profiles.ts`; changing a story is a table edit, not a code change.
