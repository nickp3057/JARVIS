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

    var generatedDate: Date { Date(timeIntervalSince1970: generatedAt) }

    enum CodingKeys: String, CodingKey {
        case timers, reminders
        case generatedAt = "generated_at"
        case nextEvent = "next_event"
        case calendarError = "calendar_error"
        case academicEvents = "academic_events"
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
    let iconHint: String

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, url
        case iconHint = "icon_hint"
    }
}

struct HomeEntities: Decodable {
    let error: String
    let entities: [HomeEntity]
}

struct HomeEntity: Decodable, Identifiable {
    let entityID: String
    let friendlyName: String
    let domain: String
    let state: String

    init(entityID: String, friendlyName: String, domain: String, state: String) {
        self.entityID = entityID
        self.friendlyName = friendlyName
        self.domain = domain
        self.state = state
    }

    var id: String { entityID }
    var isOn: Bool { state == "on" }
    /// `unavailable` / `unknown` entities render but cannot be toggled.
    var isToggleable: Bool { state == "on" || state == "off" }

    enum CodingKeys: String, CodingKey {
        case domain, state
        case entityID = "entity_id"
        case friendlyName = "friendly_name"
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
