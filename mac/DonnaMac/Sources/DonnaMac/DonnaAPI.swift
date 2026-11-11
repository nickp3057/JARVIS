import Foundation

// Wire types: the same shapes the phone decodes (ios/.../Models/DonnaModels.swift).

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

enum DonnaAPIError: LocalizedError {
    case notConfigured
    case unauthorized
    case server(Int, String)

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "Add Core's address and token in Settings (⌘,)."
        case .unauthorized: return "Core rejected the token (401). Check it in Settings (⌘,)."
        case .server(let code, let detail): return detail.isEmpty ? "Core answered \(code)." : detail
        }
    }
}

/// Thin client over Core's /api/donna endpoints (app/server.py).
struct DonnaAPI {
    let baseURL: URL
    let token: String

    @MainActor
    static func make(_ settings: MacSettings) throws -> DonnaAPI {
        guard let url = settings.baseURL, settings.isConfigured else { throw DonnaAPIError.notConfigured }
        return DonnaAPI(baseURL: url, token: settings.token.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func state() async throws -> DonnaState { try await call("GET", "api/donna") }
    func send(_ text: String) async throws -> DonnaReply {
        try await call("POST", "api/donna/message", body: ["text": text], timeout: 180)
    }
    func end() async throws { let _: [String: Bool] = try await call("POST", "api/donna/end", timeout: 120) }
    func memory() async throws -> DonnaMemory { try await call("GET", "api/donna/memory") }

    private func call<T: Decodable>(_ method: String, _ path: String, body: [String: String]? = nil,
                                    timeout: TimeInterval = 30) async throws -> T {
        var request = URLRequest(url: baseURL.appending(path: path), timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 { throw DonnaAPIError.unauthorized }
        guard (200..<300).contains(code) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw DonnaAPIError.server(code, detail ?? "")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
