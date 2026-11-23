# Remote access over nickolaspearson.com + the money advisor

Planned 2026-11-23 with Nick. Nothing here is built or deployed yet. Nick wants every detail settled before
anything ships. Decisions he made are marked **Decided**; the questions still open are in "Open questions" at the end.

---

## Part 1: The companion reaches JARVIS through `jarvis.nickolaspearson.com`

**Decided:** auto home/away. On home Wi-Fi the app uses the Surface's LAN address; anywhere else it uses
`https://jarvis.nickolaspearson.com`. The phone no longer needs Tailscale for JARVIS.

### What exists (verified 2026-11-23)
- `*.nickolaspearson.com` is a DNS-only wildcard (Cloudflare nameservers, not proxied) to the Lightsail server
  3.128.92.104, which runs Caddy with automatic HTTPS (`nickolaspearson-site`, `deploy/from-mac.sh`).
- Lightsail is on the tailnet as `lightsail-web` (100.119.21.113). The website's chat already reaches Core this
  way (`/var/lib/nick-site/jarvis.json`, the `website` client token).
- Core (Surface, tailnet 100.69.8.11, LAN 10.0.0.73 reserved) serves on :8000. **Every route needs a token**
  (checked: unauthenticated `/api/glance`, `/api/portfolio`, `/api/theme`, `/api/tv/clock` → 401). Nothing is
  trusted by IP.
- The app already keeps the server address as a setting (`AppSettings.baseURLString`/`resolvedBaseURL`) and
  probes home vs Tailscale for the Links tab (`HubViewModel.probeHomeNetwork`, `onHomeNetwork`).
- Long-lived connections the proxy must carry: websockets (`LiveVoiceConnection`, `/api/voice/satellite`),
  SSE/streams (`/api/clients/events`, `/api/voice/stream`, `/api/home/stream`, `/api/traffic/stream`), and the
  camera.

### Pieces
1. **Caddy block on Lightsail** (in `nickolaspearson-site/deploy`, its own `# BEGIN JARVIS PROXY` block, backed
   up like the site block):
   ```
   jarvis.nickolaspearson.com {
       reverse_proxy 100.69.8.11:8000 {
           flush_interval -1              # stream SSE/voice immediately, no buffering
           header_up X-Forwarded-For {remote_host}
           transport http {
               read_timeout 0             # long-lived streams/websockets stay open
           }
       }
       request_body { max_size 25MB }     # voice clips, camera snapshots
       log { output file /var/log/caddy/jarvis.log }
   }
   ```
   No Caddy rate-limit (needs a plugin); the lockout lives in Core (item 3).
2. **Core trusts `X-Forwarded-For` only from Lightsail.** Run uvicorn with `proxy_headers=True,
   forwarded_allow_ips="100.119.21.113"` (new `.env` `JARVIS_TRUSTED_PROXIES`), so logs and the lockout see the
   phone's real IP and nobody else can spoof one.
3. **Failed-token lockout in Core.** `authorize`/`require_scope` count 401s per client IP: 20 in 10 min blocks
   that IP for 15 min (`JARVIS_AUTH_LOCKOUT_*`), and one `security` alert "Someone is guessing JARVIS's token from
   <ip>". Never locks out a request that has a valid token. Tests for both.
4. **A reachability monitor.** Core checks `https://jarvis.nickolaspearson.com/health` (round trip through
   Lightsail) every 10 min; two failures in a row raise one "Away access is down" alert (Lightsail down, Caddy
   down, or Lightsail's Tailscale dropped). Clears itself.
5. **App: two addresses, chosen automatically.**
   - `AppSettings` gains `homeURLString` (default `http://10.0.0.73:8000`) and `awayURLString` (default
     `https://jarvis.nickolaspearson.com`); today's `baseURLString` migrates into whichever slot it matches (a
     `100.x` Tailscale address becomes an optional third "fallback").
   - A `ServerRoute` resolver watches `NWPathMonitor`: on Wi-Fi it probes home (`/api/glance`, 1.5 s timeout);
     home answers → home, else away. Cellular → away straight away. A request that fails with a connection error
     retries once on the other route and flips the choice.
   - Streams and the voice websocket reconnect when the route flips.
   - **Watch out:** `AppSettings` hashes the base URL into a fingerprint (line 41) that keys caches. Switching
     routes must not look like "a different server", so the fingerprint uses a stable server identity (token
     + away URL), not the live route.
   - Settings shows "Connected at home" / "Connected away (nickolaspearson.com)", plus manual override
     (Auto / Home / Away).
   - Error text stops telling Nick to check Tailscale (`JarvisAPIClient.swift:47`).
   - ATS: `https` needs nothing; the LAN `http` address already works today.
6. **Same change in JARVIS for Mac** (`mac/JarvisMac`), which also stores one server address.
7. **Not in scope:** the Links tab's service links (Plex, HA, Radarr, ...) still use their own home/Tailscale
   addresses; the TV stays LAN-only; the website keeps its own `jarvis.json` path.

### Security posture after this
The admin token becomes the only lock on Core from the whole internet, and Core can approve Schwab trades,
control the room and show the camera. Mitigations in this plan: HTTPS only, the lockout, the alert, and the token
staying in the Keychain. Follow-up worth doing soon after: the deferred **Face ID gate** for camera and trade
approvals in the app (memory: camera-faceid-backlog). A device certificate (mTLS) was offered and not chosen; it
can be added later in Caddy without touching Core.

### Rollout (each step only when Nick says go)
1. Core: proxy headers + lockout + monitor (deploy, harmless without the proxy).
2. Lightsail: Caddy block (`bash deploy/from-mac.sh`), then `curl https://jarvis.nickolaspearson.com/api/glance`
   → 401 proves the path.
3. App build with home/away, installed to iPhone + both iPads. Keep the Tailscale fallback for a week, then
   decide whether to turn Tailscale off on the phone.

---

## Part 2: The money advisor (Mooney linked to JARVIS)

**Decided:** link, don't merge. Mooney stays its own app (offline-first, decoy PIN, its own versioning). The
Mooney backend on the NAS becomes the money hub that both apps talk to. Spending comes from **Plaid
transactions**. The advisor talks four ways: **weekly review, same-day nudges, monthly budget check, big-purchase
heads-up**.

### What exists (verified 2026-11-23)
- Mooney's data (buckets, income, bills, subscriptions, cards) lives only on the phone, as one JSON blob in
  `UserDefaults`. The decoy PIN swaps in a separate, made-up state.
- `mooney/Backend` (Node/Express, Docker `mooney-plaid-backend` on the NAS, :4001, running) holds **one** Plaid
  item and **one** account id (`data/item.json`, access token AES-GCM encrypted), asked for
  `Products.Auth` only, and serves `/link_token`, `/exchange_public_token`, `/balance`, `/disconnect`.
  **It has no authentication**: its only protection is being reachable only on the tailnet.
- JARVIS already runs a model-driven advisor for investing (`brain/investing.py`) and keeps private,
  separate stores (Donna). New model work goes in a subagent with an `agent_roster` entry and
  `usage.record_response`.

### Backend changes (mooney repo)
1. **Auth:** every route needs `Authorization: Bearer <token>`. Two tokens in `.env`: `MOONEY_APP_TOKEN` (the
   app) and `MOONEY_JARVIS_TOKEN` (read transactions/budget, write advisor notes and annotations; cannot link,
   disconnect or see the Plaid token).
2. **Several items/accounts:** `data/items.json` = list of `{itemId, institution, accessToken (enc), accounts[],
   cursor}`; migrate the old `item.json` on start. `/link_token` takes `mode: new | update` (update re-consents an
   existing item).
3. **Transactions:** Link asks for `transactions` (plus balance). The existing MACU item needs a re-consent through
   update mode before Plaid will return its transactions. A sync loop calls `/transactions/sync` with the stored
   cursor every `SYNC_INTERVAL_MIN` (60) and on demand, and stores results in SQLite (`data/money.db`): id,
   account, date, authorized date, amount, merchant, Plaid personal-finance category, pending, removed.
   Webhooks would need a public URL; polling first.
4. **New endpoints:** `GET /accounts`, `GET /transactions?since=&account=`, `POST /sync`,
   `PUT/GET /budget` (the app's budget snapshot), `GET/POST /advisor/notes`,
   `POST /transactions/:id/annotation` (`{kind: reimbursed|excused|split|mistake, amount?, note}`).
   Annotations live here so both apps show the same story.

### Mooney app changes
1. Sends the token; Settings > Bank Connection gets "Add another bank/card" and "Allow spending access"
   (update mode for the existing link).
2. **Budget snapshot:** on every change to buckets, bills, subscriptions, cards or pay schedule, the app
   `PUT /budget` a trimmed copy (no PINs, no notes). **Never in decoy mode**, and nothing is sent while decoy is
   active.
3. **Advisor card** on the Dashboard: JARVIS's latest note and the week's flagged charges; tap a charge to
   annotate ("golf, got $40 back"). Hidden in decoy mode.
4. Version: a minor bump (Y) per Mooney's rules.

### JARVIS side
- `integrations/mooney.py`: client for the backend (`MOONEY_URL` = NAS tailnet/LAN :4001, `MOONEY_JARVIS_TOKEN`).
- `brain/finance_advisor.py` (subagent, roster entry "Money advisor", usage recorded). State in
  `memory/finance.json`: budgets (from the snapshot plus anything Nick sets), learned excuse rules
  ("golf is usually split"), and a log of nudges so it never repeats itself.
- **Deterministic triggers, model only for the words** (cheap and predictable):
  - *Big-purchase heads-up:* a charge ≥ `FINANCE_BIG_PURCHASE` lands → one push, right away.
  - *Same-day nudge:* a category runs ahead of its monthly budget pace, or one merchant shows up ≥ N times
    in 7 days. At most one nudge a day, never during quiet hours.
  - *Weekly review:* `FINANCE_WEEKLY_AT` (e.g. Sunday 7 pm): where it went, vs budget, what looked off, excuses
    noted.
  - *Monthly check:* on the 1st, last month vs Mooney's buckets/bills, plus one suggestion for next month.
  - Delivered as notices through the runtime (inbox + passive push, own `finance` thread), not alerts.
- **Arguing back** (new tool `explain_transaction`): "that was golf, I got paid back" → finds the charge (merchant,
  date, amount, fuzzy), writes the annotation to the backend, and nets out a reimbursement. Optional
  "remember that golf is usually split" becomes an excuse rule. He keeps his dry wit: an excuse is accepted, but
  the third "I was hungry" in a week gets noticed ("McDonald's again, sir. Hunger appears to be a recurring
  condition.").
- Other tools: `spending_summary(period, category?)`, `set_budget(category, monthly)`, `finance_settings`.
- **Privacy:**
  - Money tools answer only Nick: the phone, the website, or voice-ID-verified. A guest in the room asking "how
    much did Nick spend" gets nothing, and the TV gets nothing.
  - Finance facts stay out of profile memory (`memory_extractor` skips spending details).
  - Nothing financial goes to the event bus SSE that the TV reads.

### Rollout
1. Backend auth + multi-item + transactions (sandbox first), then deploy on the NAS.
2. Mooney app build (token, spending access, budget snapshot, advisor card); re-consent the MACU link.
3. JARVIS: client + tools + advisor, with triggers in dry-run (logged, not pushed) for a week; Nick reviews the
   log, tunes the thresholds, then pushes go live.

---

## Open questions (Nick)
1. **Plaid environment:** is the backend on `production` today (is the live MACU balance real)? Transactions in
   production needs Plaid to enable the product on the account and is billed per connected account; check the
   Plaid dashboard.
2. **Which accounts:** MACU checking, plus which credit cards (and their banks)?
3. **Budgets:** use Mooney's buckets/bills as the budget, or set monthly limits per category (food, golf, gas,
   fun...)? Or both?
4. **Numbers:** the big-purchase amount, quiet hours, and the weekly review's day and time.
5. **Tone:** how blunt? (Default: dry, a little teasing, never preachy, one line.)
6. **Away access:** after a week, turn Tailscale off on the phone, or keep it as the fallback?
