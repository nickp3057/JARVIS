import Foundation
import SwiftUI

/// State for the Hub screen: independent sections over the dashboard
/// endpoints. Each section loads, fails, and renders on its own — one dead
/// service must never blank the rest of the screen.
@MainActor
final class HubViewModel: ObservableObject {

    /// One section's lifecycle. `serverError` is the endpoint's own `error`
    /// field (service down, request fine); `loadError` means the request to
    /// JARVIS Core itself failed.
    struct Section<Value> {
        var value: Value?
        var serverError: String = ""
        var loadError: String?
        var isLoading = false

        var failureText: String? {
            if let loadError { return loadError }
            return serverError.isEmpty ? nil : serverError
        }
    }

    @Published var services = Section<[ServiceStatus]>()
    @Published var now = Section<NowStatus>()
    @Published var torrents = Section<TorrentsStatus>()
    @Published var media = Section<MediaStatus>()
    /// Temporary 4K films (Media tab's countdown); empty or unconfigured hides the card.
    @Published var temp4k = Section<Temp4kStatus>()
    @Published var busyTemp4k: Set<Int> = []
    @Published var storage = Section<StorageStatus>()
    @Published var entities = Section<[HomeEntity]>()
    @Published var links = Section<[ServiceLink]>()
    /// Recent alerts from the server's alert bus, newest first.
    @Published var alerts = Section<[JarvisAlert]>()
    /// Recurring chores, soonest due first (server-sorted).
    @Published var maintenance = Section<[MaintenanceTask]>()
    /// The shopping list and every saved item (with photos).
    @Published var shopping = Section<ShoppingOverview>()
    /// Schwab, read-only. Polled every tick: Core streams prices, so it moves.
    @Published var portfolio = Section<Portfolio>()
    @Published var scenes = Section<HomeScenes>()
    /// Light sync, for the iPad's movie screen (fetched while it shows, and
    /// whenever the home stream says it changed).
    @Published var lightSync = Section<LightSyncState>()
    /// The movie screen is showing: only then is light sync worth fetching.
    var wantsLightSync = false
    /// Weather and the agenda, for the Mac's Desk (fetched only while it shows).
    @Published var glance = Section<Glance>()
    var wantsGlance = false
    /// `sun.sun` from the last entities read; the iPad's night screen uses it.
    @Published var sun: String?
    /// The scene being run right now (its tile shows a spinner).
    @Published var runningScene: String?
    /// The study brief as JARVIS Core plans it; Settings shows the schedule.
    @Published var studyBrief = Section<StudyBrief>()

    /// entity_id -> in-flight optimistic state, so a toggle feels instant and
    /// reverts visibly if the call fails.
    @Published var pendingEntityStates: [String: Bool] = [:]
    /// Torrent hashes with a control call in flight.
    @Published var busyTorrents: Set<String> = []
    /// Whether the services' home (LAN) addresses answer from here; nil
    /// until probed. Decides between each link's home and Tailscale URL.
    @Published private(set) var onHomeNetwork: Bool?
    /// Pending and recent self-repair proposals (server-staged fixes).
    @Published var repairs = Section<[RepairProposal]>()
    /// Skills Nick asked for that JARVIS doesn't have yet; empty on an older Core.
    @Published var skillRequests = Section<[SkillRequest]>()
    /// Skills JARVIS composed from his own tools: drafts to approve, active, off.
    @Published var learnedSkills = Section<[LearnedSkill]>()
    /// Repair ids with an Approve/Dismiss call in flight.
    @Published private(set) var busyRepairs: Set<String> = []
    /// The alert whose incident report is being gathered (Needs attention tap).
    @Published private(set) var reportingAlertID: Int?
    /// To-dos checked off on Home: hidden at once, deleted on the server.
    @Published private(set) var finishedTodoIDs: Set<String> = []
    /// A Media-tab card ("services", "storage") a Home chip asked to show;
    /// the Media tab scrolls to it and clears this.
    @Published var mediaScrollTarget: String?
    /// Transient, user-visible outcome of the last quick action that failed.
    @Published var actionError: String?
    @Published private(set) var recheckingAlerts = false
    /// Alerts with a Fix it call in flight.
    @Published private(set) var fixingAlertIDs: Set<Int> = []
    /// Alerts swiped away here, hidden at once while Core catches up.
    @Published private(set) var dismissedAlertIDs: Set<Int> = []
    /// Whether JARVIS fixes the basic stuff himself; nil on an older Core.
    @Published private(set) var autopilot: AutopilotStatus?
    @Published private(set) var nowSnapshotIsStale = false

    private let settings: AppSettings
    private let makeClient: () throws -> JarvisAPIClient
    private let homeProbe: (URL) async -> Bool
    private var pollTask: Task<Void, Never>?
    private var homeStreamTask: Task<Void, Never>?
    private let snapshotDefaults: UserDefaults
    private let academicNotifications: AcademicNotificationScheduling?
    private var connectionIdentity: String

    private var nowSnapshotKey: String { "jarvis.hub.nowSnapshot.\(connectionIdentity)" }

    /// Matches the server's TTL cache — polling faster just returns the same
    /// cached payload.
    private let pollInterval: Duration = .seconds(5)
    /// Core's home stream is connected: lights, switches, scenes and the
    /// theme arrive as they change, so nothing needs to poll for them.
    @Published private(set) var homeStreamLive = false
    /// Whether Core wants the iPad's lens on, as the home stream last said;
    /// nil while the stream is down or Core is too old to say, and then
    /// `RoomCameraController` asks for itself every 1.5 s as before.
    @Published private(set) var cameraWanted: Bool?
    /// Visible views that show more than the room (Hub, Media, Home, Desk):
    /// torrents, services, repairs, alerts, portfolio. Room alone needs none.
    private var detailViews = 0

    init(
        settings: AppSettings,
        clientFactory: (() throws -> JarvisAPIClient)? = nil,
        snapshotDefaults: UserDefaults = .standard,
        academicNotifications: AcademicNotificationScheduling? = nil,
        homeProbe: ((URL) async -> Bool)? = nil
    ) {
        self.settings = settings
        self.homeProbe = homeProbe ?? HubViewModel.answers
        self.makeClient = clientFactory ?? { try JarvisAPIClient.make(from: settings) }
        self.snapshotDefaults = snapshotDefaults
        self.academicNotifications = academicNotifications
        self.connectionIdentity = settings.connectionIdentity
        if let data = snapshotDefaults.data(forKey: nowSnapshotKey),
           let cached = try? JSONDecoder().decode(NowStatus.self, from: data) {
            now.value = cached
            nowSnapshotIsStale = true
        }
    }

    private func client() throws -> JarvisAPIClient {
        try makeClient()
    }

    // MARK: - Loading

    /// Full refresh: every section, in parallel. Used on appear and
    /// pull-to-refresh.
    func refreshAll() async {
        guard settings.isConfigured else { return }
        resetConnectionIfNeeded()
        briefSyncedAt = nil  // a full refresh (launch, pull down) re-reads the brief too
        async let a: Void = refreshServices()
        async let b: Void = refreshTorrents()
        async let c: Void = refreshMedia()
        async let d: Void = refreshEntities()
        async let e: Void = refreshLinks()
        async let f: Void = refreshNow()
        async let g: Void = refreshStorage()
        async let h: Void = refreshAlerts()
        async let i: Void = refreshRepairs()
        async let j: Void = refreshMaintenance()
        async let k: Void = refreshPortfolio()
        async let l: Void = refreshScenes()
        async let m: Void = refreshGlance()
        async let n: Void = refreshShopping()
        async let o: Void = refreshAutopilot()
        async let p: Void = refreshSkillRequests()
        async let q: Void = refreshLearnedSkills()
        _ = await (a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p, q)
    }

    func refreshGlance() async {
        guard wantsGlance else { return }
        await load(\.glance) { try await $0.glance() }
    }

    func refreshNow() async {
        resetConnectionIfNeeded()
        // Every 5 s: publish (and cache) only when something changed.
        guard !loadingSections.contains(\HubViewModel.now) else { return }
        let identity = connectionIdentity
        loadingSections.insert(\HubViewModel.now)
        if now.value == nil, !now.isLoading { now.isLoading = true }
        defer {
            loadingSections.remove(\HubViewModel.now)
            if identity == connectionIdentity, now.isLoading { now.isLoading = false }
        }
        do {
            let value = try await client().nowStatus()
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            var next = now
            next.value = value
            next.loadError = nil
            next.serverError = ""
            if !Self.same(now, next) {
                now = next
                if let data = try? JSONEncoder().encode(value) {
                    snapshotDefaults.set(data, forKey: nowSnapshotKey)
                }
            }
            if nowSnapshotIsStale { nowSnapshotIsStale = false }
            if value.calendarError.isEmpty, value.supportsAcademicEvents {
                let countdown = Self.briefSignature(value.academicEvents)
                if briefDue(for: countdown) {
                    await syncStudyBrief(fallbackEvents: value.academicEvents)
                    if studyBrief.loadError == nil, studyBrief.serverError.isEmpty {
                        briefSyncedAt = Date()
                        briefSyncedFor = countdown
                    }
                }
            }
        } catch {
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            let message = error.localizedDescription
            if now.loadError != message { now.loadError = message }
            if nowSnapshotIsStale != (now.value != nil) { nowSnapshotIsStale = now.value != nil }
        }
    }

    func refreshServices() async {
        await load(\.services) { try await $0.servicesStatus().services }
    }

    /// When the brief was last pulled, and for which countdown list. The
    /// brief (12 KB) only changes when the calendar or the day does, so it
    /// is fetched then, on a retry after a failure, or every ten minutes:
    /// not with each 5 s `now` tick, as it was (17,000 downloads a day from
    /// the wall iPad alone).
    private var briefSyncedAt: Date?
    private var briefSyncedFor = ""
    static let briefRefreshInterval: TimeInterval = 600

    static func briefSignature(_ events: [AcademicEvent]) -> String {
        events.map { "\($0.id)|\($0.title)|\($0.startAt ?? 0)|\($0.daysLeft)" }.joined(separator: ";")
    }

    private func briefDue(for countdown: String) -> Bool {
        guard let last = briefSyncedAt, countdown == briefSyncedFor else { return true }
        return Date().timeIntervalSince(last) >= Self.briefRefreshInterval
    }

    /// Pull the composed briefs from JARVIS Core and hand them to the
    /// scheduler. A calendar outage (payload `error`) or a transport failure
    /// leaves the existing queue alone; only a good payload reconciles it.
    /// An older Core without the endpoint (404) falls back to planning on
    /// the phone from the Hub's countdown list.
    func syncStudyBrief(fallbackEvents: [AcademicEvent]) async {
        guard let academicNotifications else { return }
        guard settings.studyAlertsEnabled else {
            await academicNotifications.sync(briefs: [], enabled: false)
            return
        }
        let identity = connectionIdentity
        do {
            let payload = try await client().studyBrief()
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            studyBrief.value = payload
            studyBrief.serverError = payload.error
            studyBrief.loadError = nil
            guard payload.error.isEmpty else { return }
            await academicNotifications.sync(briefs: payload.briefs, enabled: payload.enabled)
        } catch JarvisAPIError.server(let status, _) where status == 404 {
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            studyBrief.loadError = nil
            let briefs = AcademicNotificationScheduler.briefs(
                events: fallbackEvents, now: Date(), calendar: .autoupdatingCurrent, hour: settings.studyAlertHour)
            await academicNotifications.sync(briefs: briefs, enabled: true)
        } catch {
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            studyBrief.loadError = error.localizedDescription
        }
    }

    /// The phone-side switch. Turning it on asks for notification permission
    /// immediately, then re-syncs; turning it off clears the queue.
    func updateNotificationPreferences() async {
        guard let academicNotifications else { return }
        if !settings.studyAlertsEnabled {
            await academicNotifications.sync(briefs: [], enabled: false)
        } else {
            _ = await academicNotifications.requestPermission()
            briefSyncedAt = nil  // the switch just changed: plan again now
            await refreshNow()
        }
    }

    private func resetConnectionIfNeeded() {
        guard connectionIdentity != settings.connectionIdentity else { return }
        connectionIdentity = settings.connectionIdentity
        now = Section(); services = Section(); torrents = Section(); media = Section()
        storage = Section(); entities = Section(); links = Section(); studyBrief = Section()
        alerts = Section(); repairs = Section(); maintenance = Section(); portfolio = Section()
        briefSyncedAt = nil
        loadingSections = []
        nowSnapshotIsStale = false
        if let data = snapshotDefaults.data(forKey: nowSnapshotKey),
           let cached = try? JSONDecoder().decode(NowStatus.self, from: data) {
            now.value = cached
            nowSnapshotIsStale = true
        }
    }

    func refreshTorrents() async {
        await load(\.torrents, serverError: { $0.error }) { try await $0.torrentsStatus() }
    }

    func refreshMedia() async {
        await load(\.media, serverError: { status in
            // Per-service errors render inside the section; only surface a
            // whole-section error when both halves failed.
            status.radarr.error.isEmpty || status.sonarr.error.isEmpty
                ? "" : status.radarr.error
        }) { try await $0.mediaStatus() }
        await refreshTemp4k()
    }

    func refreshTemp4k() async {
        await load(\.temp4k) { try await $0.temp4kStatus() }
    }

    /// The 4K card's Extend (`hours` more) and Remove now.
    func extendTemp4k(_ film: Temp4kFilm, hours: Double) {
        temp4kAction(film) { try await $0.extendTemp4k(film.tmdbID, hours: hours); return "" }
    }

    func removeTemp4k(_ film: Temp4kFilm) {
        temp4kAction(film) { try await $0.removeTemp4k(film.tmdbID) }
    }

    private func temp4kAction(_ film: Temp4kFilm, _ call: @escaping (JarvisAPIClient) async throws -> String) {
        guard !busyTemp4k.contains(film.tmdbID) else { return }
        busyTemp4k.insert(film.tmdbID)
        Task {
            defer { busyTemp4k.remove(film.tmdbID) }
            do {
                let error = try await call(client())
                if !error.isEmpty { actionError = error }
            } catch {
                actionError = error.localizedDescription
            }
            await refreshTemp4k()
        }
    }

    func refreshStorage() async {
        await load(\.storage, serverError: { $0.error }) { try await $0.storageStatus() }
    }

    func refreshEntities() async {
        await load(\.entities, serverError: { $0.error }, transform: { $0.entities }) { [weak self] in
            let result = try await $0.homeEntities()
            self?.sun = result.sun
            return result
        }
    }

    func refreshAlerts() async {
        await load(\.alerts) { try await $0.recentAlerts() }
        // Autopilot may be mid-fix: keep watching until it reports.
        if (alerts.value ?? []).contains(where: { $0.repair?.isWorking == true }) { followRepairs() }
    }

    /// The Needs attention card's Refresh button: what's fixed disappears.
    func recheckAlerts() {
        guard !recheckingAlerts else { return }
        recheckingAlerts = true
        Task {
            defer { recheckingAlerts = false }
            do { try await client().recheckAlerts() } catch { actionError = error.localizedDescription }
            await refreshAlerts()
        }
    }

    /// Fix it on one alert. The fix runs on Core; the alert's `repair`
    /// shows its progress, polled here until it settles.
    func fixAlert(_ alert: JarvisAlert) async -> AlertFixResult? {
        guard !fixingAlertIDs.contains(alert.id) else { return nil }
        fixingAlertIDs.insert(alert.id)
        defer { fixingAlertIDs.remove(alert.id) }
        do {
            let result = try await client().fixAlert(alert)
            await refreshAlerts()
            await refreshRepairs()
            if result.started { followRepairs() }
            return result
        } catch {
            actionError = error.localizedDescription
            return nil
        }
    }

    func dismissAlert(_ alert: JarvisAlert) {
        dismissedAlertIDs.insert(alert.id)
        Task {
            do {
                try await client().dismissAlert(alert)
                await refreshAlerts()
            } catch {
                dismissedAlertIDs.remove(alert.id)
                actionError = error.localizedDescription
            }
        }
    }

    func refreshSkillRequests() async {
        await load(\.skillRequests) { try await $0.skillRequests() }
    }

    func refreshLearnedSkills() async {
        await load(\.learnedSkills) { try await $0.learnedSkills() }
    }

    /// Hand one request to the skill drafter; watch until it answers.
    func draftSkill(for request: SkillRequest) {
        Task {
            do { _ = try await client().draftSkill(requestID: request.id) } catch {
                actionError = error.localizedDescription
            }
            for _ in 0..<40 {
                await refreshSkillRequests()
                await refreshLearnedSkills()
                let busy = (skillRequests.value ?? []).contains { $0.id == request.id && $0.drafting == true }
                if !busy { break }
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    func setSkill(_ skill: LearnedSkill, status: String) {
        Task {
            do { try await client().setSkill(id: skill.id, status: status) } catch {
                actionError = error.localizedDescription
            }
            await refreshLearnedSkills()
            await refreshSkillRequests()
        }
    }

    func closeSkillRequest(_ request: SkillRequest, status: String) {
        skillRequests.value?.removeAll { $0.id == request.id }
        Task {
            do { try await client().closeSkillRequest(id: request.id, status: status) } catch {
                actionError = error.localizedDescription
            }
            await refreshSkillRequests()
        }
    }

    func refreshAutopilot() async {
        autopilot = try? await client().autopilot()
    }

    func setAutopilot(_ enabled: Bool) {
        Task {
            do { autopilot = try await client().setAutopilot(enabled) } catch { actionError = error.localizedDescription }
        }
    }

    private var following: Task<Void, Never>?

    /// While a repair is running, re-read alerts and repairs every few
    /// seconds (for up to ten minutes) so its outcome lands without a pull.
    func followRepairs() {
        guard following == nil else { return }
        following = Task { [weak self] in
            for _ in 0..<120 {
                try? await Task.sleep(for: .seconds(5))
                guard let self, !Task.isCancelled else { return }
                await self.refreshAlerts()
                await self.refreshRepairs()
                let working = (self.alerts.value ?? []).contains { $0.repair?.isWorking == true }
                    || (self.repairs.value ?? []).contains { $0.status == "running" }
                if !working { break }
            }
            self?.following = nil
        }
    }

    /// An older Core without /api/repairs just leaves this empty.
    func refreshRepairs() async {
        await load(\.repairs) { try await $0.repairs() }
    }

    /// Approve (the tap is the approval) or dismiss one staged repair.
    func resolveRepair(_ repair: RepairProposal, approve: Bool) {
        guard !busyRepairs.contains(repair.id) else { return }
        busyRepairs.insert(repair.id)
        Task {
            defer { busyRepairs.remove(repair.id) }
            do {
                let result = try await client().repairAction(id: repair.id, action: approve ? "apply" : "reject")
                if approve, result.status == nil || result.status == "pending" { actionError = result.message }
            } catch {
                actionError = error.localizedDescription
            }
            await refreshRepairs()
        }
    }

    // MARK: - To-dos

    @discardableResult
    func addTodo(_ text: String) async -> Bool {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        do {
            _ = try await client().addTodo(text)
        } catch {
            actionError = error.localizedDescription
            return false
        }
        await refreshNow()
        return true
    }

    /// New wording, typed in place on the list.
    @discardableResult
    func renameTodo(id: String, text: String) async -> Bool {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        do {
            _ = try await client().renameTodo(id: id, text: text)
        } catch {
            actionError = error.localizedDescription
            return false
        }
        await refreshNow()
        return true
    }

    /// Checked means gone: hide it now, delete it on the server, and bring
    /// it back only if that fails.
    func completeTodo(id: String) {
        guard !finishedTodoIDs.contains(id) else { return }
        finishedTodoIDs.insert(id)
        Task {
            do {
                try await client().completeTodo(id: id)
                await refreshNow()
            } catch {
                finishedTodoIDs.remove(id)
                actionError = error.localizedDescription
            }
        }
    }

    // MARK: - Portfolio

    /// An older Core without /api/portfolio just never shows the card.
    func refreshPortfolio() async {
        await load(\.portfolio) { try await $0.portfolio() }
    }

    // MARK: - Light brightness and colour

    /// Brightness and/or colour for one light; re-reads the room after
    /// (`refresh: false` for the in-between steps of a hold-and-drag).
    func setLight(_ entity: HomeEntity, brightness: Int? = nil, rgb: [Int]? = nil, kelvin: Int? = nil,
                  rainbow: Bool = false, refresh: Bool = true) {
        Task {
            do {
                let error = try await client().setLight(entity.entityID, brightness: brightness, rgb: rgb,
                                                        kelvin: kelvin, rainbow: rainbow)
                if !error.isEmpty { actionError = error }
            } catch {
                actionError = error.localizedDescription
            }
            if refresh { await refreshEntities() }
        }
    }

    /// Speed (0 = off) and/or mode for a fan; re-reads the room after.
    func setFan(_ entity: HomeEntity, percentage: Int? = nil, presetMode: String? = nil, refresh: Bool = true) {
        Task {
            do {
                let error = try await client().setFan(entity.entityID, percentage: percentage, presetMode: presetMode)
                if !error.isEmpty { actionError = error }
            } catch {
                actionError = error.localizedDescription
            }
            if refresh { await refreshEntities() }
        }
    }

    /// One step of a hold-and-drag on a light or fan tile: 0 turns it off.
    func setLevel(_ entity: HomeEntity, _ level: Int, final: Bool) {
        if entity.domain == "fan" {
            setFan(entity, percentage: level, refresh: final)
        } else if level <= 0 {
            if final, entity.isOn { toggle(entity, to: false) }
        } else {
            setLight(entity, brightness: level, refresh: final)
        }
    }

    // MARK: - Scenes

    /// An older Core without /api/home/scenes just never shows the picker.
    func refreshScenes() async {
        await load(\.scenes, serverError: { $0.error }) { try await $0.homeScenes() }
    }

    func runScene(_ scene: HomeScene) {
        guard runningScene == nil else { return }
        runningScene = scene.entityID
        Task {
            defer { runningScene = nil }
            do {
                let error = try await client().activateScene(scene.entityID)
                if !error.isEmpty { actionError = error }
            } catch {
                actionError = error.localizedDescription
            }
            await refreshScenes()
            await refreshEntities()
        }
    }

    /// Exit Movie (the nightstand's movie remote): the room as it was before.
    func exitMovie() {
        guard runningScene == nil else { return }
        runningScene = "scene.movie"
        Task {
            defer { runningScene = nil }
            do {
                let error = try await client().exitMovieScene()
                if !error.isEmpty { actionError = error }
            } catch {
                actionError = error.localizedDescription
            }
            await refreshScenes()
            await refreshEntities()
        }
    }

    // MARK: - Light sync

    private var lastLightSyncRevision: Int?

    func refreshLightSync() async {
        await load(\.lightSync) { try await $0.lightSync() }
    }

    private func lightSyncChanged(_ revision: Int?) {
        guard let revision, revision != lastLightSyncRevision else { return }
        lastLightSyncRevision = revision
        guard wantsLightSync else { return }
        Task { await refreshLightSync() }
    }

    /// Start / stop, intensity, the auto switch, a timing nudge or reset, or
    /// calibration on / Done; Core's answer replaces the state.
    func setLightSync(action: String? = nil, intensity: String? = nil, auto: Bool? = nil,
                      nudgeMs: Int? = nil, resetOffset: Bool? = nil, calibrate: Bool? = nil,
                      lampNudgeMs: Int? = nil) {
        Task {
            do {
                let state = try await client().setLightSync(action: action, intensity: intensity, auto: auto,
                                                            nudgeMs: nudgeMs, resetOffset: resetOffset,
                                                            calibrate: calibrate, lampNudgeMs: lampNudgeMs)
                lightSync.value = state
                if let message = state.message, !message.isEmpty, action == "start", !state.running {
                    actionError = message
                }
                if let message = state.message, !message.isEmpty, calibrate == true, state.calibrating == nil {
                    actionError = message  // why calibration couldn't start
                }
            } catch {
                actionError = "Light sync didn't answer: \(error.localizedDescription)"
            }
        }
    }

    func buildLightSync(ratingKey: String) {
        Task {
            do {
                let state = try await client().buildLightSync(ratingKey: ratingKey)
                lightSync.value = state
                if state.building == nil, let message = state.message, !message.isEmpty { actionError = message }
            } catch {
                actionError = "Light sync didn't answer: \(error.localizedDescription)"
            }
        }
    }

    // MARK: - Shopping list

    /// An older Core without /api/shopping leaves this nil (the tile hides).
    func refreshShopping() async {
        await load(\.shopping) { try await $0.shopping() }
    }

    /// Replace one item in both the list and the catalog, in place.
    private func applyShopping(_ item: ShoppingItem) {
        guard var overview = shopping.value else { return }
        if let i = overview.catalog.firstIndex(where: { $0.id == item.id }) { overview.catalog[i] = item }
        else { overview.catalog.insert(item, at: 0) }
        if item.onList {
            if let i = overview.list.firstIndex(where: { $0.id == item.id }) { overview.list[i] = item }
            else { overview.list.append(item) }
        } else {
            overview.list.removeAll { $0.id == item.id }
        }
        shopping.value = overview
    }

    private func shoppingAction(_ optimistic: ShoppingItem? = nil,
                                _ work: (JarvisAPIClient) async throws -> ShoppingItem?) async -> ShoppingItem? {
        let before = shopping.value
        if let optimistic { applyShopping(optimistic) }
        do {
            let item = try await work(client())
            if let item { applyShopping(item) }
            return item
        } catch {
            if optimistic != nil { shopping.value = before }
            actionError = error.localizedDescription
            return nil
        }
    }

    /// A saved item onto the list (tapped from the suggestions).
    @discardableResult
    func listShopping(_ item: ShoppingItem, quantity: Int? = nil) async -> ShoppingItem? {
        var shown = item
        shown.onList = true
        if let quantity { shown.quantity = quantity }
        return await shoppingAction(shown) { try await $0.listShoppingItem(id: item.id, quantity: quantity) }
    }

    /// Ticked off: gone from the list at once, kept in the catalog.
    @discardableResult
    func boughtShopping(_ item: ShoppingItem) async -> ShoppingItem? {
        var shown = item
        shown.onList = false
        shown.timesBought += 1
        return await shoppingAction(shown) { try await $0.boughtShoppingItem(id: item.id) }
    }

    @discardableResult
    func undoBoughtShopping(_ item: ShoppingItem) async -> ShoppingItem? {
        var shown = item
        shown.onList = true
        return await shoppingAction(shown) { try await $0.undoBoughtShoppingItem(id: item.id) }
    }

    /// Off the list without counting a purchase.
    @discardableResult
    func unlistShopping(_ item: ShoppingItem) async -> ShoppingItem? {
        var shown = item
        shown.onList = false
        return await shoppingAction(shown) { try await $0.unlistShoppingItem(id: item.id) }
    }

    /// New item (and its photo), or an edit of a saved one. Returns the
    /// saved item, or nil (with `actionError` set) so the editor stays open.
    func saveShopping(_ draft: ShoppingItemDraft, editing item: ShoppingItem?, photo: ShoppingPhotoChange) async -> ShoppingItem? {
        let saved = await shoppingAction { client in
            var saved: ShoppingItem
            if let item { saved = try await client.updateShoppingItem(id: item.id, draft) }
            else { saved = try await client.createShoppingItem(draft) }
            switch photo {
            case .keep: break
            case .remove: saved = try await client.clearShoppingPhoto(id: saved.id)
            case .replace(let jpeg): saved = try await client.putShoppingPhoto(id: saved.id, jpeg: jpeg)
            }
            return saved
        }
        if let saved, let item, saved.onList != draft.onList {
            return draft.onList ? await listShopping(saved) : await unlistShopping(saved)
        }
        return saved
    }

    /// A quick in-place edit (rename, move to another store), shown at once.
    @discardableResult
    func updateShopping(_ item: ShoppingItem, _ change: (inout ShoppingItemDraft) -> Void) async -> ShoppingItem? {
        var draft = ShoppingItemDraft(item)
        change(&draft)
        var shown = item
        shown.name = draft.name
        shown.note = draft.note
        shown.store = draft.store
        shown.quantity = draft.quantity
        return await shoppingAction(shown) { try await $0.updateShoppingItem(id: item.id, draft) }
    }

    @discardableResult
    func deleteShopping(_ item: ShoppingItem) async -> Bool {
        let before = shopping.value
        if var overview = shopping.value {
            overview.list.removeAll { $0.id == item.id }
            overview.catalog.removeAll { $0.id == item.id }
            shopping.value = overview
        }
        do {
            try await client().deleteShoppingItem(id: item.id)
            return true
        } catch {
            shopping.value = before
            actionError = error.localizedDescription
            return false
        }
    }

    func shoppingPhoto(_ item: ShoppingItem) async -> Data? {
        try? await client().shoppingPhoto(id: item.id, version: item.photoVersion)
    }

    // MARK: - Maintenance

    /// An older Core without /api/maintenance just never shows the card.
    func refreshMaintenance() async {
        await load(\.maintenance) { try await $0.maintenanceTasks() }
    }

    /// Log a completion. Returns false (and surfaces why) on failure so a
    /// sheet can stay open with the note intact.
    @discardableResult
    func completeMaintenance(_ task: MaintenanceTask, note: String, on day: Date?) async -> Bool {
        await maintenanceAction {
            _ = try await $0.completeMaintenanceTask(
                id: task.id, note: note, completedOn: day.map(Self.dayString))
        }
    }

    @discardableResult
    func saveMaintenance(_ draft: MaintenanceTaskDraft, editing task: MaintenanceTask?) async -> Bool {
        await maintenanceAction { client in
            if let task { _ = try await client.updateMaintenanceTask(id: task.id, draft) }
            else { _ = try await client.createMaintenanceTask(draft) }
        }
    }

    @discardableResult
    func deleteMaintenance(_ task: MaintenanceTask) async -> Bool {
        await maintenanceAction { try await $0.deleteMaintenanceTask(id: task.id) }
    }

    @discardableResult
    func deleteMaintenanceEntry(_ entry: MaintenanceLogEntry, of task: MaintenanceTask) async -> Bool {
        await maintenanceAction { try await $0.deleteMaintenanceEntry(taskID: task.id, entryID: entry.id) }
    }

    func maintenanceHistory(for task: MaintenanceTask) async throws -> [MaintenanceLogEntry] {
        try await client().maintenanceHistory(id: task.id)
    }

    /// Any change also moves the Now feed, which lists due tasks.
    private func maintenanceAction(_ work: (JarvisAPIClient) async throws -> Void) async -> Bool {
        do {
            try await work(client())
        } catch {
            actionError = error.localizedDescription
            return false
        }
        async let tasks: Void = refreshMaintenance()
        async let now: Void = refreshNow()
        _ = await (tasks, now)
        return true
    }

    nonisolated static func dayString(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    func refreshLinks() async {
        await load(\.links) { try await $0.serviceLinks().links }
        await probeHomeNetwork()
    }

    /// One quick request to a link's home address: any HTTP answer means the
    /// phone is on the home network (or a route to it). Runs with each full
    /// refresh (appear, pull-to-refresh), not on the 5 s poll.
    func probeHomeNetwork() async {
        guard let link = links.value?.first(where: { !$0.remoteURL.isEmpty }),
              let url = URL(string: link.url) else { return }
        onHomeNetwork = await homeProbe(url)
    }

    nonisolated static func answers(_ url: URL) async -> Bool {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 1.5
        config.waitsForConnectivity = false
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: url)
        request.httpMethod = "HEAD"
        return (try? await session.data(for: request)) != nil
    }

    /// Fetch one section, keeping stale data on screen when a refresh fails.
    /// Sections with a fetch in flight. Kept off the published Sections, so a
    /// background refresh doesn't redraw every card twice just to say so.
    private var loadingSections: Set<AnyKeyPath> = []

    private func load<Value>(
        _ keyPath: ReferenceWritableKeyPath<HubViewModel, Section<Value>>,
        serverError: (Value) -> String = { _ in "" },
        fetch: (JarvisAPIClient) async throws -> Value
    ) async {
        await load(keyPath, serverError: serverError, transform: { $0 }, fetch: fetch)
    }

    /// Fetches one section and publishes it once, and only if it changed. The
    /// poll refreshes up to seven sections every 5 s; each used to publish five
    /// times (loading on, value, error, error cleared, loading off), redrawing
    /// the visible tab ~35 times a tick even when nothing had changed.
    /// `isLoading` is only published for a section's first load (the
    /// placeholder row); later refreshes are silent until they have news.
    private func load<Raw, Value>(
        _ keyPath: ReferenceWritableKeyPath<HubViewModel, Section<Value>>,
        serverError: (Raw) -> String,
        transform: (Raw) -> Value,
        fetch: (JarvisAPIClient) async throws -> Raw
    ) async {
        resetConnectionIfNeeded()
        guard !loadingSections.contains(keyPath) else { return }
        let identity = connectionIdentity
        loadingSections.insert(keyPath)
        if self[keyPath: keyPath].value == nil, !self[keyPath: keyPath].isLoading {
            self[keyPath: keyPath].isLoading = true
        }
        defer {
            loadingSections.remove(keyPath)
            if identity == connectionIdentity, self[keyPath: keyPath].isLoading {
                self[keyPath: keyPath].isLoading = false
            }
        }
        var next: Section<Value>
        do {
            let raw = try await fetch(client())
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            next = self[keyPath: keyPath]
            next.value = transform(raw)
            next.serverError = serverError(raw)
            next.loadError = nil
        } catch {
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            next = self[keyPath: keyPath]
            next.loadError = error.localizedDescription
        }
        if !Self.same(self[keyPath: keyPath], next) { self[keyPath: keyPath] = next }
    }

    /// Whether publishing `next` would change nothing on screen. Values that
    /// aren't Equatable always count as changed.
    nonisolated static func same<Value>(_ old: Section<Value>, _ next: Section<Value>) -> Bool {
        guard old.isLoading == next.isLoading, old.serverError == next.serverError,
              old.loadError == next.loadError else { return false }
        switch (old.value, next.value) {
        case (nil, nil): return true
        case let (a?, b?):
            guard let a = a as? any Equatable else { return false }
            return a.isEqual(to: b)
        default: return false
        }
    }

    // MARK: - Foreground polling

    /// US market hours (with a few minutes either side), New York time:
    /// Core streams quotes then, so the portfolio is worth a 5 s refresh.
    static func marketHours(_ date: Date = .now) -> Bool {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York") ?? .current
        let parts = calendar.dateComponents([.weekday, .hour, .minute], from: date)
        guard let weekday = parts.weekday, (2...6).contains(weekday),
              let hour = parts.hour, let minute = parts.minute else { return false }
        let minutes = hour * 60 + minute
        return minutes >= 9 * 60 + 25 && minutes <= 16 * 60 + 5
    }

    /// Auto-refresh the fast-moving sections (torrents, services) while the
    /// Hub is visible and the app is foregrounded. Never faster than the
    /// server's 5s TTL; stops entirely when the view goes away. Lights and
    /// scenes come from the home stream while it's up (a 60 s safety
    /// refetch), the Hub-only sections only while a Hub view is showing, and
    /// the portfolio every 5 s in market hours, else once a minute.
    func startPolling() {
        guard pollTask == nil, settings.isConfigured else { return }
        startHomeStream()
        pollTask = Task { [weak self] in
            await self?.refreshAll()
            var tick = 0
            while !Task.isCancelled {
                try? await Task.sleep(for: self?.pollInterval ?? .seconds(5))
                guard !Task.isCancelled, let self else { return }
                tick += 1
                let details = self.detailViews > 0
                let homeDue = !self.homeStreamLive || tick.isMultiple(of: 12)
                let portfolioDue = details && (Self.marketHours() || tick.isMultiple(of: 12))
                async let now: Void = self.refreshNow()
                async let entities: Void = homeDue ? self.refreshEntities() : ()
                async let torrents: Void = details ? self.refreshTorrents() : ()
                async let services: Void = details ? self.refreshServices() : ()
                async let alerts: Void = details ? self.refreshAlerts() : ()
                async let repairs: Void = details ? self.refreshRepairs() : ()
                async let portfolio: Void = portfolioDue ? self.refreshPortfolio() : ()
                _ = await (torrents, services, entities, now, alerts, repairs, portfolio)
                if tick.isMultiple(of: 3) {
                    async let media: Void = details ? self.refreshMedia() : ()
                    // New scenes made in HA show up (the stream covers runs).
                    async let scenes: Void = homeDue || tick.isMultiple(of: 6) ? self.refreshScenes() : ()
                    _ = await (media, scenes)
                }
                if details, tick.isMultiple(of: 6) {
                    async let storage: Void = self.refreshStorage()
                    async let maintenance: Void = self.refreshMaintenance()
                    async let glance: Void = self.refreshGlance()
                    async let shopping: Void = self.refreshShopping()
                    _ = await (storage, maintenance, glance, shopping)
                }
            }
        }
    }

    /// Core's `home` events (`GET /api/home/stream`): a light, switch or
    /// scene changed anywhere (phone, iPad, voice, wall switch, HA), so the
    /// lights and scenes refetch at once rather than on the next poll. Events
    /// in a burst share one refetch. Reconnects every few seconds if dropped;
    /// an older Core without the stream just leaves the poll doing the work.
    private func startHomeStream() {
        guard homeStreamTask == nil else { return }
        homeStreamTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let client = try? self?.client() else { return }
                self?.lastHomeSeq = nil  // a reconnect's first event may hide a missed change
                try? await client.streamHome { [weak self] seq, theme, look, camera, lightsync in
                    self?.homeChanged(seq, theme: theme, look: look, camera: camera)
                    self?.lightSyncChanged(lightsync)
                }
                self?.homeStreamLive = false
                self?.cameraWanted = nil
                guard !Task.isCancelled else { return }
                await self?.refreshEntities()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    private var lastHomeSeq: Int?
    private var homeRefetch: Task<Void, Never>?

    private var lastTheme: String?
    private var lastLook: Double?

    private func homeChanged(_ seq: Int, theme: String?, look: Double?, camera: Bool?) {
        homeStreamLive = true
        if cameraWanted != camera { cameraWanted = camera }
        let newTheme = theme.map { $0 != JarvisPalette.current.rawValue && $0 != lastTheme } ?? false
        // An Experimenting switch flipped elsewhere moves `look` without changing the theme.
        let newLook = look != nil && lastLook != nil && look != lastLook
        if newTheme || newLook {
            // Barn Door, a pick on the TV or another device: follow it now, not at a poll.
            Task { [settings] in await ThemeSync.pull(settings) }
        }
        lastTheme = theme
        lastLook = look
        defer { lastHomeSeq = seq }
        guard let last = lastHomeSeq, last != seq else { return }  // the first event is just "connected"
        homeRefetch?.cancel()
        homeRefetch = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(120))
            guard !Task.isCancelled, let self else { return }
            async let entities: Void = self.refreshEntities()
            async let scenes: Void = self.refreshScenes()
            _ = await (entities, scenes)
        }
    }

    /// Visible Hub views (Home and Media tabs share this model). SwiftUI may
    /// show the next tab before hiding the last, so polling follows a count
    /// rather than each view's appear/disappear.
    private var visibleViews = 0

    /// `details`: the view shows Hub sections (torrents, portfolio...); Room doesn't.
    func retainPolling(start: Bool, details: Bool = true) {
        visibleViews += 1
        if details { detailViews += 1 }
        if start { startPolling() }
    }

    func releasePolling(details: Bool = true) {
        visibleViews = max(0, visibleViews - 1)
        if details { detailViews = max(0, detailViews - 1) }
        if visibleViews == 0 { stopPolling() }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
        homeStreamTask?.cancel()
        homeStreamTask = nil
        homeStreamLive = false
        cameraWanted = nil
        nowSnapshotIsStale = now.value != nil
    }

    // MARK: - Incident reports

    /// The chat prompt for a tapped alert: the server's incident report when
    /// it can build one, otherwise the alert text itself, so a tap always
    /// lands in Chat (older Core, restarted server, NAS unreachable).
    func incidentPrompt(for alert: JarvisAlert) async -> String? {
        guard reportingAlertID == nil else { return nil }
        reportingAlertID = alert.id
        defer { reportingAlertID = nil }
        if let report = try? await client().incidentReport(for: alert) {
            return report.prompt
        }
        return Self.fallbackPrompt(for: alert)
    }

    static func fallbackPrompt(for alert: JarvisAlert) -> String {
        let when = alert.date.formatted(date: .abbreviated, time: .shortened)
        var lines = ["Alert from \(when): \(alert.title)"]
        if !alert.message.isEmpty { lines.append(alert.message) }
        lines.append("What's wrong, what's the likely cause, and how do we fix it?")
        return lines.joined(separator: "\n")
    }

    // MARK: - Quick actions

    /// Optimistically flip an entity, then reconcile with the state the
    /// server reports back (Core waits up to two seconds for Home Assistant
    /// to settle) and once more a few seconds later. Any failure reverts the
    /// toggle and surfaces why.
    func toggle(_ entity: HomeEntity, to turnOn: Bool) {
        guard pendingEntityStates[entity.entityID] == nil else { return }
        pendingEntityStates[entity.entityID] = turnOn
        Task {
            do {
                let result = try await client().homeControl(
                    entityID: entity.entityID, turnOn: turnOn
                )
                if result.error.isEmpty {
                    applyEntityState(entity.entityID, state: result.state ?? (turnOn ? "on" : "off"))
                }
                pendingEntityStates[entity.entityID] = nil
                guard result.error.isEmpty else {
                    actionError = result.error
                    return
                }
            } catch {
                pendingEntityStates[entity.entityID] = nil
                actionError = error.localizedDescription
                return
            }
            // Some devices report in after Core answers; re-read once they have.
            try? await Task.sleep(for: .seconds(3))
            await refreshEntities()
        }
    }

    private func applyEntityState(_ entityID: String, state: String) {
        guard var current = entities.value,
              let index = current.firstIndex(where: { $0.entityID == entityID })
        else { return }
        current[index].state = state
        entities.value = current
    }

    func torrentAction(_ torrent: TorrentTransfer, action: String) {
        guard let hash = torrent.hash, !busyTorrents.contains(hash) else { return }
        busyTorrents.insert(hash)
        Task {
            defer { busyTorrents.remove(hash) }
            do {
                let result = try await client().torrentControl(hash: hash, action: action)
                if !result.error.isEmpty {
                    actionError = result.error
                }
            } catch {
                actionError = error.localizedDescription
            }
            await refreshTorrents()
        }
    }
}

private extension Equatable {
    /// `==` against a value of unknown type (HubViewModel.same).
    func isEqual(to other: Any) -> Bool { (other as? Self) == self }
}
