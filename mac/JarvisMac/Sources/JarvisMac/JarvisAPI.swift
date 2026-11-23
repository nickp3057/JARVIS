import Foundation

enum JarvisAPIError: LocalizedError {
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

/// Thin client over Core's HTTP API (app/server.py), authenticated with the
/// companion token.
struct JarvisAPI {
    let baseURL: URL
    let token: String

    /// agent.ROUND_BREAK: separates model rounds in a streamed reply.
    static let roundBreak: Character = "\u{1e}"

    @MainActor
    static func make(_ settings: MacSettings) throws -> JarvisAPI {
        guard let url = settings.baseURL, settings.isConfigured else { throw JarvisAPIError.notConfigured }
        return JarvisAPI(baseURL: url, token: settings.token.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    // MARK: Chat

    func sessions() async throws -> [ServerSession] {
        struct Envelope: Decodable { let sessions: [ServerSession] }
        let envelope: Envelope = try await get("sessions")
        return envelope.sessions
    }

    func messages(in sessionID: String) async throws -> [ServerMessage] {
        struct Envelope: Decodable { let messages: [ServerMessage] }
        let envelope: Envelope = try await get("sessions/\(sessionID)")
        return envelope.messages
    }

    func deleteSession(_ sessionID: String) async throws {
        _ = try await send("DELETE", "sessions/\(sessionID)")
    }

    /// `POST /chat/stream`: hands each decoded piece of text to `onText` as
    /// it arrives (round breaks included) and returns the session id.
    func streamChat(_ message: String, sessionID: String?,
                    onText: @MainActor @escaping (String) -> Void) async throws -> String? {
        struct Body: Encodable {
            let message: String
            let sessionID: String?
            enum CodingKeys: String, CodingKey { case message; case sessionID = "session_id" }
        }
        var request = makeRequest("POST", "chat/stream", timeout: 300)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("1", forHTTPHeaderField: "X-Jarvis-Stream-Segments")
        request.httpBody = try JSONEncoder().encode(Body(message: message, sessionID: sessionID))

        let (bytes, response) = try await Self.reporting { try await MacCertificateDelegate.session.bytes(for: request) }
        let http = response as? HTTPURLResponse
        let code = http?.statusCode ?? 0
        if code == 401 { throw JarvisAPIError.unauthorized }
        guard (200..<300).contains(code) else {
            var data = Data()
            for try await byte in bytes { data.append(byte) }
            throw JarvisAPIError.server(code, Self.detail(data))
        }
        // Text is handed over on a 40 ms clock, not when the next byte lands:
        // Core often gets a whole round in one burst, and a byte-driven check
        // showed the first word and held the rest until the stream closed.
        let buffer = StreamTextBuffer()
        let flusher = Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(40))
                if let text = buffer.take() { await onText(text) }
            }
        }
        do {
            for try await byte in bytes { buffer.append(byte) }
        } catch {
            flusher.cancel()
            throw error
        }
        flusher.cancel()
        await flusher.value
        if let text = buffer.take(final: true) { await onText(text) }
        return http?.value(forHTTPHeaderField: "X-Jarvis-Session-ID")
    }

    // MARK: Home

    func homeEntities() async throws -> HomeEntities { try await get("api/home/entities") }
    func homeScenes() async throws -> HomeScenes { try await get("api/home/scenes") }

    func activateScene(_ entityID: String) async throws -> String {
        let result: ErrorField = try await post("api/home/scenes/activate",
                                                body: ["entity_id": AnyEncodable(entityID), "toggle": AnyEncodable(true)])
        return result.error ?? ""
    }

    func setPower(_ entityID: String, on: Bool) async throws -> String {
        let result: ErrorField = try await post("api/home/control",
                                                body: ["entity_id": AnyEncodable(entityID), "action": AnyEncodable(on ? "on" : "off")])
        return result.error ?? ""
    }

    func setBrightness(_ entityID: String, percent: Int) async throws -> String {
        let result: ErrorField = try await post("api/home/light",
                                                body: ["entity_id": AnyEncodable(entityID), "brightness_pct": AnyEncodable(percent)])
        return result.error ?? ""
    }

    // MARK: Alerts

    func alerts() async throws -> [JarvisAlert] {
        struct Envelope: Decodable { let alerts: [JarvisAlert] }
        let envelope: Envelope = try await get("api/alerts/recent", query: [URLQueryItem(name: "limit", value: "50")])
        return envelope.alerts
    }

    func refreshAlerts() async throws { _ = try await send("POST", "api/alerts/refresh", timeout: 60) }
    func clearAlerts() async throws { _ = try await send("POST", "api/alerts/clear") }

    func fixAlert(_ alert: JarvisAlert) async throws -> AlertFixResult {
        try await post("api/alerts/\(alert.id)/fix", body: ["created_at": AnyEncodable(alert.createdAt)], timeout: 60)
    }

    func dismissAlert(_ alert: JarvisAlert) async throws {
        try await send("POST", "api/alerts/\(alert.id)/dismiss",
                       body: try JSONEncoder().encode(["created_at": alert.createdAt]))
    }

    func autopilot() async throws -> AutopilotStatus { try await get("api/autopilot") }

    func setAutopilot(_ enabled: Bool) async throws -> AutopilotStatus {
        try await post("api/autopilot", body: ["enabled": AnyEncodable(enabled)])
    }

    // MARK: Donna

    func donnaState() async throws -> DonnaState { try await get("api/donna") }
    func donnaSend(_ text: String) async throws -> DonnaReply {
        try await post("api/donna/message", body: ["text": AnyEncodable(text)], timeout: 180)
    }
    func donnaEnd() async throws { _ = try await send("POST", "api/donna/end", timeout: 120) }
    func donnaMemory() async throws -> DonnaMemory { try await get("api/donna/memory") }

    // MARK: Plumbing

    /// An address that never answered: re-check home vs away for next time.
    private static func reporting<T>(_ body: () async throws -> T) async throws -> T {
        do {
            return try await body()
        } catch let error as URLError where [.cannotConnectToHost, .cannotFindHost, .timedOut, .secureConnectionFailed,
                                              .networkConnectionLost, .dnsLookupFailed].contains(error.code) {
            await MainActor.run { MacSettings.current?.refreshRoute() }
            throw error
        }
    }

    private func makeRequest(_ method: String, _ path: String, query: [URLQueryItem] = [],
                             timeout: TimeInterval = 30) -> URLRequest {
        var url = baseURL.appending(path: path)
        if !query.isEmpty { url.append(queryItems: query) }
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await send("GET", path, query: query))
    }

    func post<T: Decodable>(_ path: String, body: [String: AnyEncodable],
                                    timeout: TimeInterval = 30) async throws -> T {
        let data = try await send("POST", path, body: try JSONEncoder().encode(body), timeout: timeout)
        return try JSONDecoder().decode(T.self, from: data)
    }

    @discardableResult
    func send(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil,
                      timeout: TimeInterval = 30) async throws -> Data {
        var request = makeRequest(method, path, query: query, timeout: timeout)
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        }
        let (data, response) = try await Self.reporting { try await MacCertificateDelegate.session.data(for: request) }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 { throw JarvisAPIError.unauthorized }
        guard (200..<300).contains(code) else { throw JarvisAPIError.server(code, Self.detail(data)) }
        return data
    }

    private static func detail(_ data: Data) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        return object["detail"] as? String ?? ""
    }
}

/// Lets a request body mix strings, numbers and booleans.
struct AnyEncodable: Encodable {
    private let encodeValue: (Encoder) throws -> Void
    init<T: Encodable>(_ value: T) { encodeValue = value.encode }
    func encode(to encoder: Encoder) throws { try encodeValue(encoder) }
}

/// A streamed reply's bytes, written by the reader and drained by a timer.
/// ``take()`` returns everything that decodes as UTF-8 so far; a character
/// split across network packets waits for its last byte.
final class StreamTextBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var pending = Data()

    func append(_ byte: UInt8) {
        lock.lock()
        pending.append(byte)
        lock.unlock()
    }

    func take(final: Bool = false) -> String? {
        lock.lock()
        defer { lock.unlock() }
        guard !pending.isEmpty else { return nil }
        for held in 0...min(3, pending.count - 1) {
            let end = pending.count - held
            if let text = String(data: pending.prefix(end), encoding: .utf8) {
                pending.removeFirst(end)
                return text
            }
        }
        if final {  // a broken tail: show what we can rather than drop it
            defer { pending.removeAll() }
            return String(decoding: pending, as: UTF8.self)
        }
        return nil
    }
}
