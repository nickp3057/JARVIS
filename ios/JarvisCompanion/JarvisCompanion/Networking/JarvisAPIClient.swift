import Foundation

/// Everything that can go wrong on the way to JARVIS Core, phrased for a human
/// staring at a phone rather than for a log file.
enum JarvisAPIError: LocalizedError {
    case notConfigured
    case invalidBaseURL(String)
    case unauthorized
    case server(status: Int, body: String?)
    case transport(URLError)
    case malformedResponse(String?)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "No server configured yet. Add your JARVIS URL and API token in Settings."

        case .invalidBaseURL(let text):
            return "\"\(text)\" isn't a usable server address. Try something like http://100.x.y.z:8000."

        case .unauthorized:
            return "JARVIS rejected the API token (401). Check the token in Settings."

        case .server(let status, let body):
            if status == 404, body?.contains("Not Found") == true {
                return "This JARVIS Core does not support that feature yet. Update and restart the server, then try again."
            }
            let detail = Self.snippet(body).map { ": \($0)" } ?? "."
            return "JARVIS returned an error (HTTP \(status))\(detail)"

        case .transport(let error):
            return Self.describe(error)

        case .malformedResponse(let body):
            let detail = Self.snippet(body).map { " Got: \($0)" } ?? ""
            return "Couldn't read JARVIS's reply — the response wasn't the expected JSON.\(detail)"
        }
    }

    /// URLError codes worth translating, with a nudge toward the usual
    /// suspects on this setup (Core down, or away access not set up).
    private static func describe(_ error: URLError) -> String {
        switch error.code {
        case .timedOut:
            return "JARVIS didn't respond in time. It may still be working through a long tool call — try again in a moment."
        case .cannotConnectToHost:
            return "Couldn't connect to JARVIS. Is the server running? Away from home, check Settings > Server > Away access."
        case .clientCertificateRequired, .clientCertificateRejected:
            return "Away access needs this device's certificate. Import it in Settings > Server > Away access."
        case .secureConnectionFailed:
            return "The secure connection to jarvis.nickolaspearson.com failed. Is this device's certificate imported (Settings > Server > Away access)?"
        case .cannotFindHost:
            return "Couldn't find that host. Double-check the server address in Settings."
        case .notConnectedToInternet:
            return "This device is offline."
        case .networkConnectionLost:
            return "The connection dropped mid-request. Try again."
        case .appTransportSecurityRequiresSecureConnection:
            return "iOS blocked the connection as insecure. A plain-http:// server needs the App Transport Security exception described in the README."
        default:
            return "Network error: \(error.localizedDescription)"
        }
    }

    /// Server error bodies can be a whole HTML page or a stack trace; show
    /// enough to identify the problem and no more.
    private static func snippet(_ body: String?) -> String? {
        guard let trimmed = body?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty
        else { return nil }

        let oneLine = trimmed.replacingOccurrences(of: "\n", with: " ")
        return oneLine.count > 200 ? String(oneLine.prefix(200)) + "…" : oneLine
    }
}

struct ServerSession: Decodable {
    let lastMessageID: Int?
    let sessionID: String
    let createdAt: String?
    let updatedAt: String?
    let title: String?
    let preview: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case lastMessageID = "last_message_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case title, preview
    }
}

struct ServerMessage: Decodable {
    let id: Int?
    let requestID: String?
    let role: String
    let content: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, role, content
        case requestID = "request_id"
        case createdAt = "created_at"
    }
}

/// One alert from JARVIS Core's alert bus (`GET /api/alerts/recent`).
struct JarvisAlert: Decodable, Identifiable, Hashable {
    let id: Int
    let kind: String
    let severity: String  // info | warning | critical
    let title: String
    let message: String
    let source: String
    let createdAt: Double
    /// How many times the same problem was raised (Core condenses them; 5.69+).
    var repeats: Int? = nil
    /// Core has a one-tap fix for it (the sheet's Fix it button; 6.97+).
    var fixable: Bool? = nil
    /// What JARVIS is doing about it: the newest repair tied to this alert,
    /// from autopilot or a Fix it tap (6.97+).
    var repair: AlertRepair? = nil

    var date: Date { Date(timeIntervalSince1970: createdAt) }

    enum CodingKeys: String, CodingKey {
        case id, kind, severity, title, message, source, repeats, fixable, repair
        case createdAt = "created_at"
    }
}

/// The repair working on one alert (`repair` in `GET /api/alerts/recent`).
struct AlertRepair: Decodable, Hashable {
    let id: String
    let label: String
    let status: String  // pending | running | applied | failed | rolled_back
    let result: String
    /// JARVIS ran it on his own (autopilot) rather than on a tap.
    let auto: Bool

    var isWorking: Bool { status == "running" }
    var succeeded: Bool { status == "applied" }
    var failed: Bool { status == "failed" || status == "rolled_back" }
}

/// `POST /api/alerts/{id}/fix`.
struct AlertFixResult: Decodable {
    let started: Bool
    let message: String
    var resolved: Bool? = nil
}

/// Something Nick asked for that JARVIS has no tool for yet (`GET /api/skills/requests`).
struct SkillRequest: Decodable, Identifiable, Hashable {
    let id: String
    let summary: String
    let asks: [String]
    let why: String
    let count: Int
    let lastAt: Double
    /// The skill drafter's outcome: a draft to approve, or what it'd take.
    var note: String? = nil
    var draftSkillID: String? = nil
    var drafting: Bool? = nil

    enum CodingKeys: String, CodingKey {
        case id, summary, asks, why, count, note, drafting
        case lastAt = "last_at"
        case draftSkillID = "draft_skill_id"
    }
}

/// A skill JARVIS taught himself from his own tools (`GET /api/skills`).
struct LearnedSkill: Decodable, Identifiable, Hashable {
    struct Step: Decodable, Hashable { let tool: String }
    struct Param: Decodable, Hashable { let name: String; let description: String }
    let id: String
    let name: String
    let description: String
    let status: String  // draft | active | disabled
    let steps: [Step]
    let params: [Param]
    var uses: Int? = nil

    var isDraft: Bool { status == "draft" }
    var title: String { name.replacingOccurrences(of: "_", with: " ").capitalized }
}

/// One film for the movie picker (`GET /api/plex/movies`).
struct PlexMovie: Decodable, Identifiable, Hashable {
    let ratingKey: String
    let title: String
    let year: Int?
    let thumb: String
    let durationMs: Int
    let viewOffsetMs: Int
    let watched: Bool
    let summary: String
    var contentRating: String? = nil

    var id: String { ratingKey }
    var progress: Double { durationMs > 0 ? Double(viewOffsetMs) / Double(durationMs) : 0 }
    var resumable: Bool { viewOffsetMs > 60_000 }

    enum CodingKeys: String, CodingKey {
        case title, year, thumb, watched, summary
        case ratingKey = "rating_key"
        case durationMs = "duration_ms"
        case viewOffsetMs = "view_offset_ms"
        case contentRating = "content_rating"
    }
}

/// A Plex library and whether the picker hides it (`GET /api/plex/libraries`).
struct PlexLibrary: Decodable, Identifiable, Hashable {
    let key: String
    let title: String
    let type: String
    let hidden: Bool
    var id: String { key }
}

/// One show for the picker (`GET /api/plex/shows`).
struct PlexShow: Decodable, Identifiable, Hashable {
    let ratingKey: String
    let title: String
    let year: Int?
    let thumb: String
    let episodes: Int
    let watchedEpisodes: Int
    let seasons: Int

    var id: String { ratingKey }
    var progress: Double { episodes > 0 ? Double(watchedEpisodes) / Double(episodes) : 0 }
    var allWatched: Bool { episodes > 0 && watchedEpisodes >= episodes }
    var countLine: String {
        let left = episodes - watchedEpisodes
        if episodes == 0 { return seasons == 1 ? "1 season" : "\(seasons) seasons" }
        return left <= 0 ? "Watched" : (watchedEpisodes == 0 ? "\(episodes) episodes" : "\(left) left")
    }

    enum CodingKeys: String, CodingKey {
        case title, year, thumb, episodes, seasons
        case ratingKey = "rating_key"
        case watchedEpisodes = "watched_episodes"
    }
}

struct PlexEpisode: Decodable, Identifiable, Hashable {
    let ratingKey: String
    let title: String
    let season: Int
    let episode: Int
    let durationMs: Int
    let viewOffsetMs: Int
    let watched: Bool

    var id: String { ratingKey }
    var progress: Double { durationMs > 0 ? Double(viewOffsetMs) / Double(durationMs) : 0 }
    var resumable: Bool { viewOffsetMs > 60_000 }
    var code: String { "S\(season) E\(episode)" }

    enum CodingKeys: String, CodingKey {
        case title, season, episode, watched
        case ratingKey = "rating_key"
        case durationMs = "duration_ms"
        case viewOffsetMs = "view_offset_ms"
    }
}

/// `GET /api/plex/shows/{key}/episodes`.
struct ShowEpisodes: Decodable, Hashable {
    struct Season: Decodable, Hashable {
        let season: Int
        let episodes: [PlexEpisode]
    }
    let nextUp: String?
    let seasons: [Season]

    enum CodingKeys: String, CodingKey {
        case seasons
        case nextUp = "next_up"
    }
}

/// `GET/POST /api/autopilot`: JARVIS fixing the basic stuff himself.
struct AutopilotStatus: Decodable, Hashable {
    let enabled: Bool
    let fixes: [String]
}

/// One self-repair proposal (`GET /api/repairs`). Pending ones get an
/// Approve button; the tap is the approval.
struct RepairProposal: Decodable, Identifiable, Hashable {
    let id: String
    let kind: String?
    let label: String
    let rationale: String
    let status: String  // pending | running | applied | failed | rolled_back | rejected
    let result: String
    /// "auto" when JARVIS ran it himself, "app"/"chat" when Nick approved (6.97+).
    var approvedBy: String? = nil

    var isPending: Bool { status == "pending" }
    var isAuto: Bool { approvedBy == "auto" }

    enum CodingKeys: String, CodingKey {
        case id, kind, label, rationale, status, result
        case approvedBy = "approved_by"
    }
}

/// Something JARVIS staged in this chat that waits for Nick: a Schwab order,
/// a fix, a scene (`GET /api/approvals`). The buttons send "approve <code>" or
/// "deny <code>" as a chat message, the same gate as typing it.
struct PendingApproval: Decodable, Identifiable, Hashable {
    let code: String
    let description: String
    let expiresIn: Int

    var id: String { code }

    enum CodingKeys: String, CodingKey {
        case code, description
        case expiresIn = "expires_in"
    }
}

/// `POST /api/alerts/{id}/report`: a stored incident report plus the chat
/// prompt that opens it.
struct IncidentReport: Decodable {
    let reportID: String
    let prompt: String

    enum CodingKeys: String, CodingKey {
        case prompt
        case reportID = "report_id"
    }
}

/// The media doctor's trace (`GET /api/status/media/diagnose`).
struct MediaDiagnosis: Decodable {
    struct Step: Decodable, Hashable {
        let stage: String
        let status: String  // ok | fail | pending | skip
        let detail: String
    }

    let stage: String
    let stalled: Bool
    let summary: String
    let steps: [Step]
}

struct MediaSearchResult: Decodable, Identifiable, Hashable {
    let kind: String
    let id: Int
    let title: String
    let year: Int?
    let inLibrary: Bool
    let overview: String
    let poster: String?

    enum CodingKeys: String, CodingKey {
        case kind, id, title, year, overview, poster
        case inLibrary = "in_library"
    }
}

struct MediaSearchResponse: Decodable {
    let results: [MediaSearchResult]
    let error: String?
}

/// `status`: needs_confirmation | added | failed | invalid | exists | expired.
struct MediaRequestResponse: Decodable {
    let status: String
    let code: String?
    let description: String?
    let message: String?
}

struct DebugReportMessagePayload: Encodable {
    let id: String
    let role: String
    let content: String
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, role, content
        case createdAt = "created_at"
    }
}

/// Thin wrapper over `POST {baseURL}/chat`.
///
/// This is the only type in the app that knows the wire format. JARVIS Core
/// does all the reasoning and tool-calling; the request is one string out and
/// the response is one string back.
struct JarvisAPIClient {
    /// Generous by design: a single turn can fan out into several server-side
    /// tool calls before Claude produces its final text.
    static let defaultTimeout: TimeInterval = 30
    static let streamingTimeout: TimeInterval = 3 * 60

    /// One session for the whole app. A `URLSession` retains itself until it is
    /// invalidated, so building one per request would leak; the per-request
    /// timeout is set on the `URLRequest` instead.
    /// Its delegate presents this device's certificate when Lightsail's
    /// Caddy asks for one (away access, `DeviceCertificate`).
    private static let sharedSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration, delegate: ClientCertificateDelegate.shared, delegateQueue: nil)
    }()

    /// A home/away switch: drop whatever is still talking to the old address
    /// (streams, sockets), so each reconnects on the new one.
    static func cancelRequests(to base: URL) {
        sharedSession.getAllTasks { tasks in
            for task in tasks {
                guard let url = task.originalRequest?.url, ServerRoute.sameServer(url, base) else { continue }
                task.cancel()
            }
        }
    }

    /// Errors that mean the address never answered (so even a POST never
    /// arrived): worth one try on the other route. Timeouts and dropped
    /// connections only count for reads.
    private static func unreachable(_ error: URLError, method: String?) -> Bool {
        switch error.code {
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed, .secureConnectionFailed,
             .clientCertificateRequired, .clientCertificateRejected:
            return true
        case .timedOut, .networkConnectionLost:
            return (method ?? "GET") == "GET"
        default:
            return false
        }
    }

    private static func rebase(_ request: URLRequest, onto base: URL) -> URLRequest? {
        guard let url = request.url, var parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let target = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        let socket = parts.scheme == "ws" || parts.scheme == "wss"
        parts.scheme = socket ? (target.scheme == "https" ? "wss" : "ws") : target.scheme
        parts.host = target.host
        parts.port = target.port
        var moved = request
        moved.url = parts.url
        return moved
    }

    /// `session.data`, with one retry on the other route (home <-> away)
    /// when this address can't be reached at all; the route flips with it.
    private func fetch(for request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch let error as URLError where Self.unreachable(error, method: request.httpMethod) {
            let base = baseURL
            guard let other = await MainActor.run(body: { ServerRoute.shared.failover(from: base) }),
                  let retried = Self.rebase(request, onto: other) else { throw error }
            return try await session.data(for: retried)
        }
    }

    /// `session.bytes` for streams: no retry here (the stream's loop
    /// reconnects), but an unreachable address flips the route first.
    private func openStream(for request: URLRequest) async throws -> (URLSession.AsyncBytes, URLResponse) {
        do {
            return try await session.bytes(for: request)
        } catch let error as URLError where Self.unreachable(error, method: "GET") {
            let base = baseURL
            _ = await MainActor.run { ServerRoute.shared.failover(from: base) }
            throw error
        }
    }

    private let session: URLSession
    private let baseURL: URL
    private let token: String
    private let timeout: TimeInterval

    init(baseURL: URL, token: String, timeout: TimeInterval = JarvisAPIClient.defaultTimeout, session: URLSession? = nil) {
        self.session = session ?? Self.sharedSession
        self.baseURL = baseURL
        self.token = token
        self.timeout = timeout
    }

    /// Builds a client from the user's settings, or throws the specific reason
    /// it couldn't.
    @MainActor
    static func make(from settings: AppSettings) throws -> JarvisAPIClient {
        let token = settings.apiToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard settings.hasAddress, !token.isEmpty else { throw JarvisAPIError.notConfigured }
        guard let url = settings.resolvedBaseURL else { throw JarvisAPIError.invalidBaseURL(settings.homeURLString) }

        return JarvisAPIClient(baseURL: url, token: token)
    }

    private struct ChatRequest: Encodable {
        let message: String
        let sessionID: String?
        let requestID: String?

        enum CodingKeys: String, CodingKey {
            case message
            case requestID = "request_id"
            case sessionID = "session_id"
        }
    }

    private struct ChatResponse: Decodable {
        let reply: String
        let sessionID: String?

        enum CodingKeys: String, CodingKey {
            case reply
            case sessionID = "session_id"
        }
    }

    private struct DebugReportRequest: Encodable {
        let issue: String
        let messages: [DebugReportMessagePayload]
        let sessionID: String?
        let conversationTitle: String
        let focusMessageID: String?
        let appContext: [String: String]

        enum CodingKeys: String, CodingKey {
            case issue, messages
            case sessionID = "session_id"
            case conversationTitle = "conversation_title"
            case focusMessageID = "focus_message_id"
            case appContext = "app_context"
        }
    }

    private struct DebugReportResponse: Decodable {
        let id: String
    }

    private func request(path: String, method: String = "GET") -> URLRequest {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // Which device is talking, for Core's link map (brain/traffic.py).
        request.setValue(Self.device, forHTTPHeaderField: "X-Jarvis-Device")
        return request
    }

    /// "ipad-nightstand" for the mini by the bed, so the link map gives it
    /// its own node. Read per request: Room settings can change the role.
    private static var device: String {
        if ProcessInfo.processInfo.isMacCatalystApp { return "mac" }
        // The hardware model ("iPad13,4") is readable off the main thread, unlike the idiom.
        let model = machine
        guard model.hasPrefix("iPad") else { return "iphone" }
        // PadRole's stored choice; "Automatic" means a mini (PadRole.automatic
        // goes by screen size, which needs the main actor; the model doesn't).
        let stored = UserDefaults.standard.string(forKey: PadRole.storageKey) ?? ""
        let nightstand = stored.isEmpty ? miniModels.contains(model) : stored == PadRole.nightstand.rawValue
        return nightstand ? "ipad-nightstand" : "ipad"
    }

    /// iPad mini hardware: 4th and 5th gen, 6th gen, A17 Pro.
    private static let miniModels: Set<String> = [
        "iPad5,1", "iPad5,2", "iPad11,1", "iPad11,2", "iPad14,1", "iPad14,2", "iPad16,1", "iPad16,2",
    ]

    /// `WS /api/voice/satellite?name=<bed|door>`: this iPad lends Core's room
    /// voice its mic and speaker (`SatelliteVoice`, Core's `speech/satellites.py`).
    func satelliteSocket(name: String) throws -> URLSessionWebSocketTask {
        var request = request(path: "api/voice/satellite")
        guard let url = request.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw JarvisAPIError.invalidBaseURL(baseURL.absoluteString)
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        components.queryItems = [URLQueryItem(name: "name", value: name)]
        guard let socketURL = components.url else { throw JarvisAPIError.invalidBaseURL(baseURL.absoluteString) }
        request.url = socketURL
        request.timeoutInterval = 120  // Core pings every 20 s
        let task = session.webSocketTask(with: request)
        task.maximumMessageSize = 4 * 1024 * 1024
        return task
    }

    @MainActor
    func liveVoiceConnection() throws -> LiveVoiceConnection {
        var request = request(path: "api/voice/live")
        guard let url = request.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw JarvisAPIError.invalidBaseURL(baseURL.absoluteString)
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        guard let socketURL = components.url else { throw JarvisAPIError.invalidBaseURL(baseURL.absoluteString) }
        request.url = socketURL
        request.timeoutInterval = 180
        return LiveVoiceConnection(socket: session.webSocketTask(with: request))
    }

    private static let machine: String = {
        var info = utsname()
        uname(&info)
        return withUnsafeBytes(of: &info.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }()

    private func validate(_ response: URLResponse, data: Data? = nil) throws -> HTTPURLResponse {
        guard let http = response as? HTTPURLResponse else {
            throw JarvisAPIError.malformedResponse(nil)
        }
        let body = data.flatMap { String(data: $0, encoding: .utf8) }
        switch http.statusCode {
        case 200..<300: return http
        case 401, 403: throw JarvisAPIError.unauthorized
        default: throw JarvisAPIError.server(status: http.statusCode, body: body)
        }
    }

    func send(message: String, sessionID: String?, requestID: String? = nil) async throws -> (reply: String, sessionID: String?) {
        var request = request(path: "chat", method: "POST")
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(ChatRequest(message: message, sessionID: sessionID, requestID: requestID))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: request)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }

        let body = String(data: data, encoding: .utf8)
        _ = try validate(response, data: data)

        guard let decoded = try? JSONDecoder().decode(ChatResponse.self, from: data) else {
            throw JarvisAPIError.malformedResponse(body)
        }

        return (decoded.reply, decoded.sessionID)
    }

    func stream(
        message: String,
        sessionID: String?,
        requestID: String? = nil,
        onSession: @escaping @MainActor (String) -> Void,
        onChunk: @escaping @MainActor (String) -> Void
    ) async throws -> String? {
        var request = request(path: "chat/stream", method: "POST")
        // Tool rounds can be silent while Radarr, Sonarr, HA, or another local
        // service is working. Do not mistake that silence for a dead request.
        request.timeoutInterval = max(timeout, Self.streamingTimeout)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/plain", forHTTPHeaderField: "Accept")
        // Ask the server to mark each model round with U+001E so MessageBubble
        // can show them as separate bubbles. Older servers ignore it.
        request.setValue("1", forHTTPHeaderField: "X-Jarvis-Stream-Segments")
        request.httpBody = try JSONEncoder().encode(ChatRequest(message: message, sessionID: sessionID, requestID: requestID))

        do {
            let (bytes, response) = try await openStream(for: request)
            let http = try validate(response)
            if let boundSessionID = http.value(forHTTPHeaderField: "X-Jarvis-Session-ID") {
                await onSession(boundSessionID)
            }
            // Delivery runs on its own 40 ms clock, not on byte arrival:
            // Core often gets a whole round from Claude in one burst, and a
            // check made only when the next byte lands showed the first word,
            // then held the rest until the stream closed (after tool rounds
            // and history saving), so the paragraph appeared all at once.
            let buffer = StreamTextBuffer()
            let flusher = Task {
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(40))
                    if let text = buffer.take() { await onChunk(text) }
                }
            }
            do {
                for try await byte in bytes {
                    try Task.checkCancellation()
                    buffer.append(byte)
                }
            } catch {
                flusher.cancel()
                throw error
            }
            flusher.cancel()
            await flusher.value  // the flusher's last chunk lands before the tail
            if let text = buffer.take(final: true) { await onChunk(text) }
            return http.value(forHTTPHeaderField: "X-Jarvis-Session-ID")
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
    }

    /// The room voice's live state (`GET /api/voice/stream`, server-sent
    /// events): calls `onState` for every change until cancelled or dropped.
    func streamVoice(onState: @escaping @MainActor (VoiceState) -> Void) async throws {
        var request = request(path: "api/voice/stream")
        request.timeoutInterval = 60  // Core sends a keep-alive every 15 s
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        do {
            let (bytes, response) = try await openStream(for: request)
            _ = try validate(response)
            for try await line in bytes.lines {
                try Task.checkCancellation()
                guard line.hasPrefix("data: "), let data = line.dropFirst(6).data(using: .utf8),
                      let state = try? JSONDecoder().decode(VoiceState.self, from: data) else { continue }
                // Teach JARVIS my voice: the line to read next (nil when not enrolling).
                struct Enroll: Decodable { let enroll: VoiceEnrollState? }
                let enroll = (try? JSONDecoder().decode(Enroll.self, from: data))?.enroll
                await VoiceEnrollment.shared.follow(enroll: enroll, reply: state.reply)
                await onState(state)
            }
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
    }

    /// A tap on the room conversation (`POST /api/voice/hush`): the room
    /// voice stops talking and listening and waits for "Hey JARVIS" again.
    @discardableResult
    func hushVoice() async throws -> Bool {
        struct Hushed: Decodable { let hushed: Bool }
        return try await postEmpty(Hushed.self, path: "api/voice/hush").hushed
    }

    /// Home Assistant changes (`GET /api/home/stream`, server-sent events):
    /// calls `onChange` with Core's change counter for each `home` event.
    /// `onChange(seq, theme)`: every pulse carries the shared theme too.
    /// `look` moves with any theme pick or Experimenting switch on Core.
    func streamHome(onChange: @escaping @MainActor (Int, String?, Double?, Bool?, Int?) -> Void) async throws {
        // `camera` (is the iPad's lens wanted) and `lightsync` (light sync's
        // revision) are nil from a Core that predates them.
        struct Pulse: Decodable { let seq: Int; let theme: String?; let look: Double?; let camera: Bool?; let lightsync: Int? }
        var request = request(path: "api/home/stream")
        request.timeoutInterval = 60  // Core sends a keep-alive every 15 s
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        do {
            let (bytes, response) = try await openStream(for: request)
            _ = try validate(response)
            for try await line in bytes.lines {
                try Task.checkCancellation()
                guard line.hasPrefix("data: "), let data = line.dropFirst(6).data(using: .utf8),
                      let pulse = try? JSONDecoder().decode(Pulse.self, from: data) else { continue }
                await onChange(pulse.seq, pulse.theme, pulse.look, pulse.camera, pulse.lightsync)
            }
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
    }

    /// Answer screens (`GET /api/clients/events?only=cards`, server-sent
    /// events): calls `onCard` for each card JARVIS puts up, including any
    /// still on screen when the stream connects. Runs until cancelled or dropped.
    func streamCards(onCard: @escaping @MainActor (AnswerCard) -> Void) async throws {
        var request = request(path: "api/clients/events")
        if let url = request.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.queryItems = [URLQueryItem(name: "only", value: "cards")]
            request.url = components.url
        }
        request.timeoutInterval = 60  // Core sends a keep-alive every 15 s
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        do {
            let (bytes, response) = try await openStream(for: request)
            _ = try validate(response)
            for try await line in bytes.lines {
                try Task.checkCancellation()
                // With only=cards every data line is a card.
                guard line.hasPrefix("data: "), let data = line.dropFirst(6).data(using: .utf8),
                      let card = try? JSONDecoder().decode(AnswerCard.self, from: data) else { continue }
                await onCard(card)
            }
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
    }

    func checkHealth() async throws {
        let request = request(path: "health")
        let (data, response) = try await fetch(for: request)
        _ = try validate(response, data: data)
    }

    func createSession() async throws -> String {
        struct Created: Decodable {
            let session_id: String
        }
        return try await postJSON(Created.self, path: "sessions", body: [String: String]()).session_id
    }

    func renameSession(_ sessionID: String, title: String) async throws {
        var request = request(path: "sessions/\(sessionID)", method: "PATCH")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["title": title])
        let (data, response) = try await fetch(for: request)
        _ = try validate(response, data: data)
    }

    func deleteSession(_ sessionID: String) async throws {
        let request = request(path: "sessions/\(sessionID)", method: "DELETE")
        let (data, response) = try await fetch(for: request)
        _ = try validate(response, data: data)
    }

    func sessions() async throws -> [ServerSession] {
        struct Envelope: Decodable { let sessions: [ServerSession] }
        let request = request(path: "sessions")
        let (data, response) = try await fetch(for: request)
        _ = try validate(response, data: data)
        return try JSONDecoder().decode(Envelope.self, from: data).sessions
    }

    func messages(for sessionID: String) async throws -> [ServerMessage] {
        struct Envelope: Decodable { let messages: [ServerMessage] }
        let request = request(path: "sessions/\(sessionID)")
        let (data, response) = try await fetch(for: request)
        _ = try validate(response, data: data)
        return try JSONDecoder().decode(Envelope.self, from: data).messages
    }

    // MARK: - Dashboard hub

    /// Shared GET-and-decode for the read-only dashboard endpoints. They all
    /// return 200 with an `error` field when a backing service is down, so a
    /// thrown error here means the request itself failed, not the service.
    private func getJSON<T: Decodable>(_ type: T.Type, path: String, query: [URLQueryItem] = []) async throws -> T {
        var request = request(path: path)
        if !query.isEmpty, let url = request.url,
           var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.queryItems = query
            request.url = components.url
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: request)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        _ = try validate(response, data: data)
        guard let decoded = try? JSONDecoder().decode(type, from: data) else {
            throw JarvisAPIError.malformedResponse(String(data: data, encoding: .utf8))
        }
        return decoded
    }

    private func postJSON<Body: Encodable, T: Decodable>(
        _ type: T.Type, path: String, body: Body
    ) async throws -> T {
        try await sendJSON(type, path: path, method: "POST", body: body)
    }

    private func sendJSON<Body: Encodable, T: Decodable>(
        _ type: T.Type, path: String, method: String, body: Body
    ) async throws -> T {
        var request = request(path: path, method: method)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: request)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        _ = try validate(response, data: data)
        guard let decoded = try? JSONDecoder().decode(type, from: data) else {
            throw JarvisAPIError.malformedResponse(String(data: data, encoding: .utf8))
        }
        return decoded
    }

    // MARK: - Push

    private struct PushRegisterRequest: Encodable {
        let deviceToken: String
        let environment: String
        let deviceName: String
        enum CodingKeys: String, CodingKey {
            case deviceToken = "device_token", environment, deviceName = "device_name"
        }
    }

    private struct PushRegisterResponse: Decodable { let registered: Bool }

    /// Hand JARVIS Core this phone's APNs token so alerts arrive as pushes.
    func registerPushDevice(token: String, environment: String, name: String) async throws {
        _ = try await postJSON(PushRegisterResponse.self, path: "api/push/register",
                               body: PushRegisterRequest(deviceToken: token, environment: environment,
                                                         deviceName: String(name.prefix(80))))
    }

    // MARK: - Presence (the iPhone's home geofence; see PresenceGeofence)

    struct HomeZone: Codable, Equatable {
        let latitude: Double
        let longitude: Double
        let radius: Double
    }

    private struct HomeZoneResponse: Decodable { let zone: HomeZone }
    private struct PhonePresenceBody: Encodable { let `where`: String }
    private struct PhonePresenceResponse: Decodable { let ok: Bool }

    /// The circle around home to watch (Home Assistant's zone.home).
    func homeZone() async throws -> HomeZone {
        try await getJSON(HomeZoneResponse.self, path: "api/presence/zone").zone
    }

    /// The phone just left ("away") or reached ("home") the home circle.
    func reportPresence(_ place: String) async throws {
        _ = try await postJSON(PhonePresenceResponse.self, path: "api/presence/phone",
                               body: PhonePresenceBody(where: place))
    }

    // MARK: - Media requests (search -> request -> confirm; see MediaRequestView)

    private struct MediaRequestBody: Encodable {
        let kind: String
        let id: Int
        let quality: String?
    }

    private struct MediaConfirmBody: Encodable { let code: String }

    func mediaSearch(query: String, kind: String) async throws -> MediaSearchResponse {
        try await getJSON(MediaSearchResponse.self, path: "api/media/search",
                          query: [URLQueryItem(name: "q", value: query), URLQueryItem(name: "kind", value: kind)])
    }

    /// Step one: the server opens a proposal and writes nothing.
    func requestMedia(kind: String, id: Int, quality: String? = nil) async throws -> MediaRequestResponse {
        try await postJSON(MediaRequestResponse.self, path: "api/media/requests",
                           body: MediaRequestBody(kind: kind, id: id, quality: quality))
    }

    /// Step two: approve that proposal with its code.
    func confirmMediaRequest(code: String) async throws -> MediaRequestResponse {
        try await postJSON(MediaRequestResponse.self, path: "api/media/requests/confirm",
                           body: MediaConfirmBody(code: code))
    }

    func diagnoseMedia(title: String, kind: String) async throws -> MediaDiagnosis {
        try await getJSON(MediaDiagnosis.self, path: "api/status/media/diagnose",
                          query: [URLQueryItem(name: "title", value: title), URLQueryItem(name: "kind", value: kind)])
    }

    func recentAlerts() async throws -> [JarvisAlert] {
        struct Envelope: Decodable { let alerts: [JarvisAlert] }
        return try await getJSON(Envelope.self, path: "api/alerts/recent").alerts
    }

    private struct IncidentReportBody: Encodable {
        let createdAt: Double

        enum CodingKeys: String, CodingKey {
            case createdAt = "created_at"
        }
    }

    /// Gathers logs, health and recent changes for one alert on the server.
    /// `created_at` lets the server reject an id from before its restart.
    func incidentReport(for alert: JarvisAlert) async throws -> IncidentReport {
        try await postJSON(IncidentReport.self, path: "api/alerts/\(alert.id)/report",
                           body: IncidentReportBody(createdAt: alert.createdAt))
    }

    /// The alert sheet's Fix it button: the tap is the approval.
    func fixAlert(_ alert: JarvisAlert) async throws -> AlertFixResult {
        try await postJSON(AlertFixResult.self, path: "api/alerts/\(alert.id)/fix",
                           body: IncidentReportBody(createdAt: alert.createdAt))
    }

    /// Hides one alert (and its repeats) from Needs attention.
    func dismissAlert(_ alert: JarvisAlert) async throws {
        struct Result: Decodable { let dismissed: Int }
        _ = try await postJSON(Result.self, path: "api/alerts/\(alert.id)/dismiss",
                               body: IncidentReportBody(createdAt: alert.createdAt))
    }

    func skillRequests() async throws -> [SkillRequest] {
        struct Envelope: Decodable { let requests: [SkillRequest] }
        return try await getJSON(Envelope.self, path: "api/skills/requests").requests
    }

    /// "done" (JARVIS can do it now) or "declined" (not needed).
    func closeSkillRequest(id: String, status: String) async throws {
        struct Body: Encodable { let status: String }
        struct Result: Decodable {}
        _ = try await postJSON(Result.self, path: "api/skills/requests/\(id)", body: Body(status: status))
    }

    /// The "You left lights on" push's button.
    func lightsOffLeftOn() async throws -> String {
        struct Result: Decodable { let message: String }
        struct Empty: Encodable {}
        return try await postJSON(Result.self, path: "api/presence/lights-off", body: Empty()).message
    }

    func draftSkill(requestID: String) async throws -> String {
        struct Result: Decodable { let message: String }
        struct Empty: Encodable {}
        return try await postJSON(Result.self, path: "api/skills/requests/\(requestID)/draft", body: Empty()).message
    }

    func learnedSkills() async throws -> [LearnedSkill] {
        struct Envelope: Decodable { let skills: [LearnedSkill] }
        return try await getJSON(Envelope.self, path: "api/skills").skills
    }

    /// "active" (approve), "disabled" or "deleted". The tap is the approval.
    func setSkill(id: String, status: String) async throws {
        struct Body: Encodable { let status: String }
        struct Result: Decodable {}
        _ = try await postJSON(Result.self, path: "api/skills/\(id)", body: Body(status: status))
    }

    func autopilot() async throws -> AutopilotStatus {
        try await getJSON(AutopilotStatus.self, path: "api/autopilot")
    }

    func setAutopilot(_ enabled: Bool) async throws -> AutopilotStatus {
        struct Body: Encodable { let enabled: Bool }
        return try await postJSON(AutopilotStatus.self, path: "api/autopilot", body: Body(enabled: enabled))
    }

    func approvals(sessionID: String) async throws -> [PendingApproval] {
        struct Envelope: Decodable { let approvals: [PendingApproval] }
        return try await getJSON(Envelope.self, path: "api/approvals",
                                 query: [URLQueryItem(name: "session_id", value: sessionID)]).approvals
    }

    func repairs() async throws -> [RepairProposal] {
        struct Envelope: Decodable { let repairs: [RepairProposal] }
        return try await getJSON(Envelope.self, path: "api/repairs").repairs
    }

    private struct EmptyBody: Encodable {}
    struct RepairActionResult: Decodable {
        let message: String
        let status: String?
    }

    /// `action` is "apply" or "reject". The server runs stack fixes in the
    /// background and reports the verified outcome as an alert.
    func repairAction(id: String, action: String) async throws -> RepairActionResult {
        try await postJSON(RepairActionResult.self, path: "api/repairs/\(id)/\(action)", body: EmptyBody())
    }

    /// Re-checks the Needs attention list on Core: fixed alerts drop off,
    /// the rest stay.
    func recheckAlerts() async throws {
        struct Result: Decodable { let resolved: Int; let remaining: Int }
        _ = try await postJSON(Result.self, path: "api/alerts/refresh", body: EmptyBody())
    }

    // MARK: - To-dos (open reminders; done deletes)

    private struct TodoBody: Encodable { let text: String }

    func addTodo(_ text: String) async throws -> NowReminder {
        struct Envelope: Decodable { let todo: NowReminder }
        return try await postJSON(Envelope.self, path: "api/todos", body: TodoBody(text: text)).todo
    }

    func renameTodo(id: String, text: String) async throws -> NowReminder {
        struct Envelope: Decodable { let todo: NowReminder }
        return try await sendJSON(Envelope.self, path: "api/todos/\(id)", method: "PUT", body: TodoBody(text: text)).todo
    }

    func completeTodo(id: String) async throws {
        var request = request(path: "api/todos/\(id)/done", method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{}".utf8)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: request)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        _ = try validate(response, data: data)
    }

    // MARK: - TV remote

    // MARK: - Room camera

    /// The phone, while the live view is open: keep the lens on and read
    /// whether it's actually viewing (false during a kill lockout).
    @discardableResult
    func cameraHeartbeat() async throws -> CameraStatus {
        try await postEmpty(CameraStatus.self, path: "api/camera/view")
    }

    func cameraStatus() async throws -> CameraStatus {
        try await getJSON(CameraStatus.self, path: "api/camera/status")
    }

    /// The phone closed the live view: the lens stops now, not after the TTL.
    @discardableResult
    func cameraLeave() async throws -> CameraStatus {
        try await postEmpty(CameraStatus.self, path: "api/camera/leave")
    }

    /// The wall iPad's battery, so Core can warn the phone when it runs low.
    func reportBattery(device: String, level: Float, charging: Bool) async throws {
        struct Body: Encodable { let device: String; let level: Float; let charging: Bool }
        struct Reply: Decodable { let ok: Bool }
        _ = try await postJSON(Reply.self, path: "api/device/battery",
                               body: Body(device: device, level: level, charging: charging))
    }

    /// Lock the lens off for five minutes, from the phone's Kill button.
    @discardableResult
    func cameraKill() async throws -> CameraStatus {
        try await postEmpty(CameraStatus.self, path: "api/camera/kill")
    }

    /// `api/camera/frame?lens=<lens>`: each iPad is its own angle (Core's
    /// `brain/camera.py`), `door` for the iPad Air, `bed` for the mini.
    private func cameraFrameRequest(lens: String?, method: String = "GET") -> URLRequest {
        var r = request(path: "api/camera/frame", method: method)
        if let lens, let url = r.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.queryItems = [URLQueryItem(name: "lens", value: lens)]
            r.url = components.url ?? url
        }
        return r
    }

    /// An iPad uploads one JPEG frame (raw body) as its lens. Throws on a kill (409).
    func putCameraFrame(_ jpeg: Data, lens: String) async throws {
        var r = cameraFrameRequest(lens: lens, method: "POST")
        r.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        r.httpBody = jpeg
        let (data, response) = try await session.upload(for: r, from: jpeg)
        _ = try validate(response, data: data)
    }

    /// The phone pulls one angle's newest frame (nil = the door's, else any);
    /// nil when that lens isn't sending (404).
    func cameraFrame(lens: String? = nil) async throws -> Data? {
        let r = cameraFrameRequest(lens: lens)
        let (data, response) = try await fetch(for: r)
        guard let http = response as? HTTPURLResponse else { throw JarvisAPIError.malformedResponse(nil) }
        if http.statusCode == 404 { return nil }
        _ = try validate(response, data: data)
        return data
    }

    private func postEmpty<T: Decodable>(_ type: T.Type, path: String) async throws -> T {
        let r = request(path: path, method: "POST")
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: r)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        _ = try validate(response, data: data)
        guard let decoded = try? JSONDecoder().decode(type, from: data) else {
            throw JarvisAPIError.malformedResponse(String(data: data, encoding: .utf8))
        }
        return decoded
    }

    /// Whether the room TV is on (false when it's off or unreachable).
    func tvPowerState() async throws -> Bool {
        struct State: Decodable { let on: Bool }
        return try await getJSON(State.self, path: "api/tv/power").on
    }

    func pressTVRemote(_ key: String) async throws {
        struct Body: Encodable { let key: String }
        struct Done: Decodable { let ok: Bool }
        _ = try await postJSON(Done.self, path: "api/tv/remote", body: Body(key: key))
    }

    // MARK: - Charles Schwab (read-only)

    func portfolio() async throws -> Portfolio {
        try await getJSON(Portfolio.self, path: "api/portfolio")
    }

    /// JARVIS and the agents working for him (Core's brain/agent_roster.py).
    func agentUsage(days: Int) async throws -> AgentUsage {
        try await getJSON(AgentUsage.self, path: "api/agents/usage",
                          query: [URLQueryItem(name: "days", value: String(days))])
    }

    func agents() async throws -> AgentRoster {
        try await getJSON(AgentRoster.self, path: "api/agents")
    }

    /// Schwab's login page and the callback address to catch when Schwab
    /// redirects there (nothing listens at it; the app hands it to Core).
    /// `external`: the login lives on the Schwab hub (JARVIS borrows it), so the
    /// page opens in Safari instead of the in-app web view.
    func schwabLogin() async throws -> (url: URL, callback: String, external: Bool) {
        struct Envelope: Decodable { let url: URL; let callback: String; let external: Bool? }
        let body = try await postJSON(Envelope.self, path: "api/schwab/login", body: EmptyBody())
        return (body.url, body.callback, body.external ?? false)
    }

    func finishSchwabLogin(redirect: URL) async throws {
        struct Body: Encodable {
            let redirectURL: String
            enum CodingKeys: String, CodingKey { case redirectURL = "redirect_url" }
        }
        struct Done: Decodable { let linked: Bool }
        _ = try await postJSON(Done.self, path: "api/schwab/finish", body: Body(redirectURL: redirect.absoluteString))
    }

    // MARK: - Speaker sync (desk vs iPads)

    private struct SyncReply: Decodable {
        let syncMs: Int
        enum CodingKeys: String, CodingKey { case syncMs = "sync_ms" }
    }

    /// How long the desk speaker waits for the iPads, in ms (< 0: the iPads wait).
    func speakerSync() async throws -> Int {
        try await getJSON(SyncReply.self, path: "api/voice/satellites").syncMs
    }

    /// Desk later (+) or earlier (-) by `nudge` ms; `reset` goes back to Core's default.
    func nudgeSpeakerSync(_ nudge: Int, reset: Bool = false) async throws -> Int {
        struct Body: Encodable {
            let nudgeMs: Int
            let reset: Bool
            enum CodingKeys: String, CodingKey { case nudgeMs = "nudge_ms", reset }
        }
        return try await postJSON(SyncReply.self, path: "api/voice/satellites/sync",
                                  body: Body(nudgeMs: nudge, reset: reset)).syncMs
    }

    struct VoiceEnrollReply: Decodable {
        let ok: Bool
        let detail: String?
        let enroll: VoiceEnrollState?
    }

    /// Teach JARVIS my voice (`POST /api/voice/enroll`): `start` on `mic`
    /// ("desk", "bed", "door"), or `cancel`.
    func voiceEnroll(action: String, mic: String) async throws -> VoiceEnrollReply {
        struct Body: Encodable { let action: String; let mic: String }
        return try await postJSON(VoiceEnrollReply.self, path: "api/voice/enroll", body: Body(action: action, mic: mic))
    }

    // MARK: - Wake-up schedule and shared theme

    func wakeSchedule() async throws -> WakeSchedule {
        try await getJSON(WakeSchedule.self, path: "api/wake")
    }

    func snoozeWakeUp() async throws {
        struct Until: Decodable { let until: Double }
        _ = try await postJSON(Until.self, path: "api/wake/snooze", body: EmptyBody())
    }

    /// I'm up. `lights: false` is "I'm awake, leave the lights": the alarm
    /// stops and the room stays exactly as it is.
    func wakeUpAwake(lights: Bool = true) async throws {
        struct Body: Encodable { let lights: Bool }
        struct Done: Decodable { let ok: Bool }
        _ = try await postJSON(Done.self, path: "api/wake/awake", body: Body(lights: lights))
    }

    func saveWakeSchedule(_ draft: WakeScheduleDraft) async throws -> WakeSchedule {
        try await sendJSON(WakeSchedule.self, path: "api/wake", method: "PUT", body: draft)
    }

    struct SharedTheme: Codable {
        let theme: String
        var chosen: Bool? = nil
        /// Experimenting theme switches (`Experiment` raw values); absent on older Cores.
        var experiments: [String: Bool]? = nil
    }

    /// The colour theme Core shares with the TV; `chosen` is false until
    /// someone has picked one (Core is then only reporting its default).
    func sharedTheme() async throws -> SharedTheme {
        try await getJSON(SharedTheme.self, path: "api/theme")
    }

    func saveSharedTheme(_ theme: String) async throws {
        _ = try await sendJSON(SharedTheme.self, path: "api/theme", method: "PUT", body: SharedTheme(theme: theme))
    }

    /// Share Experimenting switches with Nick's other devices.
    func saveExperiments(_ experiments: [String: Bool]) async throws {
        struct Body: Encodable { let experiments: [String: Bool] }
        _ = try await sendJSON(SharedTheme.self, path: "api/theme", method: "PUT", body: Body(experiments: experiments))
    }

    // MARK: - Money advisor (Settings > Money)

    func moneyStatus() async throws -> MoneyStatus {
        try await getJSON(MoneyStatus.self, path: "api/money")
    }

    func saveMoneySettings(_ settings: MoneySettings) async throws -> MoneyStatus {
        try await sendJSON(MoneyStatus.self, path: "api/money/settings", method: "PUT", body: settings)
    }

    // MARK: - Donna (private counsellor)

    /// Donna's replies can include a lookup on a trusted site; give her room.
    private var patient: JarvisAPIClient {
        JarvisAPIClient(baseURL: baseURL, token: token, timeout: 120, session: session)
    }

    func donnaState() async throws -> DonnaState {
        try await getJSON(DonnaState.self, path: "api/donna")
    }

    func donnaSend(_ text: String) async throws -> DonnaReply {
        struct Body: Encodable { let text: String }
        return try await patient.postJSON(DonnaReply.self, path: "api/donna/message", body: Body(text: text))
    }

    func donnaEnd() async throws {
        struct Ended: Decodable { let ended: Bool }
        _ = try await patient.postEmpty(Ended.self, path: "api/donna/end")
    }

    func donnaImport(_ text: String) async throws -> String {
        struct Body: Encodable { let text: String }
        struct Done: Decodable { let caseFile: String; enum CodingKeys: String, CodingKey { case caseFile = "case_file" } }
        return try await JarvisAPIClient(baseURL: baseURL, token: token, timeout: 300, session: session)
            .postJSON(Done.self, path: "api/donna/import", body: Body(text: text)).caseFile
    }

    func donnaMemory() async throws -> DonnaMemory {
        try await getJSON(DonnaMemory.self, path: "api/donna/memory")
    }

    func donnaForget() async throws {
        try await delete(path: "api/donna/memory")
    }

    // MARK: - Shopping list

    private struct ShoppingEnvelope: Decodable { let item: ShoppingItem }

    func shopping() async throws -> ShoppingOverview {
        try await getJSON(ShoppingOverview.self, path: "api/shopping")
    }

    func createShoppingItem(_ draft: ShoppingItemDraft) async throws -> ShoppingItem {
        try await postJSON(ShoppingEnvelope.self, path: "api/shopping/items", body: draft).item
    }

    func updateShoppingItem(id: Int, _ draft: ShoppingItemDraft) async throws -> ShoppingItem {
        try await sendJSON(ShoppingEnvelope.self, path: "api/shopping/items/\(id)", method: "PUT", body: draft).item
    }

    func deleteShoppingItem(id: Int) async throws {
        try await delete(path: "api/shopping/items/\(id)")
    }

    func listShoppingItem(id: Int, quantity: Int? = nil) async throws -> ShoppingItem {
        struct Body: Encodable { let quantity: Int? }
        return try await postJSON(ShoppingEnvelope.self, path: "api/shopping/items/\(id)/list",
                                  body: Body(quantity: quantity)).item
    }

    func unlistShoppingItem(id: Int) async throws -> ShoppingItem {
        try await sendNoBody(ShoppingEnvelope.self, path: "api/shopping/items/\(id)/list", method: "DELETE").item
    }

    func boughtShoppingItem(id: Int) async throws -> ShoppingItem {
        try await sendNoBody(ShoppingEnvelope.self, path: "api/shopping/items/\(id)/bought", method: "POST").item
    }

    func undoBoughtShoppingItem(id: Int) async throws -> ShoppingItem {
        try await sendNoBody(ShoppingEnvelope.self, path: "api/shopping/items/\(id)/bought", method: "DELETE").item
    }

    func putShoppingPhoto(id: Int, jpeg: Data) async throws -> ShoppingItem {
        var r = request(path: "api/shopping/items/\(id)/photo", method: "PUT")
        // A JPEG photo, or a transparent PNG cut-out ("Remove background").
        r.setValue(jpeg.starts(with: [0x89, 0x50, 0x4E, 0x47]) ? "image/png" : "image/jpeg",
                   forHTTPHeaderField: "Content-Type")
        r.timeoutInterval = 60
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.upload(for: r, from: jpeg)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        _ = try validate(response, data: data)
        guard let decoded = try? JSONDecoder().decode(ShoppingEnvelope.self, from: data) else {
            throw JarvisAPIError.malformedResponse(String(data: data, encoding: .utf8))
        }
        return decoded.item
    }

    func clearShoppingPhoto(id: Int) async throws -> ShoppingItem {
        try await sendNoBody(ShoppingEnvelope.self, path: "api/shopping/items/\(id)/photo", method: "DELETE").item
    }

    // MARK: - Movie picker (Plex through Core)

    func plexMovies(query: String = "", sort: String = "recent") async throws -> [PlexMovie] {
        struct Envelope: Decodable { let movies: [PlexMovie] }
        var items = [URLQueryItem(name: "sort", value: sort)]
        if !query.isEmpty { items.append(URLQueryItem(name: "query", value: query)) }
        return try await getJSON(Envelope.self, path: "api/plex/movies", query: items).movies
    }

    /// A poster through Core (the Plex token never reaches the device).
    func plexPoster(path: String, width: Int) async throws -> Data? {
        var r = request(path: "api/plex/poster")
        if let url = r.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.queryItems = [URLQueryItem(name: "path", value: path), URLQueryItem(name: "width", value: String(width))]
            r.url = components.url
        }
        r.setValue("image/jpeg, image/png", forHTTPHeaderField: "Accept")
        r.cachePolicy = .returnCacheDataElseLoad
        let (data, response) = try await fetch(for: r)
        if (response as? HTTPURLResponse)?.statusCode == 404 { return nil }
        _ = try validate(response, data: data)
        return data
    }

    func plexLibraries() async throws -> [PlexLibrary] {
        struct Envelope: Decodable { let libraries: [PlexLibrary] }
        return try await getJSON(Envelope.self, path: "api/plex/libraries").libraries
    }

    /// Which libraries the picker hides (keys).
    func setHiddenLibraries(_ keys: [String]) async throws -> [PlexLibrary] {
        struct Body: Encodable { let hidden: [String] }
        struct Envelope: Decodable { let libraries: [PlexLibrary] }
        return try await postJSON(Envelope.self, path: "api/plex/libraries", body: Body(hidden: keys)).libraries
    }

    func plexShows(query: String = "", sort: String = "recent") async throws -> [PlexShow] {
        struct Envelope: Decodable { let shows: [PlexShow] }
        var items = [URLQueryItem(name: "sort", value: sort)]
        if !query.isEmpty { items.append(URLQueryItem(name: "query", value: query)) }
        return try await getJSON(Envelope.self, path: "api/plex/shows", query: items).shows
    }

    func showEpisodes(showKey: String) async throws -> ShowEpisodes {
        try await getJSON(ShowEpisodes.self, path: "api/plex/shows/\(showKey)/episodes")
    }

    /// A film or episode on the room TV (resumes unless `fromStart`).
    func playOnTV(ratingKey: String, fromStart: Bool) async throws -> String {
        struct Body: Encodable {
            let ratingKey: String
            let fromStart: Bool
            enum CodingKeys: String, CodingKey {
                case ratingKey = "rating_key"
                case fromStart = "from_start"
            }
        }
        struct Result: Decodable { let message: String }
        return try await postJSON(Result.self, path: "api/plex/play",
                                  body: Body(ratingKey: ratingKey, fromStart: fromStart)).message
    }

    /// A product photo; nil when it has none (404). `version` busts caches.
    func shoppingPhoto(id: Int, version: Int) async throws -> Data? {
        var r = request(path: "api/shopping/items/\(id)/photo")
        if let url = r.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.queryItems = [URLQueryItem(name: "v", value: String(version))]
            r.url = components.url
        }
        r.setValue("image/jpeg, image/png", forHTTPHeaderField: "Accept")
        let (data, response) = try await fetch(for: r)
        if (response as? HTTPURLResponse)?.statusCode == 404 { return nil }
        _ = try validate(response, data: data)
        return data
    }

    private func sendNoBody<T: Decodable>(_ type: T.Type, path: String, method: String) async throws -> T {
        let r = request(path: path, method: method)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: r)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        _ = try validate(response, data: data)
        guard let decoded = try? JSONDecoder().decode(type, from: data) else {
            throw JarvisAPIError.malformedResponse(String(data: data, encoding: .utf8))
        }
        return decoded
    }

    // MARK: - Maintenance

    private struct MaintenanceEnvelope: Decodable { let task: MaintenanceTask }

    func maintenanceTasks() async throws -> [MaintenanceTask] {
        struct Envelope: Decodable { let tasks: [MaintenanceTask] }
        return try await getJSON(Envelope.self, path: "api/maintenance").tasks
    }

    func createMaintenanceTask(_ draft: MaintenanceTaskDraft) async throws -> MaintenanceTask {
        try await postJSON(MaintenanceEnvelope.self, path: "api/maintenance", body: draft).task
    }

    func updateMaintenanceTask(id: Int, _ draft: MaintenanceTaskDraft) async throws -> MaintenanceTask {
        try await sendJSON(MaintenanceEnvelope.self, path: "api/maintenance/\(id)", method: "PUT", body: draft).task
    }

    private struct MaintenanceCompleteBody: Encodable {
        let note: String
        let completedOn: String?
        enum CodingKeys: String, CodingKey { case note, completedOn = "completed_on" }
    }

    /// `completedOn` (yyyy-MM-dd) backdates the completion; nil means now.
    func completeMaintenanceTask(id: Int, note: String, completedOn: String?) async throws -> MaintenanceTask {
        try await postJSON(MaintenanceEnvelope.self, path: "api/maintenance/\(id)/complete",
                           body: MaintenanceCompleteBody(note: note, completedOn: completedOn)).task
    }

    func maintenanceHistory(id: Int) async throws -> [MaintenanceLogEntry] {
        struct Envelope: Decodable { let history: [MaintenanceLogEntry] }
        return try await getJSON(Envelope.self, path: "api/maintenance/\(id)/history").history
    }

    func deleteMaintenanceTask(id: Int) async throws {
        try await delete(path: "api/maintenance/\(id)")
    }

    func deleteMaintenanceEntry(taskID: Int, entryID: Int) async throws {
        try await delete(path: "api/maintenance/\(taskID)/history/\(entryID)")
    }

    private func delete(path: String) async throws {
        let request = request(path: path, method: "DELETE")
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: request)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        _ = try validate(response, data: data)
    }

    func torrentsStatus() async throws -> TorrentsStatus {
        try await getJSON(TorrentsStatus.self, path: "api/status/torrents")
    }

    func nowStatus() async throws -> NowStatus {
        try await getJSON(NowStatus.self, path: "api/now")
    }

    func knowledgeNotes() async throws -> [KnowledgeNote] {
        struct Notes: Decodable { let notes: [KnowledgeNote] }
        return try await getJSON(Notes.self, path: "api/knowledge").notes
    }

    func knowledgeNote(_ path: String) async throws -> KnowledgeNoteText {
        try await getJSON(KnowledgeNoteText.self, path: "api/knowledge/note", query: [URLQueryItem(name: "path", value: path)])
    }

    /// Saves pasted text as a vault note under Jarvis Memory/Imported; returns its path.
    func importKnowledge(title: String, text: String) async throws -> String {
        struct Saved: Decodable { let path: String }
        return try await postJSON(Saved.self, path: "api/knowledge/import", body: ["title": title, "text": text]).path
    }

    func knowledgeGraph() async throws -> KnowledgeGraph {
        try await getJSON(KnowledgeGraph.self, path: "api/knowledge/graph")
    }

    /// Every signal between Core, the NAS, the TV, the phone, the iPad and the
    /// internet (`GET /api/traffic/stream`), a batch per event, until cancelled.
    func streamTraffic(onSignals: @escaping @MainActor ([TrafficSignal]) -> Void) async throws {
        var request = request(path: "api/traffic/stream")
        request.timeoutInterval = 60  // Core sends a keep-alive every 15 s
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        do {
            let (bytes, response) = try await openStream(for: request)
            _ = try validate(response)
            for try await line in bytes.lines {
                try Task.checkCancellation()
                guard line.hasPrefix("data: "), let data = line.dropFirst(6).data(using: .utf8),
                      let batch = try? JSONDecoder().decode([TrafficSignal].self, from: data) else { continue }
                await onSignals(batch)
            }
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
    }

    func glance() async throws -> Glance {
        try await getJSON(Glance.self, path: "api/glance")
    }

    func studyBrief() async throws -> StudyBrief {
        try await getJSON(StudyBrief.self, path: "api/study/brief")
    }

    func mediaStatus() async throws -> MediaStatus {
        try await getJSON(MediaStatus.self, path: "api/status/media")
    }

    func temp4kStatus() async throws -> Temp4kStatus {
        try await getJSON(Temp4kStatus.self, path: "api/media/temp4k")
    }

    /// Extend: `hours` more on a temporary 4K film's countdown.
    func extendTemp4k(_ tmdbID: Int, hours: Double) async throws {
        struct Body: Encodable { let tmdb_id: Int; let hours: Double }
        struct Result: Decodable {}
        _ = try await postJSON(Result.self, path: "api/media/temp4k/extend", body: Body(tmdb_id: tmdbID, hours: hours))
    }

    /// Remove now: the 4K copy goes, the 1080p one stays. Returns Core's error, or "".
    func removeTemp4k(_ tmdbID: Int) async throws -> String {
        struct Body: Encodable { let tmdb_id: Int; let hours: Double }
        struct Result: Decodable { let error: String }
        return try await postJSON(Result.self, path: "api/media/temp4k/remove", body: Body(tmdb_id: tmdbID, hours: 24)).error
    }

    func storageStatus() async throws -> StorageStatus {
        try await getJSON(StorageStatus.self, path: "api/status/storage")
    }

    func servicesStatus() async throws -> ServicesStatus {
        try await getJSON(ServicesStatus.self, path: "api/status/services")
    }

    func serviceLinks() async throws -> ServiceLinks {
        try await getJSON(ServiceLinks.self, path: "api/links")
    }

    func homeEntities() async throws -> HomeEntities {
        try await getJSON(HomeEntities.self, path: "api/home/entities")
    }

    private struct HomeControlRequest: Encodable {
        let entityID: String
        let action: String

        enum CodingKeys: String, CodingKey {
            case action
            case entityID = "entity_id"
        }
    }

    func setLight(_ entityID: String, brightness: Int?, rgb: [Int]?, kelvin: Int?,
                  rainbow: Bool = false) async throws -> String {
        struct Body: Encodable {
            let entityID: String
            let brightnessPct: Int?
            let rgb: [Int]?
            let kelvin: Int?
            let rainbow: Bool
            enum CodingKeys: String, CodingKey {
                case rgb, kelvin, rainbow
                case entityID = "entity_id"
                case brightnessPct = "brightness_pct"
            }
        }
        struct Result: Decodable { let error: String }
        return try await postJSON(Result.self, path: "api/home/light",
                                  body: Body(entityID: entityID, brightnessPct: brightness, rgb: rgb, kelvin: kelvin,
                                            rainbow: rainbow)).error
    }

    /// Speed (0 = off) and/or preset mode for an allowlisted fan.
    func setFan(_ entityID: String, percentage: Int?, presetMode: String? = nil) async throws -> String {
        struct Body: Encodable {
            let entityID: String
            let percentage: Int?
            let presetMode: String?
            enum CodingKeys: String, CodingKey {
                case percentage
                case entityID = "entity_id"
                case presetMode = "preset_mode"
            }
        }
        struct Result: Decodable { let error: String }
        return try await postJSON(Result.self, path: "api/home/fan",
                                  body: Body(entityID: entityID, percentage: percentage, presetMode: presetMode)).error
    }

    /// The room speaker's volume (JARVIS's voice and sounds on the Surface).
    /// The battery of the laptop Core runs on (Settings › Laptop).
    func hostBattery() async throws -> HostBattery {
        try await getJSON(HostBattery.self, path: "api/host/battery")
    }

    func roomVolume() async throws -> RoomVolume {
        try await getJSON(RoomVolume.self, path: "api/room/volume")
    }

    func setRoomVolume(_ level: Int) async throws -> RoomVolume {
        struct Body: Encodable { let level: Int }
        return try await postJSON(RoomVolume.self, path: "api/room/volume", body: Body(level: level))
    }

    /// The quiet-hours level, editable at any hour (Settings' Night volume).
    func setRoomNightVolume(_ level: Int) async throws -> RoomVolume {
        struct Body: Encodable { let level: Int }
        return try await postJSON(RoomVolume.self, path: "api/room/volume/night", body: Body(level: level))
    }

    // MARK: - Light sync

    func lightSync() async throws -> LightSyncState {
        try await getJSON(LightSyncState.self, path: "api/lightsync")
    }

    /// Any of: "start" / "stop", an intensity, the auto-in-movie-mode switch,
    /// a timing nudge (ms, positive = lights earlier; the film syncing, or
    /// global while calibrating), a timing reset, calibrate on (true) / Done (false).
    func setLightSync(action: String? = nil, intensity: String? = nil, auto: Bool? = nil,
                      nudgeMs: Int? = nil, resetOffset: Bool? = nil,
                      calibrate: Bool? = nil, lampNudgeMs: Int? = nil) async throws -> LightSyncState {
        struct Body: Encodable {
            let action: String?; let intensity: String?; let auto: Bool?
            let nudgeMs: Int?; let resetOffset: Bool?; let calibrate: Bool?; let lampNudgeMs: Int?
            enum CodingKeys: String, CodingKey {
                case action, intensity, auto, calibrate
                case nudgeMs = "nudge_ms", resetOffset = "reset_offset", lampNudgeMs = "lamp_nudge_ms"
            }
        }
        return try await postJSON(LightSyncState.self, path: "api/lightsync",
                                  body: Body(action: action, intensity: intensity, auto: auto,
                                             nudgeMs: nudgeMs, resetOffset: resetOffset, calibrate: calibrate,
                                             lampNudgeMs: lampNudgeMs))
    }

    /// Make a light sync for the film on the TV: the copy playing now
    /// (`playing`, so a temp-4K copy gets its own); `ratingKey` is for older
    /// Cores, which ignore `playing`. Core alerts when it's ready.
    func buildLightSync(ratingKey: String) async throws -> LightSyncState {
        struct Body: Encodable {
            let ratingKey: String
            let playing = true
            enum CodingKeys: String, CodingKey { case ratingKey = "rating_key", playing }
        }
        return try await postJSON(LightSyncState.self, path: "api/lightsync/build", body: Body(ratingKey: ratingKey))
    }

    func homeScenes() async throws -> HomeScenes {
        try await getJSON(HomeScenes.self, path: "api/home/scenes")
    }

    /// Barn Door Protocol: "engage" or "stand_down" (three taps on the reactor).
    @discardableResult
    func barnDoor(_ action: String) async throws -> String {
        struct Body: Encodable { let action: String }
        struct Result: Decodable { let message: String }
        return try await postJSON(Result.self, path: "api/protocols/barn-door", body: Body(action: action)).message
    }

    /// Radarr/Sonarr quality profile names for the request picker.
    func mediaProfiles(kind: String) async throws -> MediaProfiles {
        try await getJSON(MediaProfiles.self, path: "api/media/profiles",
                          query: [URLQueryItem(name: "kind", value: kind == "movie" ? "movie" : "tv")])
    }

    /// `toggle`: pressing the scene the room is in, untouched since, leaves it
    /// (Core runs Normal); otherwise the scene runs (again).
    func activateScene(_ entityID: String, toggle: Bool = true) async throws -> String {
        struct Body: Encodable {
            let entityID: String
            let toggle: Bool
            enum CodingKeys: String, CodingKey { case entityID = "entity_id", toggle }
        }
        struct Result: Decodable { let error: String }
        return try await postJSON(Result.self, path: "api/home/scenes/activate",
                                  body: Body(entityID: entityID, toggle: toggle)).error
    }

    /// The movie remote's Exit Movie: Core puts the room back the way it was
    /// before the Movie scene (`brain/movie_exit.py`). Returns HA's error, or "".
    func exitMovieScene() async throws -> String {
        struct Empty: Encodable {}
        struct Result: Decodable { let error: String }
        return try await postJSON(Result.self, path: "api/home/scenes/exit-movie", body: Empty()).error
    }

    func homeControl(entityID: String, turnOn: Bool) async throws -> HomeControlResult {
        try await postJSON(
            HomeControlResult.self,
            path: "api/home/control",
            body: HomeControlRequest(entityID: entityID, action: turnOn ? "on" : "off")
        )
    }

    private struct TorrentControlRequest: Encodable {
        let torrentHash: String
        let action: String
        let confirmed: Bool

        enum CodingKeys: String, CodingKey {
            case action, confirmed
            case torrentHash = "torrent_hash"
        }
    }

    /// `confirmed: true` is deliberate: on this surface the user's tap on a
    /// specific torrent's pause/resume button *is* the confirmation, and the
    /// hash comes straight from the live list response.
    func torrentControl(hash: String, action: String) async throws -> TorrentControlResult {
        try await postJSON(
            TorrentControlResult.self,
            path: "api/torrents/control",
            body: TorrentControlRequest(torrentHash: hash, action: action, confirmed: true)
        )
    }

    func submitDebugReport(
        issue: String,
        messages: [DebugReportMessagePayload],
        sessionID: String?,
        conversationTitle: String,
        focusMessageID: String?,
        appContext: [String: String]
    ) async throws -> String {
        var request = request(path: "debug-reports", method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(DebugReportRequest(
            issue: issue,
            messages: messages,
            sessionID: sessionID,
            conversationTitle: conversationTitle,
            focusMessageID: focusMessageID,
            appContext: appContext
        ))
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(for: request)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
        let body = String(data: data, encoding: .utf8)
        _ = try validate(response, data: data)
        guard let decoded = try? JSONDecoder().decode(DebugReportResponse.self, from: data) else {
            throw JarvisAPIError.malformedResponse(body)
        }
        return decoded.id
    }
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
