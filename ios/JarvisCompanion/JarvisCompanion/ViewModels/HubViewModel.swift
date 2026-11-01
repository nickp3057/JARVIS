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
    @Published var storage = Section<StorageStatus>()
    @Published var entities = Section<[HomeEntity]>()
    @Published var links = Section<[ServiceLink]>()
    /// Recent alerts from the server's alert bus, newest first.
    @Published var alerts = Section<[JarvisAlert]>()
    /// Recurring chores, soonest due first (server-sorted).
    @Published var maintenance = Section<[MaintenanceTask]>()
    /// Schwab, read-only. Polled every tick: Core streams prices, so it moves.
    @Published var portfolio = Section<Portfolio>()
    @Published var scenes = Section<HomeScenes>()
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
        _ = await (a, b, c, d, e, f, g, h, i, j, k, l, m)
    }

    func refreshGlance() async {
        guard wantsGlance else { return }
        await load(\.glance) { try await $0.glance() }
    }

    func refreshNow() async {
        resetConnectionIfNeeded()
        guard !now.isLoading else { return }
        let identity = connectionIdentity
        now.isLoading = true
        defer { if identity == connectionIdentity { now.isLoading = false } }
        do {
            let value = try await client().nowStatus()
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            now.value = value
            now.loadError = nil
            now.serverError = ""
            nowSnapshotIsStale = false
            if let data = try? JSONEncoder().encode(value) {
                snapshotDefaults.set(data, forKey: nowSnapshotKey)
            }
            if value.calendarError.isEmpty, value.supportsAcademicEvents {
                await syncStudyBrief(fallbackEvents: value.academicEvents)
            }
        } catch {
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            now.loadError = error.localizedDescription
            nowSnapshotIsStale = now.value != nil
        }
    }

    func refreshServices() async {
        await load(\.services) { try await $0.servicesStatus().services }
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
            await refreshNow()
        }
    }

    private func resetConnectionIfNeeded() {
        guard connectionIdentity != settings.connectionIdentity else { return }
        connectionIdentity = settings.connectionIdentity
        now = Section(); services = Section(); torrents = Section(); media = Section()
        storage = Section(); entities = Section(); links = Section(); studyBrief = Section()
        alerts = Section(); repairs = Section(); maintenance = Section(); portfolio = Section()
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
    private func load<Value>(
        _ keyPath: ReferenceWritableKeyPath<HubViewModel, Section<Value>>,
        serverError: (Value) -> String = { _ in "" },
        fetch: (JarvisAPIClient) async throws -> Value
    ) async {
        resetConnectionIfNeeded()
        guard !self[keyPath: keyPath].isLoading else { return }
        let identity = connectionIdentity
        self[keyPath: keyPath].isLoading = true
        defer { if identity == connectionIdentity { self[keyPath: keyPath].isLoading = false } }
        do {
            let value = try await fetch(client())
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            self[keyPath: keyPath].value = value
            self[keyPath: keyPath].serverError = serverError(value)
            self[keyPath: keyPath].loadError = nil
        } catch {
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            self[keyPath: keyPath].loadError = error.localizedDescription
        }
    }

    /// Variant for endpoints whose envelope and rendered value differ.
    private func load<Raw, Value>(
        _ keyPath: ReferenceWritableKeyPath<HubViewModel, Section<Value>>,
        serverError: (Raw) -> String,
        transform: (Raw) -> Value,
        fetch: (JarvisAPIClient) async throws -> Raw
    ) async {
        resetConnectionIfNeeded()
        guard !self[keyPath: keyPath].isLoading else { return }
        let identity = connectionIdentity
        self[keyPath: keyPath].isLoading = true
        defer { if identity == connectionIdentity { self[keyPath: keyPath].isLoading = false } }
        do {
            let raw = try await fetch(client())
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            self[keyPath: keyPath].value = transform(raw)
            self[keyPath: keyPath].serverError = serverError(raw)
            self[keyPath: keyPath].loadError = nil
        } catch {
            guard !Task.isCancelled, identity == settings.connectionIdentity else { return }
            self[keyPath: keyPath].loadError = error.localizedDescription
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
                    _ = await (storage, maintenance, glance)
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
                try? await client.streamHome { [weak self] seq, theme, look in self?.homeChanged(seq, theme: theme, look: look) }
                self?.homeStreamLive = false
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

    private func homeChanged(_ seq: Int, theme: String?, look: Double?) {
        homeStreamLive = true
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
