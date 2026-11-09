import Foundation

/// Wire models for the seven dashboard endpoints.
///
/// Every read endpoint degrades server-side: a dead service comes back as
/// HTTP 200 with a non-empty `error` and empty collections, so these models
/// treat `error` as a first-class field rather than a failure.

struct NowStatus: Codable {
    let generatedAt: Double
    let timers: [NowTimer]
    let reminders: [NowReminder]
    let nextEvent: NowEvent?
    let calendarError: String
    let academicEvents: [AcademicEvent]
    let supportsAcademicEvents: Bool
    /// Overdue and due-within-a-week maintenance; empty on an older Core.
    let maintenance: [MaintenanceTask]

    var generatedDate: Date { Date(timeIntervalSince1970: generatedAt) }

    enum CodingKeys: String, CodingKey {
        case timers, reminders
        case generatedAt = "generated_at"
        case nextEvent = "next_event"
        case calendarError = "calendar_error"
        case academicEvents = "academic_events"
        case maintenance
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try values.decode(Double.self, forKey: .generatedAt)
        timers = try values.decode([NowTimer].self, forKey: .timers)
        reminders = try values.decode([NowReminder].self, forKey: .reminders)
        nextEvent = try values.decodeIfPresent(NowEvent.self, forKey: .nextEvent)
        calendarError = try values.decodeIfPresent(String.self, forKey: .calendarError) ?? ""
        supportsAcademicEvents = values.contains(.academicEvents)
        academicEvents = try values.decodeIfPresent([AcademicEvent].self, forKey: .academicEvents) ?? []
        maintenance = try values.decodeIfPresent([MaintenanceTask].self, forKey: .maintenance) ?? []
    }
}

struct NowTimer: Codable, Identifiable {
    let id: String
    let label: String
    let state: String
    let remainingSeconds: Int
    let expiresAt: Double

    enum CodingKeys: String, CodingKey {
        case id, label, state
        case remainingSeconds = "remaining_seconds"
        case expiresAt = "expires_at"
    }
}

struct NowReminder: Codable, Identifiable {
    let id: String
    let text: String
    let dueAt: Double?
    let isDue: Bool

    enum CodingKeys: String, CodingKey {
        case id, text
        case dueAt = "due_at"
        case isDue = "is_due"
    }
}

struct NowEvent: Codable, Identifiable {
    let id: String
    let title: String
    let startAt: Double?
    let allDay: Bool
    let location: String

    enum CodingKeys: String, CodingKey {
        case id, title, location
        case startAt = "start_at"
        case allDay = "all_day"
    }
}

struct AcademicEvent: Codable, Identifiable {
    let id: String
    let title: String
    let startAt: Double?
    let allDay: Bool
    let location: String
    let daysLeft: Int

    enum CodingKeys: String, CodingKey {
        case id, title, location
        case startAt = "start_at"
        case allDay = "all_day"
        case daysLeft = "days_left"
    }
}

/// `/api/study/brief`: the daily assessment countdown as JARVIS Core plans
/// and writes it. The phone registers `briefs` as local notifications; the
/// rules (hour, window, keywords, tone) are settings Nick changes by talking
/// to JARVIS, so the app only displays them.
struct StudyBrief: Decodable {
    let error: String
    let enabled: Bool
    let hour: Int
    let windowDays: Int
    let keywords: [String]
    let style: String
    let composedBy: String
    let briefs: [StudyBriefEntry]

    enum CodingKeys: String, CodingKey {
        case error, enabled, hour, keywords, style, briefs
        case windowDays = "window_days"
        case composedBy = "composed_by"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        error = try values.decodeIfPresent(String.self, forKey: .error) ?? ""
        enabled = try values.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        hour = try values.decodeIfPresent(Int.self, forKey: .hour) ?? 9
        windowDays = try values.decodeIfPresent(Int.self, forKey: .windowDays) ?? 14
        keywords = try values.decodeIfPresent([String].self, forKey: .keywords) ?? []
        style = try values.decodeIfPresent(String.self, forKey: .style) ?? ""
        composedBy = try values.decodeIfPresent(String.self, forKey: .composedBy) ?? ""
        briefs = try values.decodeIfPresent([StudyBriefEntry].self, forKey: .briefs) ?? []
    }
}

struct StudyBriefEntry: Decodable, Identifiable, Equatable {
    let id: String
    let fireAt: Double
    let body: String
    let items: [StudyBriefItem]

    var fireDate: Date { Date(timeIntervalSince1970: fireAt) }

    init(id: String, fireAt: Double, body: String, items: [StudyBriefItem]) {
        self.id = id
        self.fireAt = fireAt
        self.body = body
        self.items = items
    }

    enum CodingKeys: String, CodingKey {
        case id, body, items
        case fireAt = "fire_at"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        fireAt = try values.decode(Double.self, forKey: .fireAt)
        body = try values.decodeIfPresent(String.self, forKey: .body) ?? ""
        items = try values.decodeIfPresent([StudyBriefItem].self, forKey: .items) ?? []
    }
}

struct StudyBriefItem: Decodable, Equatable {
    let title: String
    let daysLeft: Int

    init(title: String, daysLeft: Int) {
        self.title = title
        self.daysLeft = daysLeft
    }

    enum CodingKeys: String, CodingKey {
        case title
        case daysLeft = "days_left"
    }
}

struct StorageStatus: Decodable {
    let error: String
    let label: String
    let totalBytes: Int64
    let usedBytes: Int64
    let freeBytes: Int64
    let usedFraction: Double

    enum CodingKeys: String, CodingKey {
        case error, label
        case totalBytes = "total_bytes"
        case usedBytes = "used_bytes"
        case freeBytes = "free_bytes"
        case usedFraction = "used_fraction"
    }
}

struct TorrentsStatus: Decodable {
    let error: String
    let dlSpeed: Int
    let upSpeed: Int
    let torrents: [TorrentTransfer]

    enum CodingKeys: String, CodingKey {
        case error
        case dlSpeed = "dl_speed"
        case upSpeed = "up_speed"
        case torrents
    }
}

struct TorrentTransfer: Decodable, Identifiable {
    let name: String
    let progress: Double
    let state: String
    let dlSpeed: Int
    let upSpeed: Int
    let eta: Int
    let size: Int
    /// Targets pause/resume. Optional so the app still renders the list when
    /// talking to an older JARVIS Core that predates the field; the controls
    /// simply stay hidden there.
    let hash: String?

    var id: String { hash ?? name }
    var isPaused: Bool {
        state.lowercased().hasPrefix("paused") || state.lowercased().hasPrefix("stopped")
    }

    enum CodingKeys: String, CodingKey {
        case name, progress, state, eta, size, hash
        case dlSpeed = "dl_speed"
        case upSpeed = "up_speed"
    }
}

struct MediaStatus: Decodable {
    let radarr: ServarrSection
    let sonarr: ServarrSection
}

struct ServarrSection: Decodable {
    let error: String
    let queue: [MediaQueueItem]
    let recent: [MediaRecentItem]
}

struct MediaQueueItem: Decodable, Identifiable {
    let title: String
    let progress: Double
    let size: Int
    let sizeLeft: Int
    let timeLeft: String
    let status: String
    let errorMessage: String

    let queueID: String?
    var id: String { queueID ?? title }

    enum CodingKeys: String, CodingKey {
        case queueID = "id"
        case title, progress, size, status
        case sizeLeft = "size_left"
        case timeLeft = "time_left"
        case errorMessage = "error_message"
    }
}

struct MediaRecentItem: Decodable, Identifiable {
    let title: String
    let year: Int
    let added: String

    var id: String { title + added }
}

struct ServicesStatus: Decodable {
    let services: [ServiceStatus]
}

struct ServiceStatus: Decodable, Identifiable {
    let name: String
    let up: Bool
    let latencyMS: Int?
    let error: String

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, up, error
        case latencyMS = "latency_ms"
    }
}

struct ServiceLinks: Decodable {
    let links: [ServiceLink]
}

struct ServiceLink: Decodable, Identifiable {
    let name: String
    let url: String
    /// Tailscale address for the same service ("" when the server has no
    /// LINK_REMOTE_HOSTS mapping for its host, or an older Core).
    let remoteURL: String
    let iconHint: String

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, url
        case remoteURL = "remote_url"
        case iconHint = "icon_hint"
    }

    init(name: String, url: String, remoteURL: String = "", iconHint: String) {
        self.name = name
        self.url = url
        self.remoteURL = remoteURL
        self.iconHint = iconHint
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        url = try c.decode(String.self, forKey: .url)
        remoteURL = try c.decodeIfPresent(String.self, forKey: .remoteURL) ?? ""
        iconHint = try c.decode(String.self, forKey: .iconHint)
    }

    /// The home URL when the home network answers, Tailscale otherwise
    /// (including while that is still unknown: Tailscale works anywhere).
    func destination(onHomeNetwork: Bool?) -> URL? {
        if remoteURL.isEmpty || onHomeNetwork == true { return URL(string: url) }
        return URL(string: remoteURL)
    }
}

struct HomeEntities: Decodable {
    let error: String
    let entities: [HomeEntity]
    /// `sun.sun`'s state ("below_horizon" after dark); nil from an older Core.
    let sun: String?
}

struct HomeEntity: Decodable, Identifiable {
    let entityID: String
    let friendlyName: String
    let domain: String
    var state: String
    /// Lights only: what it can do and where it is (brightness/colour sheet).
    var light: LightDetails? = nil
    /// Fans only: speed and modes (hold-and-drag on the tile, `FanSheet`).
    var fan: FanDetails? = nil

    init(entityID: String, friendlyName: String, domain: String, state: String, light: LightDetails? = nil,
         fan: FanDetails? = nil) {
        self.entityID = entityID
        self.friendlyName = friendlyName
        self.domain = domain
        self.state = state
        self.light = light
        self.fan = fan
    }

    var id: String { entityID }
    var isOn: Bool { state == "on" }
    /// `unavailable` / `unknown` entities render but cannot be toggled.
    var isToggleable: Bool { state == "on" || state == "off" }

    enum CodingKeys: String, CodingKey {
        case domain, state, light, fan
        case entityID = "entity_id"
        case friendlyName = "friendly_name"
    }
}

struct FanDetails: Decodable, Equatable {
    /// 0-100; nil while the fan is off on some integrations.
    let percentage: Int?
    let step: Double?
    let presetModes: [String]
    let presetMode: String?

    enum CodingKeys: String, CodingKey {
        case percentage, step
        case presetModes = "preset_modes"
        case presetMode = "preset_mode"
    }
}

/// The room camera's state (Core's `brain/camera.py`).
struct CameraStatus: Decodable, Equatable {
    /// The lens should be capturing (a live viewer, no kill lockout).
    let wanted: Bool
    /// Nick's phone is watching right now.
    let viewing: Bool
    /// Locked off (a kill is in effect).
    let killed: Bool
    /// Seconds of lockout left.
    let killSecondsLeft: Int
    /// A fresh frame is available to pull.
    let hasFrame: Bool

    enum CodingKeys: String, CodingKey {
        case wanted, viewing, killed
        case killSecondsLeft = "kill_seconds_left"
        case hasFrame = "has_frame"
    }
}

/// The room speaker's level (Core's `speech/room_volume.py`, 0-10).
struct RoomVolume: Decodable, Equatable {
    let level: Int?
    /// Inside Core's quiet hours: the slider sets the night level.
    let night: Bool
    /// The quiet-hours level (Settings' Night volume); nil on an older Core.
    var nightLevel: Int? = nil

    enum CodingKeys: String, CodingKey {
        case level, night
        case nightLevel = "night_level"
    }
}

struct LightDetails: Decodable, Equatable {
    let dimmable: Bool
    let color: Bool
    let temperature: Bool
    let brightnessPct: Int?
    let rgb: [Int]?
    let kelvin: Int?
    let minKelvin: Int?
    let maxKelvin: Int?
    /// Nick's own Govee rainbow is set up for this light (Core's LIGHT_RAINBOWS).
    let rainbow: Bool?

    enum CodingKeys: String, CodingKey {
        case dimmable, color, temperature, rgb, kelvin, rainbow
        case brightnessPct = "brightness_pct"
        case minKelvin = "min_kelvin"
        case maxKelvin = "max_kelvin"
    }
}

/// Saved Home Assistant scenes (`GET /api/home/scenes`); `last` is the one run most recently.
struct HomeScenes: Decodable {
    let error: String
    let scenes: [HomeScene]
    let last: String?
    /// The scene the room is in: nil after Normal, Peace or Goodnight (Core
    /// says so); an older Core without the field means the last one run.
    let current: String?

    init(error: String, scenes: [HomeScene], last: String?, current: String? = nil) {
        self.error = error
        self.scenes = scenes
        self.last = last
        self.current = current
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        error = try c.decode(String.self, forKey: .error)
        scenes = try c.decode([HomeScene].self, forKey: .scenes)
        last = try c.decodeIfPresent(String.self, forKey: .last)
        current = c.contains(.current) ? try c.decodeIfPresent(String.self, forKey: .current) : last
    }

    enum CodingKeys: String, CodingKey { case error, scenes, last, current }
}

struct HomeScene: Decodable, Identifiable {
    let entityID: String
    let name: String
    let icon: String

    var id: String { entityID }

    /// HA's mdi icon or the name, as an SF Symbol.
    var symbol: String {
        let text = (icon + " " + name).lowercased()
        let table: [(String, String)] = [
            ("morning", "sun.horizon"), ("wake", "sun.horizon"), ("sun", "sun.max"),
            ("movie", "film"), ("tv", "tv"), ("film", "film"),
            ("night", "moon.stars"), ("bed", "bed.double"), ("sleep", "moon.zzz"),
            ("study", "book"), ("work", "laptopcomputer"), ("focus", "scope"), ("read", "book"),
            ("party", "party.popper"), ("game", "gamecontroller"), ("relax", "sofa"), ("chill", "sofa"),
            ("off", "power"), ("red", "light.beacon.max"), ("dinner", "fork.knife"), ("peace", "hand.wave"),
        ]
        return table.first { text.contains($0.0) }?.1 ?? "sparkles"
    }

    enum CodingKeys: String, CodingKey {
        case name, icon
        case entityID = "entity_id"
    }
}

struct HomeControlResult: Decodable {
    let error: String
    let entityID: String
    let state: String?

    enum CodingKeys: String, CodingKey {
        case error, state
        case entityID = "entity_id"
    }
}

struct TorrentControlResult: Decodable {
    let error: String
    let message: String
}

/// `/api/maintenance`: a recurring chore with its countdown, computed by
/// JARVIS Core so every client agrees on "days remaining".
struct MaintenanceTask: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let scheduleType: String
    let intervalValue: Int?
    let intervalUnit: String?
    let fixedMonth: Int?
    let fixedDay: Int?
    let notes: String
    let lastDone: String?
    let lastNote: String?
    let logCount: Int
    let daysSince: Int?
    let nextDue: String?
    let daysRemaining: Int?
    /// "overdue", "due", "soon", "ok", or "log" (log-only task).
    let status: String
    let scheduleLabel: String

    var isOverdue: Bool { status == "overdue" }
    var needsAttention: Bool { status == "overdue" || status == "due" }
    var lastDoneDate: Date? { lastDone.flatMap(MaintenanceTask.parse) }

    /// Countdown wording shared by the Now feed and the maintenance card.
    var countdown: String {
        guard let days = daysRemaining else {
            guard let since = daysSince else { return "NEVER LOGGED" }
            switch since {
            case 0: return "DONE TODAY"
            case 1: return "1 DAY AGO"
            default: return "\(since) DAYS AGO"
            }
        }
        switch days {
        case -1: return "1 DAY OVERDUE"
        case ..<0: return "\(-days) DAYS OVERDUE"
        case 0: return lastDone == nil ? "DUE NOW" : "DUE TODAY"
        case 1: return "TOMORROW"
        default: return "IN \(days) DAYS"
        }
    }

    /// "Last done 12 days ago" / "Never logged".
    var lastDoneText: String {
        guard let since = daysSince else { return "Never logged" }
        switch since {
        case 0: return "Done today"
        case 1: return "Done yesterday"
        default: return "Done \(since) days ago"
        }
    }

    /// The server writes local ISO timestamps without a zone.
    static func parse(_ value: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = value.count > 10 ? "yyyy-MM-dd'T'HH:mm:ss" : "yyyy-MM-dd"
        return formatter.date(from: value)
    }

    enum CodingKeys: String, CodingKey {
        case id, name, notes, status
        case scheduleType = "schedule_type"
        case intervalValue = "interval_value"
        case intervalUnit = "interval_unit"
        case fixedMonth = "fixed_month"
        case fixedDay = "fixed_day"
        case lastDone = "last_done"
        case lastNote = "last_note"
        case logCount = "log_count"
        case daysSince = "days_since"
        case nextDue = "next_due"
        case daysRemaining = "days_remaining"
        case scheduleLabel = "schedule_label"
    }
}

struct MaintenanceLogEntry: Codable, Identifiable, Hashable {
    let id: Int
    let completedAt: String
    let note: String

    var date: Date? { MaintenanceTask.parse(completedAt) }

    enum CodingKeys: String, CodingKey {
        case id, note
        case completedAt = "completed_at"
    }
}

/// Body for creating or editing a task. `lastDone` (yyyy-MM-dd) only
/// applies on create, so a new task can start from when it was last done.
struct MaintenanceTaskDraft: Encodable {
    var name = ""
    var scheduleType = "interval"
    var intervalValue: Int? = 3
    var intervalUnit: String? = "months"
    var fixedMonth: Int?
    var fixedDay: Int?
    var notes = ""
    var lastDone: String?

    init() {}

    init(_ task: MaintenanceTask) {
        name = task.name
        scheduleType = task.scheduleType
        intervalValue = task.intervalValue ?? 3
        intervalUnit = task.intervalUnit ?? "months"
        fixedMonth = task.fixedMonth
        fixedDay = task.fixedDay
        notes = task.notes
    }

    enum CodingKeys: String, CodingKey {
        case name, notes
        case scheduleType = "schedule_type"
        case intervalValue = "interval_value"
        case intervalUnit = "interval_unit"
        case fixedMonth = "fixed_month"
        case fixedDay = "fixed_day"
        case lastDone = "last_done"
    }

    /// Only the fields that belong to the chosen schedule go over the wire.
    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(name, forKey: .name)
        try values.encode(scheduleType, forKey: .scheduleType)
        try values.encode(notes, forKey: .notes)
        try values.encodeIfPresent(lastDone, forKey: .lastDone)
        switch scheduleType {
        case "interval":
            try values.encodeIfPresent(intervalValue, forKey: .intervalValue)
            try values.encodeIfPresent(intervalUnit, forKey: .intervalUnit)
        case "fixed_calendar":
            try values.encodeIfPresent(fixedMonth, forKey: .fixedMonth)
            try values.encodeIfPresent(fixedDay, forKey: .fixedDay)
        default: break
        }
    }
}

/// The TV wake-up schedule (`GET`/`PUT /api/wake`): a time or none for each
/// weekday, Monday first, plus what plays. `onceAt` is a one-off set by
/// talking to JARVIS ("in 3 minutes"); the editor shows it but leaves it be.
struct WakeSchedule: Decodable, Equatable {
    var times: [String?]
    var music: String
    var label: String
    var volume: Int
    var enabled: Bool
    var onceAt: String?
    var nextAt: Double?
    var summary: String
    /// One-day changes made by asking JARVIS ("skip tomorrow", "8:30 tomorrow");
    /// `time` nil means skipped. They drop off once the day has passed.
    var overrides: [Override]?
    /// Set while an alarm is going off unanswered or snoozed (Core 2026-11-01+).
    var ringing: Ringing?

    struct Override: Decodable, Equatable, Hashable {
        let date: String
        let time: String?
    }

    /// `state` is "ringing" or "snoozed"; `since` (when it went off) tells one
    /// alarm from the next, `until` is when a snooze rings again.
    struct Ringing: Decodable, Equatable {
        let state: String
        let since: Double?
        let detail: String
        let until: Double?
    }

    enum CodingKeys: String, CodingKey {
        case times, music, label, volume, enabled, summary
        case onceAt = "once_at"
        case nextAt = "next_at"
        case overrides, ringing
    }
}

struct WakeScheduleDraft: Encodable {
    var times: [String?]
    var music: String
    var label: String
    var volume: Int
    var enabled: Bool
}

/// Nick's Schwab portfolio (`GET /api/portfolio`), read-only and live: prices
/// stream into Core, so each poll can move. Value fields are nil until Core
/// has fetched the accounts once.
struct Portfolio: Decodable, Equatable {
    struct Position: Decodable, Equatable, Identifiable {
        let symbol: String
        let quantity: Double
        let value: Double
        let todayChange: Double
        let last: Double?
        var id: String { symbol }

        enum CodingKeys: String, CodingKey {
            case symbol, quantity, value, last
            case todayChange = "today_change"
        }
    }

    let configured: Bool
    let linked: Bool
    var loginExpiresAt: Double?
    var streaming: Bool?
    var error: String?
    var value: Double?
    var cash: Double?
    var todayChange: Double?
    var todayPct: Double?
    var yesterdayChange: Double?
    /// Unrealized gain/loss on what's held now, against the average price paid.
    var totalGain: Double?
    var totalGainPct: Double?
    var positions: [Position]?
    /// The login lives on the Schwab hub and JARVIS borrows it (one Schwab
    /// login for JARVIS and Coopa); log in on `loginPage` in Safari.
    var borrowed: Bool?
    var loginPage: URL?

    enum CodingKeys: String, CodingKey {
        case configured, linked, streaming, error, value, cash, positions, borrowed
        case loginExpiresAt = "login_expires_at"
        case loginPage = "login_page"
        case todayChange = "today_change"
        case todayPct = "today_pct"
        case yesterdayChange = "yesterday_change"
        case totalGain = "total_gain"
        case totalGainPct = "total_gain_pct"
    }

    /// Within a day of Schwab's weekly login running out (or past it).
    var loginDueSoon: Bool {
        guard let loginExpiresAt else { return false }
        return loginExpiresAt - Date().timeIntervalSince1970 < 24 * 3600
    }
}

/// The room voice right now (`/api/voice/stream`): phase is idle, listening,
/// thinking, speaking, done or error; transcript fills in live while listening.
struct VoiceState: Decodable, Equatable {
    let seq: Int
    let phase: String
    let transcript: String
    let reply: String

    static let idle = VoiceState(seq: 0, phase: "idle", transcript: "", reply: "")
}

/// `GET /api/glance`, the parts the Mac's Desk shows: weather and the
/// calendar agenda (the TV reads the same snapshot).
struct Glance: Decodable {
    let weather: GlanceWeather?
    let events: [GlanceEvent]

    enum CodingKeys: String, CodingKey { case weather, events }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        weather = try? values.decodeIfPresent(GlanceWeather.self, forKey: .weather)
        events = (try? values.decodeIfPresent([GlanceEvent].self, forKey: .events)) ?? []
    }
}

struct GlanceWeather: Decodable {
    let online: Bool?
    let location: String?
    let temperatureF: Double?
    let description: String?
    let highF: Double?
    let lowF: Double?
    let humidityPercent: Double?
    let windMph: Double?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case online, location, description, error
        case temperatureF = "temperature_f"
        case highF = "high_f"
        case lowF = "low_f"
        case humidityPercent = "humidity_percent"
        case windMph = "wind_mph"
    }

    /// An SF Symbol for the description ("clear", "light rain", ...).
    func symbol(night: Bool) -> String {
        let text = (description ?? "").lowercased()
        if text.contains("thunder") || text.contains("storm") { return "cloud.bolt.rain.fill" }
        if text.contains("snow") || text.contains("sleet") { return "cloud.snow.fill" }
        if text.contains("rain") || text.contains("drizzle") || text.contains("shower") { return "cloud.rain.fill" }
        if text.contains("fog") || text.contains("mist") || text.contains("haze") || text.contains("smoke") { return "cloud.fog.fill" }
        if text.contains("overcast") { return "cloud.fill" }
        if text.contains("cloud") { return night ? "cloud.moon.fill" : "cloud.sun.fill" }
        return night ? "moon.stars.fill" : "sun.max.fill"
    }
}

struct GlanceEvent: Decodable, Identifiable {
    let title: String
    let startAt: Double?
    let allDay: Bool
    let calendar: String?
    let category: String?

    var id: String { "\(startAt ?? 0)-\(title)-\(calendar ?? "")" }

    enum CodingKeys: String, CodingKey {
        case title, calendar, category
        case startAt = "start_at"
        case allDay = "all_day"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? values.decode(String.self, forKey: .title)) ?? "Untitled"
        startAt = try? values.decodeIfPresent(Double.self, forKey: .startAt)
        allDay = (try? values.decodeIfPresent(Bool.self, forKey: .allDay)) ?? false
        calendar = try? values.decodeIfPresent(String.self, forKey: .calendar)
        category = try? values.decodeIfPresent(String.self, forKey: .category)
    }
}

/// `GET /api/media/profiles`: the service's quality profiles and the one a
/// request uses when none is picked.
struct MediaProfiles: Decodable {
    let profiles: [String]
    let `default`: String
    let error: String?
}
