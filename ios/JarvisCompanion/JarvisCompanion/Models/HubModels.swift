import Foundation

/// Wire models for the seven dashboard endpoints.
///
/// Every read endpoint degrades server-side: a dead service comes back as
/// HTTP 200 with a non-empty `error` and empty collections, so these models
/// treat `error` as a first-class field rather than a failure.

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

    var id: String { title + timeLeft }

    enum CodingKeys: String, CodingKey {
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
