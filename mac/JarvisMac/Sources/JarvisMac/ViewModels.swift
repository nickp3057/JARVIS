import Foundation

// MARK: - Chat with JARVIS

/// Conversations with JARVIS. Sessions live on Core (`memory/history.db`),
/// so the phone's chats show up here and the reverse.
@MainActor
final class ChatModel: ObservableObject {
    @Published private(set) var sessions: [ServerSession] = []
    @Published var selectedSession: String?
    @Published private(set) var lines: [ChatLine] = []
    @Published private(set) var thinking = false
    @Published var draft = ""
    @Published var error: String?

    private let settings: MacSettings

    init(settings: MacSettings) { self.settings = settings }

    func loadSessions() async {
        do {
            sessions = try await JarvisAPI.make(settings).sessions()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func open(_ sessionID: String?) async {
        guard !thinking else { return }
        selectedSession = sessionID
        guard let sessionID else {
            lines = []
            return
        }
        do {
            let messages = try await JarvisAPI.make(settings).messages(in: sessionID)
            guard selectedSession == sessionID else { return }
            lines = messages.flatMap { message -> [ChatLine] in
                guard message.role == "user" || message.role == "assistant" else { return [] }
                return message.content.split(separator: JarvisAPI.roundBreak)
                    .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                    .filter { !$0.isEmpty }
                    .map { ChatLine(fromNick: message.role == "user", text: $0) }
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func newChat() {
        guard !thinking else { return }
        selectedSession = nil
        lines = []
        error = nil
    }

    func delete(_ sessionID: String) async {
        do {
            try await JarvisAPI.make(settings).deleteSession(sessionID)
            if selectedSession == sessionID { newChat() }
            await loadSessions()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !thinking else { return }
        draft = ""
        lines.append(ChatLine(fromNick: true, text: text))
        lines.append(ChatLine(fromNick: false, text: ""))
        thinking = true
        Task {
            defer {
                thinking = false
                lines.removeAll { !$0.fromNick && $0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            }
            do {
                let api = try JarvisAPI.make(settings)
                let session = try await api.streamChat(text, sessionID: selectedSession) { piece in
                    self.append(piece)
                }
                if let session { selectedSession = session }
                error = nil
                await loadSessions()
            } catch {
                if lines.last?.text.isEmpty == true { lines.removeLast() }
                if lines.last?.fromNick == true, lines.last?.text == text {
                    lines.removeLast()
                    draft = text  // nothing he wrote is lost
                }
                self.error = error.localizedDescription
            }
        }
    }

    /// A streamed piece: a round break starts a new JARVIS bubble.
    private func append(_ piece: String) {
        let parts = piece.split(separator: JarvisAPI.roundBreak, omittingEmptySubsequences: false)
        for (index, part) in parts.enumerated() {
            if index > 0 {
                if let last = lines.last, !last.fromNick, last.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    // Reuse an empty bubble rather than stacking blanks.
                } else {
                    lines.append(ChatLine(fromNick: false, text: ""))
                }
            }
            guard !part.isEmpty, !lines.isEmpty else { continue }
            var last = lines.removeLast()
            last.text += part
            lines.append(last)
        }
    }
}

// MARK: - Home

@MainActor
final class HomeModel: ObservableObject {
    @Published private(set) var entities: [HomeEntity] = []
    @Published private(set) var scenes: [HomeScene] = []
    @Published private(set) var currentScene: String?
    @Published private(set) var busy: Set<String> = []
    @Published var error: String?

    private let settings: MacSettings

    init(settings: MacSettings) { self.settings = settings }

    func load() async {
        do {
            let api = try JarvisAPI.make(settings)
            async let entityList = api.homeEntities()
            async let sceneList = api.homeScenes()
            let (e, s) = try await (entityList, sceneList)
            entities = e.entities
            scenes = s.scenes
            currentScene = s.current
            let problems = [e.error, s.error].filter { !$0.isEmpty }
            error = problems.isEmpty ? nil : problems.joined(separator: " · ")
        } catch {
            self.error = error.localizedDescription
        }
    }

    func activate(_ scene: HomeScene) async {
        await run(scene.entityID) { try await $0.activateScene(scene.entityID) }
    }

    func toggle(_ entity: HomeEntity) async {
        await run(entity.entityID) { try await $0.setPower(entity.entityID, on: !entity.isOn) }
    }

    func setBrightness(_ entity: HomeEntity, _ percent: Int) async {
        await run(entity.entityID) { try await $0.setBrightness(entity.entityID, percent: percent) }
    }

    private func run(_ id: String, _ action: (JarvisAPI) async throws -> String) async {
        busy.insert(id)
        defer { busy.remove(id) }
        do {
            let problem = try await action(JarvisAPI.make(settings))
            error = problem.isEmpty ? nil : problem
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Alerts

@MainActor
final class AlertsModel: ObservableObject {
    @Published private(set) var alerts: [JarvisAlert] = []
    @Published private(set) var refreshing = false
    @Published var error: String?

    private let settings: MacSettings

    init(settings: MacSettings) { self.settings = settings }

    /// Warnings and criticals: what the sidebar badge counts.
    var attentionCount: Int { alerts.filter { $0.severity != "info" }.count }

    func load() async {
        do {
            alerts = try await JarvisAPI.make(settings).alerts()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Re-check each alert against the live system; fixed ones disappear.
    func recheck() async {
        refreshing = true
        defer { refreshing = false }
        do {
            try await JarvisAPI.make(settings).refreshAlerts()
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func clear() async {
        do {
            try await JarvisAPI.make(settings).clearAlerts()
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: Fixes and autopilot

    @Published private(set) var fixing: Set<Int> = []
    @Published private(set) var autopilot: AutopilotStatus?
    /// A Fix it that couldn't start, keyed by alert ("nothing to retry").
    @Published var fixNotes: [Int: String] = [:]

    func loadAutopilot() async {
        autopilot = try? await JarvisAPI.make(settings).autopilot()
    }

    func setAutopilot(_ enabled: Bool) async {
        do { autopilot = try await JarvisAPI.make(settings).setAutopilot(enabled) } catch {
            self.error = error.localizedDescription
        }
    }

    /// The click is the approval; the alert's repair shows progress.
    func fix(_ alert: JarvisAlert) async {
        guard !fixing.contains(alert.id) else { return }
        fixing.insert(alert.id)
        defer { fixing.remove(alert.id) }
        do {
            let result = try await JarvisAPI.make(settings).fixAlert(alert)
            fixNotes[alert.id] = result.started ? nil : result.message
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func dismiss(_ alert: JarvisAlert) async {
        alerts.removeAll { $0.id == alert.id }
        do {
            try await JarvisAPI.make(settings).dismissAlert(alert)
        } catch {
            self.error = error.localizedDescription
        }
        await load()
    }

    var anyRepairRunning: Bool { alerts.contains { $0.repair?.isWorking == true } }
}

// MARK: - Donna

/// Donna's session, mirroring the phone's DonnaModel. Core keeps one open
/// session, so a conversation started on the phone continues here.
@MainActor
final class DonnaModel: ObservableObject {
    @Published private(set) var messages: [DonnaMessage] = []
    @Published private(set) var thinking = false
    @Published private(set) var loading = false
    @Published private(set) var hasCaseFile = false
    @Published var draft = ""
    @Published var error: String?

    private let settings: MacSettings

    init(settings: MacSettings) { self.settings = settings }

    func load() async {
        guard !thinking else { return }
        loading = true
        defer { loading = false }
        do {
            let state = try await JarvisAPI.make(settings).donnaState()
            messages = state.session?.messages ?? []
            hasCaseFile = state.hasCaseFile
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !thinking else { return }
        draft = ""
        messages.append(DonnaMessage(role: "user", text: text, at: Date().timeIntervalSince1970))
        thinking = true
        Task {
            defer { thinking = false }
            do {
                let reply = try await JarvisAPI.make(settings).donnaSend(text)
                messages.append(DonnaMessage(role: "assistant", text: reply.reply, at: Date().timeIntervalSince1970))
                error = nil
            } catch {
                // Core usually finished and saved her answer anyway (the Mac
                // slept, the network dropped): fetch it before giving up.
                if await recover(sent: text) { return }
                if messages.last?.text == text { messages.removeLast() }
                draft = text
                self.error = error.localizedDescription
            }
        }
    }

    /// After a failed send: wait for Core's copy of her reply. True once the
    /// session shows `sent` followed by an answer.
    private func recover(sent text: String) async -> Bool {
        for attempt in 0..<20 {
            if attempt > 0 { try? await Task.sleep(for: .seconds(3)) }
            guard let state = try? await JarvisAPI.make(settings).donnaState() else { continue }
            let remote = state.session?.messages ?? []
            guard let asked = remote.lastIndex(where: { $0.fromNick && $0.text == text }) else { return false }
            if remote.index(after: asked) < remote.endIndex {
                messages = remote
                error = nil
                return true
            }
        }
        return false
    }

    /// End now: she writes her note; the next message starts a new session.
    /// True when the session ended (the caller closes Donna).
    @discardableResult
    func endSession() async -> Bool {
        guard !messages.isEmpty, !thinking else { return false }
        do {
            try await JarvisAPI.make(settings).donnaEnd()
            messages = []
            hasCaseFile = true
            error = nil
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    func memory() async throws -> DonnaMemory { try await JarvisAPI.make(settings).donnaMemory() }
}
