import Foundation

struct LiveVoiceEvent: Decodable {
    let type: String
    var text: String?
    var message: String?
    var sampleRate: Double?
    var sessionID: String?
    var requestID: String?
    enum CodingKeys: String, CodingKey {
        case type, text, message
        case sampleRate = "sample_rate", sessionID = "session_id", requestID = "request_id"
    }
}

/// One capture/turn per authenticated socket. It is never automatically
/// retried: ordinary Chat history/receipts recover a committed turn safely.
@MainActor
final class LiveVoiceConnection {
    private let socket: URLSessionWebSocketTask
    private var receiveTask: Task<Void, Never>?

    init(socket: URLSessionWebSocketTask) { self.socket = socket }

    func open(sessionID: String, requestID: String,
              onEvent: @escaping (LiveVoiceEvent) -> Void,
              onAudio: @escaping (Data) -> Void,
              onFailure: @escaping (Error) -> Void) async throws {
        socket.maximumMessageSize = 128 * 1024
        socket.resume()
        try await send(["type": "start", "session_id": sessionID, "request_id": requestID])
        let first = try await socket.receive()
        guard case .string(let json) = first else { throw JarvisAPIError.malformedResponse("Invalid voice handshake.") }
        let ready = try JSONDecoder().decode(LiveVoiceEvent.self, from: Data(json.utf8))
        guard ready.type == "ready", ready.sampleRate == 22050 else {
            throw JarvisAPIError.malformedResponse(ready.message ?? "Update JARVIS Core to use live voice.")
        }
        onEvent(ready)
        receiveTask = Task { [weak self] in
            guard let self else { return }
            do {
                while !Task.isCancelled {
                    let message = try await socket.receive()
                    guard !Task.isCancelled else { return }
                    switch message {
                    case .data(let data): onAudio(data)
                    case .string(let json):
                        let event = try JSONDecoder().decode(LiveVoiceEvent.self, from: Data(json.utf8))
                        onEvent(event)
                        if event.type == "done" || event.type == "error" { return }
                    @unknown default: break
                    }
                }
            } catch {
                if !Task.isCancelled { onFailure(error) }
            }
        }
    }

    func send(_ message: [String: String]) async throws {
        let data = try JSONSerialization.data(withJSONObject: message)
        try await socket.send(.string(String(decoding: data, as: UTF8.self)))
    }

    func close() {
        receiveTask?.cancel()
        receiveTask = nil
        socket.cancel(with: .normalClosure, reason: nil)
    }
}
