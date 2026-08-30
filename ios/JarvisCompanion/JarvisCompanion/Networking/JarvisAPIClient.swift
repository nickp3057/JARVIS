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

/// Thin wrapper over `POST {baseURL}/chat`.
///
/// This is the only type in the app that knows the wire format. JARVIS Core
/// does all the reasoning and tool-calling; the request is one string out and
/// the response is one string back.
struct JarvisAPIClient {
    /// Generous by design: a single turn can fan out into several server-side
    /// tool calls before Claude produces its final text.
    static let defaultTimeout: TimeInterval = 30

    /// One session for the whole app. A `URLSession` retains itself until it is
    /// invalidated, so building one per request would leak; the per-request
    /// timeout is set on the `URLRequest` instead.
    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

    private let baseURL: URL
    private let token: String
    private let timeout: TimeInterval

    init(baseURL: URL, token: String, timeout: TimeInterval = JarvisAPIClient.defaultTimeout) {
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
    }

    private struct ChatResponse: Decodable {
        let reply: String
    }

    func send(message: String) async throws -> String {
        var request = URLRequest(url: baseURL.appending(path: "chat"))
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(ChatRequest(message: message))

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await Self.session.data(for: request)
        } catch let error as URLError {
            throw JarvisAPIError.transport(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw JarvisAPIError.malformedResponse(nil)
        }

        let body = String(data: data, encoding: .utf8)

        switch http.statusCode {
        case 200..<300:
            break
        case 401, 403:
            throw JarvisAPIError.unauthorized
        default:
            throw JarvisAPIError.server(status: http.statusCode, body: body)
        }

        guard let decoded = try? JSONDecoder().decode(ChatResponse.self, from: data) else {
            throw JarvisAPIError.malformedResponse(body)
        }

        return decoded.reply
    }
}
