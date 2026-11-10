import Foundation

/// Donna, the private counsellor (brain/donna.py). Nothing here is shared
/// with JARVIS's chat, memory or dashboards.
struct DonnaMessage: Codable, Identifiable, Hashable {
    let role: String
    let text: String
    let at: Double

    var id: String { "\(at)-\(role)" }
    var fromNick: Bool { role == "user" }
}

struct DonnaSession: Codable, Hashable {
    let id: Int
    let startedAt: Double
    let messages: [DonnaMessage]

    enum CodingKeys: String, CodingKey {
        case id, messages
        case startedAt = "started_at"
    }
}

struct DonnaCheckin: Codable, Identifiable, Hashable {
    let id: Int
    let dueAt: Double
    let message: String

    enum CodingKeys: String, CodingKey {
        case id, message
        case dueAt = "due_at"
    }
}

struct DonnaState: Decodable {
    let session: DonnaSession?
    let checkins: [DonnaCheckin]
    let hasCaseFile: Bool

    enum CodingKeys: String, CodingKey {
        case session, checkins
        case hasCaseFile = "has_case_file"
    }
}

struct DonnaReply: Decodable {
    let reply: String
    let sessionID: Int
    let checkins: [DonnaCheckin]

    enum CodingKeys: String, CodingKey {
        case reply, checkins
        case sessionID = "session_id"
    }
}

struct DonnaMemory: Decodable {
    struct Note: Decodable, Identifiable {
        let id: Int
        let startedAt: Double
        let note: String
        enum CodingKeys: String, CodingKey { case id, note; case startedAt = "started_at" }
    }
    struct Followup: Decodable, Identifiable {
        let id: Int
        let text: String
    }
    struct Mood: Decodable, Identifiable {
        let at: Double
        let score: Int
        let note: String
        var id: Double { at }
    }

    let caseFile: String
    let notes: [Note]
    let followups: [Followup]
    let moods: [Mood]

    enum CodingKeys: String, CodingKey {
        case notes, followups, moods
        case caseFile = "case_file"
    }
}
