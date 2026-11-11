import Foundation

// Wire types for Core's API (app/server.py). Same shapes the phone decodes
// in ios/JarvisCompanion/JarvisCompanion/Models and Networking.

// MARK: Chat

struct ServerSession: Decodable, Identifiable, Hashable {
    let sessionID: String
    let updatedAt: String?
    let title: String?
    let preview: String?

    var id: String { sessionID }
    var label: String {
        let text = (title?.isEmpty == false ? title : preview) ?? ""
        return text.isEmpty ? "New conversation" : text
    }

    enum CodingKeys: String, CodingKey {
        case title, preview
        case sessionID = "session_id"
        case updatedAt = "updated_at"
    }
}

struct ServerMessage: Decodable {
    let role: String
    let content: String
}

/// One bubble in the chat transcript.
struct ChatLine: Identifiable, Hashable {
    let id = UUID()
    let fromNick: Bool
    var text: String
}

// MARK: Home

struct HomeEntities: Decodable {
    let error: String
    let entities: [HomeEntity]
}

struct HomeEntity: Decodable, Identifiable, Hashable {
    let entityID: String
    let friendlyName: String
    let domain: String
    let state: String
    let light: LightDetails?

    var id: String { entityID }
    var isOn: Bool { state == "on" }
    var isToggleable: Bool { state == "on" || state == "off" }

    var symbol: String {
        switch domain {
        case "light": return isOn ? "lightbulb.fill" : "lightbulb"
        case "fan": return "fan"
        case "media_player": return "tv"
        case "climate": return "thermometer.medium"
        case "lock": return isOn ? "lock.open" : "lock"
        default: return isOn ? "power.circle.fill" : "power.circle"
        }
    }

    enum CodingKeys: String, CodingKey {
        case domain, state, light
        case entityID = "entity_id"
        case friendlyName = "friendly_name"
    }
}

struct LightDetails: Decodable, Hashable {
    let dimmable: Bool
    let brightnessPct: Int?

    enum CodingKeys: String, CodingKey {
        case dimmable
        case brightnessPct = "brightness_pct"
    }
}

struct HomeScenes: Decodable {
    let error: String
    let scenes: [HomeScene]
    let current: String?
}

struct HomeScene: Decodable, Identifiable, Hashable {
    let entityID: String
    let name: String
    let icon: String

    var id: String { entityID }

    /// HA's mdi icon or the name, as an SF Symbol (the phone's table).
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

struct ErrorField: Decodable {
    let error: String?
}

// MARK: Alerts

struct JarvisAlert: Decodable, Identifiable, Hashable {
    let id: Int
    let severity: String
    let title: String
    let message: String
    let createdAt: Double
    let repeats: Int?

    enum CodingKeys: String, CodingKey {
        case id, severity, title, message, repeats
        case createdAt = "created_at"
    }
}

// MARK: Donna

struct DonnaMessage: Codable, Identifiable, Hashable {
    let role: String
    let text: String
    let at: Double
    var id: String { "\(at)-\(role)" }
    var fromNick: Bool { role == "user" }
}

struct DonnaSession: Decodable {
    let id: Int
    let messages: [DonnaMessage]
}

struct DonnaState: Decodable {
    let session: DonnaSession?
    let hasCaseFile: Bool
    enum CodingKeys: String, CodingKey { case session; case hasCaseFile = "has_case_file" }
}

struct DonnaReply: Decodable {
    let reply: String
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
    let caseFile: String
    let notes: [Note]
    let followups: [Followup]
    enum CodingKeys: String, CodingKey { case notes, followups; case caseFile = "case_file" }
}
