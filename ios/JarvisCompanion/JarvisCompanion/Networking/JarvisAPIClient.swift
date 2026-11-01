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

    /// URLError codes worth translating, with a nudge toward the usual suspect
    /// on this setup: the phone having dropped off the Tailscale network.
    private static func describe(_ error: URLError) -> String {
        switch error.code {
        case .timedOut:
            return "JARVIS didn't respond in time. It may still be working through a long tool call — try again in a moment."
        case .cannotConnectToHost:
            return "Couldn't connect to JARVIS. Is the server running, and is Tailscale connected on this device?"
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

    var date: Date { Date(timeIntervalSince1970: createdAt) }

    enum CodingKeys: String, CodingKey {
        case id, kind, severity, title, message, source
        case createdAt = "created_at"
    }
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

    var isPending: Bool { status == "pending" }
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
    private static let sharedSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

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
        let address = settings.baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !address.isEmpty, !token.isEmpty else { throw JarvisAPIError.notConfigured }
        guard let url = settings.resolvedBaseURL else { throw JarvisAPIError.invalidBaseURL(address) }

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

    private static let device: String = {
        if ProcessInfo.processInfo.isMacCatalystApp { return "mac" }
        // The hardware model ("iPad13,4") is readable off the main thread, unlike the idiom.
        return modelIsPad ? "ipad" : "iphone"
    }()

    private static var modelIsPad: Bool {
        var info = utsname()
        uname(&info)
        let machine = withUnsafeBytes(of: &info.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
        return machine.hasPrefix("iPad")
    }

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
            (data, response) = try await session.data(for: request)
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
            let (bytes, response) = try await session.bytes(for: request)
            let http = try validate(response)
            if let boundSessionID = http.value(forHTTPHeaderField: "X-Jarvis-Session-ID") {
                await onSession(boundSessionID)
            }
            var pending = Data()
            let clock = ContinuousClock()
            var lastDelivery = clock.now
            for try await byte in bytes {
                try Task.checkCancellation()
                pending.append(byte)
                let deliveryDue = pending.count >= 512
                    || lastDelivery.duration(to: clock.now) >= .milliseconds(40)
                if deliveryDue, let text = String(data: pending, encoding: .utf8) {
                    await onChunk(text)
                    pending.removeAll(keepingCapacity: true)
                    lastDelivery = clock.now
                }
            }
            if !pending.isEmpty, let text = String(data: pending, encoding: .utf8) {
                await onChunk(text)
            }
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
            let (bytes, response) = try await session.bytes(for: request)
            _ = try validate(response)
            for try await line in bytes.lines {
                try Task.checkCancellation()
                guard line.hasPrefix("data: "), let data = line.dropFirst(6).data(using: .utf8),
                      let state = try? JSONDecoder().decode(VoiceState.self, from: data) else { continue }
                await onState(state)
            }
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
    }

    /// Home Assistant changes (`GET /api/home/stream`, server-sent events):
    /// calls `onChange` with Core's change counter for each `home` event.
    /// `onChange(seq, theme)`: every pulse carries the shared theme too.
    /// `look` moves with any theme pick or Experimenting switch on Core.
    func streamHome(onChange: @escaping @MainActor (Int, String?, Double?) -> Void) async throws {
        struct Pulse: Decodable { let seq: Int; let theme: String?; let look: Double? }
        var request = request(path: "api/home/stream")
        request.timeoutInterval = 60  // Core sends a keep-alive every 15 s
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        do {
            let (bytes, response) = try await session.bytes(for: request)
            _ = try validate(response)
            for try await line in bytes.lines {
                try Task.checkCancellation()
                guard line.hasPrefix("data: "), let data = line.dropFirst(6).data(using: .utf8),
                      let pulse = try? JSONDecoder().decode(Pulse.self, from: data) else { continue }
                await onChange(pulse.seq, pulse.theme, pulse.look)
            }
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }
    }

    func checkHealth() async throws {
        let request = request(path: "health")
        let (data, response) = try await session.data(for: request)
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
        let (data, response) = try await session.data(for: request)
        _ = try validate(response, data: data)
    }

    func deleteSession(_ sessionID: String) async throws {
        let request = request(path: "sessions/\(sessionID)", method: "DELETE")
        let (data, response) = try await session.data(for: request)
        _ = try validate(response, data: data)
    }

    func sessions() async throws -> [ServerSession] {
        struct Envelope: Decodable { let sessions: [ServerSession] }
        let request = request(path: "sessions")
        let (data, response) = try await session.data(for: request)
        _ = try validate(response, data: data)
        return try JSONDecoder().decode(Envelope.self, from: data).sessions
    }

    func messages(for sessionID: String) async throws -> [ServerMessage] {
        struct Envelope: Decodable { let messages: [ServerMessage] }
        let request = request(path: "sessions/\(sessionID)")
        let (data, response) = try await session.data(for: request)
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
            (data, response) = try await session.data(for: request)
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
            (data, response) = try await session.data(for: request)
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

    // MARK: - To-dos (open reminders; done deletes)

    private struct TodoBody: Encodable { let text: String }

    func addTodo(_ text: String) async throws -> NowReminder {
        struct Envelope: Decodable { let todo: NowReminder }
        return try await postJSON(Envelope.self, path: "api/todos", body: TodoBody(text: text)).todo
    }

    func completeTodo(id: String) async throws {
        var request = request(path: "api/todos/\(id)/done", method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{}".utf8)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
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
    func reportBattery(level: Float, charging: Bool) async throws {
        struct Body: Encodable { let device = "iPad"; let level: Float; let charging: Bool }
        struct Reply: Decodable { let ok: Bool }
        _ = try await postJSON(Reply.self, path: "api/device/battery", body: Body(level: level, charging: charging))
    }

    /// Lock the lens off for five minutes, from the phone's Kill button.
    @discardableResult
    func cameraKill() async throws -> CameraStatus {
        try await postEmpty(CameraStatus.self, path: "api/camera/kill")
    }

    /// The iPad uploads one JPEG frame (raw body). Throws on a kill (409).
    func putCameraFrame(_ jpeg: Data) async throws {
        var r = request(path: "api/camera/frame", method: "POST")
        r.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        r.httpBody = jpeg
        let (data, response) = try await session.upload(for: r, from: jpeg)
        _ = try validate(response, data: data)
    }

    /// The phone pulls the newest frame; nil when the lens isn't sending (404).
    func cameraFrame() async throws -> Data? {
        let r = request(path: "api/camera/frame")
        let (data, response) = try await session.data(for: r)
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
            (data, response) = try await session.data(for: r)
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

    /// Schwab's login page and the callback address to catch when Schwab
    /// redirects there (nothing listens at it; the app hands it to Core).
    func schwabLogin() async throws -> (url: URL, callback: String) {
        struct Envelope: Decodable { let url: URL; let callback: String }
        let body = try await postJSON(Envelope.self, path: "api/schwab/login", body: EmptyBody())
        return (body.url, body.callback)
    }

    func finishSchwabLogin(redirect: URL) async throws {
        struct Body: Encodable {
            let redirectURL: String
            enum CodingKeys: String, CodingKey { case redirectURL = "redirect_url" }
        }
        struct Done: Decodable { let linked: Bool }
        _ = try await postJSON(Done.self, path: "api/schwab/finish", body: Body(redirectURL: redirect.absoluteString))
    }

    // MARK: - Wake-up schedule and shared theme

    func wakeSchedule() async throws -> WakeSchedule {
        try await getJSON(WakeSchedule.self, path: "api/wake")
    }

    func snoozeWakeUp() async throws {
        struct Until: Decodable { let until: Double }
        _ = try await postJSON(Until.self, path: "api/wake/snooze", body: EmptyBody())
    }

    func wakeUpAwake() async throws {
        struct Done: Decodable { let ok: Bool }
        _ = try await postJSON(Done.self, path: "api/wake/awake", body: EmptyBody())
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
            (data, response) = try await session.data(for: request)
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
            let (bytes, response) = try await session.bytes(for: request)
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

    /// The room speaker's volume (JARVIS's voice and sounds on the Optiplex).
    func roomVolume() async throws -> RoomVolume {
        try await getJSON(RoomVolume.self, path: "api/room/volume")
    }

    func setRoomVolume(_ level: Int) async throws -> RoomVolume {
        struct Body: Encodable { let level: Int }
        return try await postJSON(RoomVolume.self, path: "api/room/volume", body: Body(level: level))
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
            (data, response) = try await session.data(for: request)
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
