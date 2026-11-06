import AVFoundation
import Combine
import Foundation

@MainActor
final class PushToTalkController: ObservableObject {
    @Published private(set) var phase = "idle"
    @Published private(set) var transcript = ""
    @Published private(set) var reply = ""
    @Published private(set) var detail = ""
    @Published private(set) var acknowledged = false
    var showing: Bool { phase != "idle" }
    var listening: Bool { phase == "listening" || phase == "connecting" }
    var busy: Bool { !["idle", "done", "error"].contains(phase) }

    private weak var settings: AppSettings?
    private weak var chat: ChatViewModel?
    private let microphone = VoiceInputController()
    private let audio = VoiceAudioPlayer()
    private var socket: LiveVoiceConnection?
    private var connectionTask: Task<Void, Error>?
    private var releaseTask: Task<Void, Never>?
    private var partialTask: Task<Void, Never>?
    private var token = UUID()
    private var conversationID: UUID?
    private var responseID: UUID?
    private var requestID = ""
    private var committed = false
    private var ready = false
    private var listeningState: AnyCancellable?
    private var errors: AnyCancellable?
    private var interruptions: AnyCancellable?
    private var inStatus = false

    init() {
        listeningState = microphone.$isListening.sink { [weak self] active in
            guard let self, active, self.ready, self.phase == "connecting" else { return }
            self.phase = "listening"
        }
        errors = microphone.$errorMessage.compactMap { $0 }.sink { [weak self] message in
            guard let self, self.busy else { return }
            self.fail(message)
        }
        interruptions = NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)
            .sink { [weak self] _ in Task { @MainActor in self?.cancel() } }
        audio.onDrained = { [weak self] in
            guard let self, self.phase != "idle", self.phase != "error" else { return }
            self.phase = "done"
        }
    }

    func configure(settings: AppSettings, chat: ChatViewModel) { self.settings = settings; self.chat = chat }

    func begin() {
        cancel()
        guard let settings, let chat else { return }
        guard !chat.isAwaitingReply else { fail("Let the current Chat reply finish first."); return }
        guard settings.isConfigured else { fail("Connect JARVIS in Settings first."); return }
        token = UUID()
        let capture = token
        requestID = UUID().uuidString
        phase = "connecting"
        transcript = ""
        reply = ""
        detail = ""
        acknowledged = false
        ready = false
        committed = false
        inStatus = false
        microphone.start { [weak self] text in
            guard let self, self.token == capture, self.listening else { return }
            self.transcript = text
            self.sendPartial(capture)
        }
        connectionTask = Task { [weak self] in
            guard let self else { throw CancellationError() }
            do {
                let client = try JarvisAPIClient.make(from: settings)
                let (conversation, session) = try await chat.prepareVoiceConversation()
                guard self.token == capture, !Task.isCancelled else { throw CancellationError() }
                self.conversationID = conversation
                let link = try client.liveVoiceConnection()
                self.socket = link
                try await link.open(sessionID: session, requestID: self.requestID,
                    onEvent: { [weak self] event in self?.event(event, capture: capture) },
                    onAudio: { [weak self] bytes in self?.receiveAudio(bytes, capture: capture) },
                    onFailure: { [weak self] _ in
                        guard let self, self.token == capture else { return }
                        self.fail("HQ disconnected. Check Chat history before retrying; it may still finish this turn.")
                    })
                guard self.token == capture, !Task.isCancelled else { link.close(); throw CancellationError() }
                self.ready = true
                if self.phase == "connecting", self.microphone.isListening { self.phase = "listening" }
                self.sendPartial(capture)
            } catch {
                if self.token == capture, !Task.isCancelled {
                    self.fail("Live voice could not connect. Check the server address and update JARVIS Core.")
                }
                throw error
            }
        }
    }

    private func sendPartial(_ capture: UUID) {
        // Throttle rather than debounce: continuous speech must still reach
        // HQ even when recognition revises words more often than every 120 ms.
        guard socket != nil, listening, partialTask == nil else { return }
        partialTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(120))
            guard let self, self.token == capture, self.listening, !Task.isCancelled else { return }
            defer { if self.token == capture { self.partialTask = nil } }
            do { try await self.socket?.send(["type": "partial", "text": String(self.transcript.prefix(8000))]) }
            catch { if self.token == capture { self.fail("The live voice link disconnected.") } }
        }
    }

    func release() {
        guard listening else { return }
        phase = "finalizing"
        partialTask?.cancel()
        let capture = token
        releaseTask = Task { [weak self] in
            guard let self else { return }
            let final = await self.microphone.finish().trimmingCharacters(in: .whitespacesAndNewlines)
            guard self.token == capture, !Task.isCancelled else { return }
            guard !final.isEmpty else { self.fail("I didn’t hear anything. Hold the orb and try again."); return }
            guard final.count <= 8000 else { self.fail("That voice message is too long. Please use a shorter request."); return }
            self.transcript = final
            do {
                try await self.connectionTask?.value
                guard self.token == capture, !Task.isCancelled,
                      let conversation = self.conversationID, let chat = self.chat,
                      let response = chat.beginVoiceReply(self.transcript, conversationID: conversation, requestID: self.requestID)
                else { self.cancel(); return }
                self.responseID = response
                self.committed = true
                self.phase = "thinking"
                try await self.socket?.send(["type": "commit", "text": self.transcript])
            } catch {
                if self.token == capture { self.fail("The voice turn disconnected. Check Chat before retrying.") }
            }
        }
    }

    private func event(_ event: LiveVoiceEvent, capture: UUID) {
        guard token == capture else { return }
        switch event.type {
        case "transcript": acknowledged = true
        case "text":
            guard let responseID, let conversationID, let text = event.text else { return }
            guard chat?.receiveVoiceChunk(text, responseID: responseID, conversationID: conversationID) == true else {
                cancel(); return
            }
            // Protocol markers and tool status are for Chat, not spoken text.
            for char in text {
                if char == "\u{1D}" { inStatus.toggle() }
                else if !inStatus { reply.append(char == "\u{1E}" ? "\n\n" : String(char)) }
            }
        case "audio_error": detail = event.message ?? "Voice unavailable. The text reply will continue."
        case "done":
            if let responseID, let conversationID {
                chat?.finishVoiceReply(responseID: responseID, conversationID: conversationID)
            }
            committed = false
            self.responseID = nil
            socket?.close()
            socket = nil
            audio.finish()
        case "error": fail(event.message ?? "The voice turn failed. Check Chat before retrying.")
        default: break
        }
    }

    private func receiveAudio(_ bytes: Data, capture: UUID) {
        guard token == capture else { return }
        do { try audio.append(bytes); phase = "speaking" }
        catch { audio.stop(); detail = "Audio playback failed. The text reply will continue." }
    }

    private func fail(_ message: String) {
        cancel()
        detail = message
        phase = "error"
    }

    func cancel() {
        token = UUID()
        partialTask?.cancel()
        partialTask = nil
        releaseTask?.cancel()
        connectionTask?.cancel()
        connectionTask = nil
        microphone.stop()
        socket?.close()
        socket = nil
        audio.stop()
        if committed, let responseID, let conversationID {
            chat?.finishVoiceReply(responseID: responseID, conversationID: conversationID,
                error: "Stopped receiving voice. HQ may still finish; check history before retrying.")
        }
        committed = false
        ready = false
        responseID = nil
        conversationID = nil
        phase = "idle"
    }

    func conversationChanged(_ id: UUID) {
        if let conversationID, conversationID != id { cancel() }
    }
}
