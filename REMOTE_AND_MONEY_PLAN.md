# Remote access over nickolaspearson.com + the money advisor

Planned 2026-11-23 with Nick. **Built 2026-11-23 (companion 6.124, Mooney 1.14.0). Away access deployed the same
evening; the money side is deployed too** (see "Deployment status"); the runbook is "Deploy (Nick)" at the end. Decisions he made are marked **Decided**; the
questions still open are in "Open questions".

---

## Part 1: The companion reaches JARVIS through `jarvis.nickolaspearson.com`

**Nick's goal, in his words:** "just to not need Tailscale for JARVIS. That's it." Without opening the Surface to
the internet, and without being restricted by Cloudflare.

**Decided (final, 2026-11-23):** **Lightsail reverse proxy + device certificate (mTLS)**, with auto home/away in
the app.

```
Phone ──HTTPS + device cert──▶ Lightsail Caddy (public, Nick's own server) ──tailnet──▶ Surface Core :8000 (private)
```

- On home Wi-Fi the app uses the Surface's LAN address. Anywhere else it uses
  `https://jarvis.nickolaspearson.com`. The phone and iPads no longer need Tailscale for JARVIS. Tailscale stays
  only on the Surface, Lightsail and the NAS.
- **The Surface never listens to the internet.** Only Lightsail is public, as it already is for the website.
- **No third party in the middle.** TLS ends on Nick's own Lightsail server, so Donna, arr requests and the camera
  all work without restrictions.
- **Only Nick's devices get through.** Caddy requires a client certificate that only his iPhone and iPads hold,
  and refuses everything else before Core sees it. The JARVIS token is a second lock behind it.

### Rejected options and why
- **Cloudflare Tunnel:** fastest away (estimated 40–60 ms), but Cloudflare decrypts traffic in the middle, so
  Donna would have had to be blocked on that path, plus Cloudflare's terms on video (the camera). Nick didn't want
  restrictions or a third party seeing his traffic.
- **Port-forwarding the Surface:** never. Nick doesn't want the Surface open to the internet.
- **Token-only proxy (no device cert):** the token would be the only lock on Core from the whole internet (Schwab
  trade approvals, room control, camera). Replaced by the device certificate.

### Speed (measured from school, 2026-11-23)
| Route | Round trip |
|---|---|
| Phone → Surface over Tailscale (today) | ~45 ms (DERP Denver) |
| School → Lightsail (Ohio) | ~60 ms |
| Lightsail → Surface, before the firewall fix | 84 ms (DERP Toronto relay) |
| Lightsail → Surface, **after** the firewall fix | **~55 ms, direct** (`direct 3.128.92.104:41641`) |
| **Domain route total, away** | **~115 ms** |

About a tenth of a second per tap away from home, vs about 0.05 s today. Chat feels the same (the model takes
seconds). Home is unchanged (LAN). **Done 2026-11-23:** Nick added the Lightsail firewall rule **UDP 41641, any
IPv4**, so Lightsail and the Surface connect directly. Keep it; the website's chat path uses it too.

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
       tls {
           client_auth {
               mode require_and_verify
               trust_pool file /etc/caddy/jarvis-devices-ca.pem   # Nick's private device CA
           }
       }
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
   No Caddy rate-limit (needs a plugin); the lockout lives in Core (item 3). The `client_auth` block applies only
   to this site; the main site, waste, coopa, chores and schwab are untouched.
1b. **Device certificates (mTLS).**
   - A small private CA made once on the Mac (`ops/remote/make-device-cert.sh`): CA key kept offline (Mac
     Keychain or a file outside the repo, never committed). The CA *certificate* goes to Lightsail
     (`/etc/caddy/jarvis-devices-ca.pem`).
   - One client certificate per device (`iphone`, `ipad-air`, `ipad-mini`, `mac`), each a `.p12` with a
     passphrase, valid 2 years. To revoke a lost device, re-issue the CA or move Caddy to a list of allowed
     cert fingerprints (decide at build time; fingerprint list is simpler).
   - **iOS apps can't use certificates installed as profiles** (those are for Safari/Mail), so the app imports
     the `.p12` itself: Settings → "Away access" → "Import device certificate" (Files/AirDrop + passphrase) →
     stored in the app's Keychain (`SecPKCS12Import`), and every `URLSession` (data, SSE, voice websocket)
     answers `NSURLAuthenticationMethodClientCertificate` challenges with it. One shared session delegate.
   - JARVIS for Mac does the same.
   - Without the cert the app still works at home (LAN) and over Tailscale; Settings says "Away access: not set up".
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
Two locks in front of Core: the device certificate at Caddy (strangers never reach Core) and the JARVIS token. Plus
HTTPS, the failed-token lockout, the guessing alert, and the reachability monitor. The Surface stays off the
internet. **Lightsail becomes the front door, so it must be kept patched**: the Lightsail console showed
"Security update recommended" (CVE-2026-98432, Linux blueprint) on 2026-11-23. Follow-up: the deferred **Face ID
gate** for camera and trade approvals in the app (memory: camera-faceid-backlog).

### Rollout (each step only when Nick says go)
0. **Nick, on Lightsail:** `sudo apt update && sudo apt upgrade -y` (and reboot if asked). Firewall rule UDP 41641
   already done.
1. Core: proxy headers + lockout + monitor (deploy, harmless without the proxy).
2. Make the CA and device certs on the Mac.
3. Lightsail: Caddy block with `client_auth` (`bash deploy/from-mac.sh`). Prove it: `curl` **without** a cert →
   TLS refused; `curl --cert` with the Mac's cert → `/api/glance` 401 (no token) → with token 200.
4. App build with home/away + certificate import, installed to iPhone + both iPads (and the Mac app). Import each
   device's cert. Test away on cellular with Tailscale **off**.
5. Keep Tailscale installed on the phone as a backup for a week, then Nick decides whether to remove it.

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

## Nick's answers (2026-11-23, later the same day)

### Remote access
Settled: see Part 1 (Lightsail + device certificate; firewall fix done; measurements there).

### Money
- **Plaid is production** (the MACU balance in Mooney is real). Pay-as-you-go rates show only in the Plaid
  dashboard. The one unverified third-party figure is ~$0.25/account/month for Transactions and ~$0.05 per balance
  check. Mooney's live balance uses the per-request balance call (`accountsBalanceGet`), which may already cost more
  than Transactions would. Once Transactions syncs, take the balance from the sync and keep paid checks to an
  explicit "refresh now".
- **Accounts:** MACU checking (Plaid), Chase (Plaid; it's the Amazon card). Apple Card: **Plaid can't** read it
  normally. FinanceKit needs an Apple-approved managed entitlement and an organization developer account. Plan:
  an **iOS Shortcuts "Transaction" automation**, which fires on every Apple Pay payment with merchant, amount and
  card, and POSTs to Core (new `POST /api/money/transaction`, `money:write` client token). Monthly Apple Card
  statement export (Wallet) to fill gaps. The Shortcut also catches MACU debit Apple Pay instantly. Delta card:
  skip (unused, saves a Plaid account).
- **Budget model:**
  - *Weekly $125 on MACU debit* = everyday spending. The $125 auto-transfer lands Sunday night/Monday morning, so a
    budget week is **Monday 00:00 – Sunday 23:59**, local time.
  - *Apple Card* = "outside the weekly budget" (weird/bigger buys). Not counted against the $125, watched separately:
    the >$50 heads-up and a monthly total, so it doesn't quietly become the real budget.
  - *Chase* = Amazon. A monthly total, plus the >$50 heads-up.
  - Transfers between Nick's own accounts and card payments are never spending.
- **Big purchase:** anything over **$50**, any card.
- **Weekly review:** **Sunday 12:00**, before the new $125 lands: the week vs $125, top merchants, excuses noted.
- **Quiet hours:** a setting Nick sets in the companion app (a Money section in Settings, stored on Core).
- **Tone:** dry and teasing. **A bad week (over $125) gets a proper scolding**, still in character, never cruel.
  He can argue ("golf, got paid back", "I was hungry"). Reimbursements net out; excuses are recorded and
  considered, but a pattern of the same excuse is called out. JARVIS may concede a good argument.

## Open questions (Nick)
Answered: Plaid environment, accounts, budget, numbers, tone, away-access design (Lightsail + device cert;
firewall fix done). Still open:
1. Plaid's actual Transactions price (Nick: check the Plaid dashboard's billing page).
2. After a week of away access: remove Tailscale from the phone, or keep it as a backup?
3. Quiet hours: Nick sets them in the app once the Money settings exist.

## Build order (nothing deploys without Nick's go)
1. Part 1 Core pieces (proxy headers, lockout, monitor) + tests.
2. Part 1 app: home/away routing + certificate import (iPhone, iPads, Mac app).
3. Mooney backend: auth tokens, multi-item, Transactions sync, new endpoints (sandbox first).
4. Mooney app: token, spending access (update-mode re-consent for MACU, add Chase), budget snapshot, advisor card.
5. JARVIS: `integrations/mooney.py`, Apple Pay Shortcut endpoint, `brain/finance_advisor.py`, tools, Money
   settings (quiet hours) in the companion, triggers in dry-run for a week.
6. Go live: real pushes, weekly review Sunday 12:00.

---

## Built 2026-11-23 (what differs from the plan above)

- **Caddy asks for the certificate instead of requiring it** (`mode verify_if_given`), and aborts any request
  without one, **except `POST /api/money/transaction`**: the Apple Pay Shortcut runs inside iOS Shortcuts,
  which can't present a client certificate, so with strict mTLS it would only work at home. That one endpoint
  takes a write-only `money:write` token (it can record a payment, nothing else, not even read) and the
  failed-token lockout applies. A wrong certificate still fails at the handshake. Tested locally against Caddy
  2.11: no cert → aborted, rogue cert → handshake refused, Nick's cert → through, `../` and `%2F` tricks
  → aborted. **Nick decides:** keep the exception, or deploy with `JARVIS_SHORTCUT_OPEN=0` (strict; the
  Shortcut then only records payments made at home).
- **Revocation:** CA rotation (`make-device-cert.sh ca --rotate`, re-issue, redeploy), no fingerprint list.
- **Core's own certificate** (`device core`, PEM) is what the away-access monitor uses for its round trip.
- **Monthly category budgets (`set_budget`) were folded into `finance_settings`:** Nick's answers replaced them
  with the $125 week + Apple Card/Chase totals. The nudge rules are the weekly pace and repeat merchants.
- **Mooney's backend stays open until `MOONEY_APP_TOKEN` is set**, so it can deploy before the app that sends
  the token (otherwise the current app is locked out).
- **All Mooney changes are uncommitted** in `~/Developer/projects/mooney`: that checkout already had someone's
  uncommitted widget/Dashboard/pbxproj work in the same files, and Mooney's rule is a version bump in every
  commit (pbxproj), so both are left for Nick to review and commit together. Backend: `Backend/server.js`,
  `storage.js`, `db.js`, `sync.js`, `test/`, `Dockerfile` (Node 24), `.env.example`, `package.json`
  (`npm test`: 3 passing). App: `Store/MoneyHub.swift`, `Views/AdvisorCardSection.swift` (new),
  `Store/PlaidClient.swift` (token header), `Store/AppStore.swift` (snapshot on save, never decoy),
  `Views/BankConnectionSettingsView.swift`, `Views/DashboardView.swift` (advisor card), version 1.14.0.

## Deploy (Nick), in order

**Away access**
0. Lightsail: `sudo apt update && sudo apt upgrade -y` (reboot if asked).
1. Core: `deploy/surface.sh`. Then on the Surface add `JARVIS_TRUSTED_PROXIES=100.119.21.113` to `.env` and restart
   (`sudo systemctl restart jarvis-api`). Harmless before the proxy exists.
2. Mac: `ops/remote/make-device-cert.sh ca`, then `device iphone`, `device ipad-air`, `device ipad-mini`,
   `device mac`, `device core` (each `.p12` asks for a passphrase), then `install-ca`.
3. `cd ~/Developer/projects/nickolaspearson-site && bash deploy/from-mac.sh`. Check from the Mac (off Tailscale is
   best): `curl https://jarvis.nickolaspearson.com/health` → connection closed;
   `curl --cert ~/.jarvis-devices/core.crt --key ~/.jarvis-devices/core.key https://jarvis.nickolaspearson.com/health`
   → 401; add `-H "Authorization: Bearer $JARVIS_API_TOKEN"` → 200.
4. Copy `core.crt` + `core.key` to the Surface (`~/jarvis/memory/away/`, `chmod 600` the key), set
   `JARVIS_AWAY_CERT` / `JARVIS_AWAY_KEY`, restart: the away-access monitor starts.
5. Install companion 6.124 on the iPhone and both iPads; `mac/JarvisMac/build.sh --open`. AirDrop each `.p12`,
   import it (Settings > Server > Away access; Mac: Settings). Test on cellular with Tailscale **off**.

**Money**
6. NAS: deploy the Mooney backend (`docker compose up -d --build` in its folder) **without** the tokens first.
   It migrates `item.json` → `items.json` and starts syncing (MACU fails with ADDITIONAL_CONSENT_REQUIRED until 8).
7. Install Mooney 1.14.0. Make two tokens (`openssl rand -hex 32`): paste the first into Mooney > Settings > Bank
   Connection > Backend Token; then put both in the backend's `.env` as `MOONEY_APP_TOKEN` / `MOONEY_JARVIS_TOKEN`
   (+ `SYNC_INTERVAL_MIN=60`) and restart it.
8. Mooney > Bank Connection: **Allow Spending Access** (MACU re-consent), **Add Another Bank or Card** (Chase).
9. Core `.env`: `MOONEY_URL=http://<NAS>:4001`, `MOONEY_JARVIS_TOKEN=...`; restart. The advisor starts in
   **dry run** (logged, not sent): Settings > Money in the companion shows the log; set quiet hours there.
10. Apple Pay Shortcut: on the Surface `python -m app.client_tokens create apple-pay money:write`. Shortcuts >
    Automation > Transaction (all cards) > Run Immediately > *Get Contents of URL*
    `https://jarvis.nickolaspearson.com/api/money/transaction`, POST, header `Authorization: Bearer <token>`,
    JSON body `merchant` = Merchant, `amount` = Amount, `card` = Card (or Wallet pass name).
11. After a week of dry run: review the log, tune, then `finance_settings dry_run=false` (or Settings > Money).

## Deployment status (2026-11-24)

Nick: "keep the shortcut exception, deploy it", and **wait on linking the Apple Card** (step 10 not done).
- **Away access: deployed.** Core at 04e3483+ on the Surface; Lightsail Caddy `# BEGIN JARVIS PROXY` live (backup
  `/etc/caddy/Caddyfile.before-nick-site-20261123224138`); verified no cert → refused, cert without token → 401,
  cert + token → 200 (~0.25 s), Shortcut path without token → 401, nickolaspearson.com still 200. Surface `.env`:
  `JARVIS_TRUSTED_PROXIES`, `JARVIS_AWAY_CERT/KEY` (`memory/away/`); Core sees callers' real IPs; the away-access
  monitor's round trip passes. iPhone 6.124, both iPads 6.125, JARVIS for Mac 2.1 (certificate in place).
- **Money hub: deployed.** NAS backend rebuilt (old files in `~/Backend-backup-20261123`) with
  `MOONEY_APP_TOKEN` (copy in `~/.jarvis-devices/mooney-app-token.txt`), `MOONEY_JARVIS_TOKEN` and
  `SYNC_INTERVAL_MIN=60`; JARVIS's token reads `/accounts` and gets 403 on `/link_token`. Mooney 1.14.0 committed
  (mooney `a05c599`) and installed on the iPhone with the app token; MACU "Allow Spending Access" done
  (sync no longer reports `ADDITIONAL_CONSENT_REQUIRED`). Core's `.env` has `MOONEY_URL`/`MOONEY_JARVIS_TOKEN`;
  the advisor is in **dry run** for its first week.
- Not yet: MACU's history hadn't arrived at 08:10 UTC 11-24 (Plaid's first pull after consent; the hourly sync
  picks it up), so check `/transactions` has rows and the advisor's first pass primed; Mooney's budget snapshot
  uploads on the app's next edit; importing the `.p12` certificates on the iPhone and iPads (AirDrop,
  `~/.jarvis-devices`); Lightsail `apt upgrade` (step 0); Chase link and the Apple Pay Shortcut (step 10) later.
