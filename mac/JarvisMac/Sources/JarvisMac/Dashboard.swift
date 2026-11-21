import Foundation

// Wire types and calls for the Overview, Media and Skills pages. Same shapes
// the phone decodes (ios/.../Models/HubModels.swift, Networking).

// MARK: Overview

struct NowSnapshot: Decodable {
    struct Timer: Decodable, Identifiable {
        let id: String
        let label: String
        let state: String
        let expiresAt: Double
        enum CodingKeys: String, CodingKey { case id, label, state; case expiresAt = "expires_at" }
    }
    struct Reminder: Decodable, Identifiable {
        let id: String
        let text: String
        let dueAt: Double?
        let isDue: Bool
        enum CodingKeys: String, CodingKey { case id, text; case dueAt = "due_at"; case isDue = "is_due" }
    }
    struct Event: Decodable {
        let title: String
        let startAt: Double?
        let allDay: Bool
        let location: String
        enum CodingKeys: String, CodingKey { case title, location; case startAt = "start_at"; case allDay = "all_day" }
    }
    let timers: [Timer]
    let reminders: [Reminder]
    let nextEvent: Event?
    enum CodingKeys: String, CodingKey { case timers, reminders; case nextEvent = "next_event" }
}

struct ServiceState: Decodable, Identifiable {
    let name: String
    let up: Bool
    let latencyMS: Int?
    var id: String { name }
    enum CodingKeys: String, CodingKey { case name, up; case latencyMS = "latency_ms" }
}

struct StorageState: Decodable {
    let error: String
    let label: String
    let freeBytes: Int64
    let totalBytes: Int64
    let usedFraction: Double
    enum CodingKeys: String, CodingKey {
        case error, label
        case freeBytes = "free_bytes"
        case totalBytes = "total_bytes"
        case usedFraction = "used_fraction"
    }
}

struct PresenceState: Decodable {
    let location: String?
    let roomQuiet: Bool
    enum CodingKeys: String, CodingKey { case location; case roomQuiet = "room_quiet" }
}

// MARK: Media

struct TorrentList: Decodable {
    let error: String
    let dlSpeed: Int
    let upSpeed: Int
    let torrents: [Torrent]
    enum CodingKeys: String, CodingKey { case error, torrents; case dlSpeed = "dl_speed"; case upSpeed = "up_speed" }
}

struct Torrent: Decodable, Identifiable {
    let name: String
    let progress: Double
    let state: String
    let dlSpeed: Int
    let eta: Int
    let size: Int
    let hash: String?
    var id: String { hash ?? name }
    var isPaused: Bool { state.lowercased().hasPrefix("paused") || state.lowercased().hasPrefix("stopped") }
    var done: Bool { progress >= 1 }
    enum CodingKeys: String, CodingKey { case name, progress, state, eta, size, hash; case dlSpeed = "dl_speed" }
}

struct PlexTitle: Decodable, Identifiable, Hashable {
    let ratingKey: String
    let title: String
    let year: Int?
    let thumb: String
    var durationMs: Int? = nil
    var viewOffsetMs: Int? = nil
    var watched: Bool? = nil
    var episodes: Int? = nil
    var watchedEpisodes: Int? = nil
    var id: String { ratingKey }
    var progress: Double {
        if let episodes, episodes > 0 { return Double(watchedEpisodes ?? 0) / Double(episodes) }
        guard let durationMs, durationMs > 0 else { return 0 }
        return Double(viewOffsetMs ?? 0) / Double(durationMs)
    }
    var resumable: Bool { (viewOffsetMs ?? 0) > 60_000 }
    enum CodingKeys: String, CodingKey {
        case title, year, thumb, watched, episodes
        case ratingKey = "rating_key"
        case durationMs = "duration_ms"
        case viewOffsetMs = "view_offset_ms"
        case watchedEpisodes = "watched_episodes"
    }
}

struct PlexLibrary: Decodable, Identifiable, Hashable {
    let key: String
    let title: String
    let type: String
    let hidden: Bool
    var id: String { key }
}

// MARK: Skills and fixes

struct LearnedSkill: Decodable, Identifiable, Hashable {
    struct Step: Decodable, Hashable { let tool: String }
    struct Param: Decodable, Hashable { let name: String }
    let id: String
    let name: String
    let description: String
    let status: String
    let steps: [Step]
    let params: [Param]
    var uses: Int? = nil
    var isDraft: Bool { status == "draft" }
    var title: String { name.replacingOccurrences(of: "_", with: " ").capitalized }
}

struct SkillWish: Decodable, Identifiable, Hashable {
    let id: String
    let summary: String
    let asks: [String]
    let count: Int
    var note: String? = nil
    var draftSkillID: String? = nil
    var drafting: Bool? = nil
    enum CodingKeys: String, CodingKey {
        case id, summary, asks, count, note, drafting
        case draftSkillID = "draft_skill_id"
    }
}

struct RepairItem: Decodable, Identifiable, Hashable {
    let id: String
    let label: String
    let rationale: String
    let status: String
    let result: String
    var approvedBy: String? = nil
    var isPending: Bool { status == "pending" }
    enum CodingKeys: String, CodingKey { case id, label, rationale, status, result; case approvedBy = "approved_by" }
}

extension JarvisAPI {
    func now() async throws -> NowSnapshot { try await get("api/now") }
    func services() async throws -> [ServiceState] {
        struct Envelope: Decodable { let services: [ServiceState] }
        let envelope: Envelope = try await get("api/status/services")
        return envelope.services
    }
    func storage() async throws -> StorageState { try await get("api/status/storage") }
    func presence() async throws -> PresenceState { try await get("api/presence") }
    func torrents() async throws -> TorrentList { try await get("api/status/torrents") }
    func torrentControl(hash: String, action: String) async throws {
        // The click is the confirmation, as on the phone.
        let body: [String: AnyEncodable] = ["torrent_hash": AnyEncodable(hash), "action": AnyEncodable(action),
                                            "confirmed": AnyEncodable(true)]
        _ = try await send("POST", "api/torrents/control", body: try JSONEncoder().encode(body))
    }

    func plexMovies(query: String, sort: String) async throws -> [PlexTitle] {
        struct Envelope: Decodable { let movies: [PlexTitle] }
        var items = [URLQueryItem(name: "sort", value: sort)]
        if !query.isEmpty { items.append(URLQueryItem(name: "query", value: query)) }
        let envelope: Envelope = try await get("api/plex/movies", query: items)
        return envelope.movies
    }
    func plexShows(query: String, sort: String) async throws -> [PlexTitle] {
        struct Envelope: Decodable { let shows: [PlexTitle] }
        var items = [URLQueryItem(name: "sort", value: sort)]
        if !query.isEmpty { items.append(URLQueryItem(name: "query", value: query)) }
        let envelope: Envelope = try await get("api/plex/shows", query: items)
        return envelope.shows
    }
    func plexLibraries() async throws -> [PlexLibrary] {
        struct Envelope: Decodable { let libraries: [PlexLibrary] }
        let envelope: Envelope = try await get("api/plex/libraries")
        return envelope.libraries
    }
    func setHiddenLibraries(_ keys: [String]) async throws -> [PlexLibrary] {
        struct Envelope: Decodable { let libraries: [PlexLibrary] }
        let envelope: Envelope = try await post("api/plex/libraries", body: ["hidden": AnyEncodable(keys)])
        return envelope.libraries
    }
    func poster(path: String) async throws -> Data {
        try await send("GET", "api/plex/poster",
                       query: [URLQueryItem(name: "path", value: path), URLQueryItem(name: "width", value: "300")])
    }
    func playOnTV(ratingKey: String, fromStart: Bool) async throws -> String {
        struct Result: Decodable { let message: String }
        let result: Result = try await post("api/plex/play", body: ["rating_key": AnyEncodable(ratingKey),
                                                                     "from_start": AnyEncodable(fromStart)])
        return result.message
    }

    func skills() async throws -> [LearnedSkill] {
        struct Envelope: Decodable { let skills: [LearnedSkill] }
        let envelope: Envelope = try await get("api/skills")
        return envelope.skills
    }
    func setSkill(id: String, status: String) async throws {
        _ = try await send("POST", "api/skills/\(id)", body: try JSONEncoder().encode(["status": status]))
    }
    func skillWishes() async throws -> [SkillWish] {
        struct Envelope: Decodable { let requests: [SkillWish] }
        let envelope: Envelope = try await get("api/skills/requests")
        return envelope.requests
    }
    func closeWish(id: String, status: String) async throws {
        _ = try await send("POST", "api/skills/requests/\(id)", body: try JSONEncoder().encode(["status": status]))
    }
    func draftSkill(wishID: String) async throws {
        _ = try await send("POST", "api/skills/requests/\(wishID)/draft", timeout: 30)
    }
    func repairs() async throws -> [RepairItem] {
        struct Envelope: Decodable { let repairs: [RepairItem] }
        let envelope: Envelope = try await get("api/repairs")
        return envelope.repairs
    }
    func resolveRepair(id: String, approve: Bool) async throws {
        _ = try await send("POST", "api/repairs/\(id)/\(approve ? "apply" : "reject")", timeout: 60)
    }
}
