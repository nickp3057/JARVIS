import Foundation
import UIKit

@MainActor
final class ChatViewModel: ObservableObject {
    enum ConnectionState: Equatable {
        case unconfigured, checking, online, offline(String)

        var label: String {
            switch self {
            case .unconfigured: return "Not configured"
            case .checking: return "Connecting…"
            case .online: return "Connected"
            case .offline: return "Offline"
            }
        }

        var symbol: String {
            switch self {
            case .unconfigured: return "gearshape"
            case .checking: return "arrow.triangle.2.circlepath"
            case .online: return "checkmark.circle.fill"
            case .offline: return "wifi.slash"
            }
        }
    }

    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var conversations: [ChatConversation] = []
    @Published private(set) var isAwaitingReply = false
    @Published private(set) var connectionState: ConnectionState = .unconfigured
    @Published var draft = ""

    private let settings: AppSettings
    private let store: ChatStore
    private let inactivityLimit: TimeInterval
    private var activeConversationID: UUID
    private var inactiveSince: Date?
    private var lastSentText: String?
    private var inFlight: Task<Void, Never>?
    private var connectionCheck: Task<Void, Never>?

    init(
        settings: AppSettings,
        store: ChatStore = JSONFileChatStore(),
        inactivityLimit: TimeInterval = 5 * 60,
        now: Date = Date()
    ) {
        self.settings = settings
        self.store = store
        self.inactivityLimit = inactivityLimit
        let archive = store.load()
        conversations = archive.conversations
        activeConversationID = archive.activeConversationID
        inactiveSince = archive.inactiveSince
        let active = archive.conversations.first(where: { $0.id == archive.activeConversationID })
        messages = active?.messages ?? []
        draft = active?.draft ?? ""

        if let inactiveSince = archive.inactiveSince,
           now.timeIntervalSince(inactiveSince) >= inactivityLimit,
           !messages.isEmpty {
            startNewConversation(now: now)
        } else {
            self.inactiveSince = nil
            persist()
        }
        recoverRetryState()
    }

    var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isAwaitingReply
    }

    var canRetry: Bool {
        !isAwaitingReply && lastSentText != nil && messages.last?.role == .error
    }

    var visibleConversations: [ChatConversation] {
        conversations.filter { !$0.messages.isEmpty }.sorted { $0.updatedAt > $1.updatedAt }
    }

    var activeTitle: String {
        conversations.first(where: { $0.id == activeConversationID })?.title ?? "New chat"
    }

    func filteredConversations(matching query: String) -> [ChatConversation] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return visibleConversations }
        return visibleConversations.filter { conversation in
            conversation.title.localizedCaseInsensitiveContains(needle)
                || conversation.messages.contains { $0.text.localizedCaseInsensitiveContains(needle) }
        }
    }

    func appBecameInactive(at date: Date = Date()) {
        inactiveSince = date
        persist()
    }

    func appBecameActive(at date: Date = Date()) {
        if let inactiveSince {
            self.inactiveSince = nil
            if date.timeIntervalSince(inactiveSince) >= inactivityLimit, !messages.isEmpty {
                startNewConversation(now: date)
            } else {
                persist()
            }
        }
        refreshConnectionStatus()
    }

    func refreshConnectionStatus() {
        connectionCheck?.cancel()
        guard settings.isConfigured else {
            connectionState = .unconfigured
            return
        }
        // A refresh should not make a known-good connection look disconnected.
        // `.checking` is only the initial/unconfigured state; chat activity has
        // its own progress UI.
        if connectionState != .online { connectionState = .checking }
        connectionCheck = Task { [weak self] in
            guard let self else { return }
            do {
                try await JarvisAPIClient.make(from: settings).checkHealth()
                guard !Task.isCancelled else { return }
                connectionState = .online
                await synchronizeHistory()
            } catch {
                guard !Task.isCancelled else { return }
                connectionState = .offline(error.localizedDescription)
            }
            connectionCheck = nil
        }
    }

    private func synchronizeHistory() async {
        do {
            let client = try JarvisAPIClient.make(from: settings)
            let remoteSessions = try await client.sessions()
            for remote in remoteSessions {
                let remoteMessages = try await client.messages(for: remote.sessionID)
                let converted = remoteMessages.compactMap { item -> ChatMessage? in
                    let role: ChatMessage.Role
                    switch item.role {
                    case "user": role = .user
                    case "assistant": role = .jarvis
                    default: return nil
                    }
                    return ChatMessage(role: role, text: item.content, date: parseServerDate(item.createdAt))
                }
                guard !converted.isEmpty else { continue }

                if let index = conversations.firstIndex(where: { $0.serverSessionID == remote.sessionID }) {
                    // The server is authoritative for completed user/assistant turns;
                    // retain a trailing local error so retry remains available.
                    let trailingError = conversations[index].messages.last.flatMap {
                        $0.role == .error ? $0 : nil
                    }
                    conversations[index].messages = converted + (trailingError.map { [$0] } ?? [])
                    if let title = remote.title, !title.isEmpty { conversations[index].customTitle = title }
                    conversations[index].updatedAt = converted.last?.date ?? conversations[index].updatedAt
                    if conversations[index].id == activeConversationID {
                        messages = conversations[index].messages
                    }
                } else {
                    conversations.append(ChatConversation(
                        createdAt: converted.first?.date ?? Date(),
                        updatedAt: converted.last?.date ?? Date(),
                        messages: converted,
                        customTitle: remote.title,
                        serverSessionID: remote.sessionID
                    ))
                }
            }
            persist()
        } catch {
            // Health is already green; history sync failure should not disable chat.
        }
    }

    private func parseServerDate(_ value: String?) -> Date {
        guard let value else { return Date() }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value) ?? Date()
    }

    func startNewConversation(now: Date = Date()) {
        stopGenerating()
        lastSentText = nil
        inactiveSince = nil
        guard !messages.isEmpty || !draft.isEmpty else { return }
        persist()
        let conversation = ChatConversation(createdAt: now, updatedAt: now)
        conversations.append(conversation)
        activeConversationID = conversation.id
        messages = []
        draft = ""
        persist()
    }

    func openConversation(_ id: UUID) {
        guard id != activeConversationID,
              let conversation = conversations.first(where: { $0.id == id }) else { return }
        stopGenerating()
        persist()
        activeConversationID = id
        messages = conversation.messages
        draft = conversation.draft ?? ""
        recoverRetryState()
        persist()
    }

    func renameConversation(_ id: UUID, to title: String) {
        let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty,
              let index = conversations.firstIndex(where: { $0.id == id }) else { return }
        conversations[index].customTitle = String(clean.prefix(120))
        let serverID = conversations[index].serverSessionID
        persist()
        guard let serverID else { return }
        Task { try? await JarvisAPIClient.make(from: settings).renameSession(serverID, title: clean) }
    }

    func deleteConversation(_ id: UUID) {
        guard let index = conversations.firstIndex(where: { $0.id == id }) else { return }
        let serverID = conversations[index].serverSessionID
        let deletingActive = id == activeConversationID
        if deletingActive { stopGenerating() }
        conversations.remove(at: index)

        if conversations.isEmpty {
            let replacement = ChatConversation()
            conversations = [replacement]
            activeConversationID = replacement.id
            messages = []
            draft = ""
        } else if deletingActive {
            let replacement = conversations.max(by: { $0.updatedAt < $1.updatedAt })!
            activeConversationID = replacement.id
            messages = replacement.messages
            draft = replacement.draft ?? ""
            recoverRetryState()
        }
        persist()
        guard let serverID else { return }
        Task { try? await JarvisAPIClient.make(from: settings).deleteSession(serverID) }
    }

    func clearTranscript() {
        guard !messages.isEmpty else { return }
        deleteConversation(activeConversationID)
        if !messages.isEmpty { startNewConversation() }
    }

    func draftChanged() {
        if draft.contains("\n") {
            draft = draft.replacingOccurrences(of: "\n", with: "")
            send()
        } else {
            persist()
        }
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isAwaitingReply else { return }
        draft = ""
        append(ChatMessage(role: .user, text: text))
        request(text)
    }

    func retry() {
        guard canRetry, let text = lastSentText else { return }
        messages.removeLast()
        persist()
        request(text)
    }

    func submitDebugReport(issue: String, focusMessageID: UUID?) async throws -> String {
        let cleanIssue = issue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanIssue.isEmpty else { throw JarvisAPIError.malformedResponse("Describe what went wrong.") }
        let formatter = ISO8601DateFormatter()
        let transcript = messages.suffix(100).compactMap { message -> DebugReportMessagePayload? in
            let content = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !content.isEmpty else { return nil }
            return DebugReportMessagePayload(
                id: message.id.uuidString,
                role: message.role == .jarvis ? "assistant" : message.role.rawValue,
                content: String(content.prefix(20_000)),
                createdAt: formatter.string(from: message.date)
            )
        }
        guard !transcript.isEmpty else {
            throw JarvisAPIError.malformedResponse("There are no completed messages to report.")
        }
        let device = UIDevice.current
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
        do {
            let reportID = try await JarvisAPIClient.make(from: settings).submitDebugReport(
                issue: cleanIssue,
                messages: transcript,
                sessionID: activeConversation?.serverSessionID,
                conversationTitle: activeTitle,
                focusMessageID: focusMessageID?.uuidString,
                appContext: [
                    "app_version": "\(version) (\(build))",
                    "device": device.model,
                    "system": "\(device.systemName) \(device.systemVersion)",
                    "connection": connectionState.label,
                ]
            )
            connectionState = .online
            return reportID
        } catch {
            if case JarvisAPIError.transport = error {
                connectionState = .offline(error.localizedDescription)
            }
            throw error
        }
    }

    func stopGenerating() {
        inFlight?.cancel()
        inFlight = nil
        isAwaitingReply = false
        if messages.last?.role == .jarvis, messages.last?.text.isEmpty == true {
            messages.removeLast()
        }
        persist()
    }

    private func request(_ text: String) {
        lastSentText = text
        isAwaitingReply = true
        let responseID = UUID()
        append(ChatMessage(id: responseID, role: .jarvis, text: ""))
        let serverSessionID = activeConversation?.serverSessionID

        inFlight = Task { [weak self] in
            guard let self else { return }
            var recoveryClient: JarvisAPIClient?
            do {
                let client = try JarvisAPIClient.make(from: settings)
                recoveryClient = client
                do {
                    _ = try await streamReply(
                        client: client,
                        text: text,
                        sessionID: serverSessionID,
                        responseID: responseID
                    )
                } catch JarvisAPIError.server(let status, _) where status == 404 {
                    // A restarted server may no longer know a cached session.
                    // Retry once without it; if streaming itself is the missing
                    // route, transparently fall back to the legacy /chat API.
                    setServerSessionID(nil)
                    do {
                        _ = try await streamReply(
                            client: client,
                            text: text,
                            sessionID: nil,
                            responseID: responseID
                        )
                    } catch JarvisAPIError.server(let retryStatus, _) where retryStatus == 404 {
                        let legacy = try await client.send(message: text, sessionID: nil)
                        replaceMessage(
                            responseID,
                            text: legacy.reply.trimmingCharacters(in: .whitespacesAndNewlines)
                        )
                        setServerSessionID(legacy.sessionID)
                    }
                }
                guard !Task.isCancelled else { return }
                // Core owns the authoritative completed transcript. This also
                // repairs a stream that ended with a temporary fallback while
                // the server successfully persisted the real answer.
                if let sessionID = activeConversation?.serverSessionID {
                    _ = await reconcileCompletedTurn(
                        client: client,
                        sessionID: sessionID,
                        userText: text,
                        responseID: responseID,
                        attempts: 2
                    )
                }
                if messages.last?.id == responseID, messages.last?.text.isEmpty == true {
                    replaceMessage(responseID, text: "(no reply)")
                }
                connectionState = .online
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled else { return }
                let recovered: Bool
                if let recoveryClient,
                   let sessionID = activeConversation?.serverSessionID {
                    recovered = await reconcileCompletedTurn(
                        client: recoveryClient,
                        sessionID: sessionID,
                        userText: text,
                        responseID: responseID,
                        attempts: 12
                    )
                } else {
                    recovered = false
                }
                if recovered {
                    connectionState = .online
                } else {
                    if messages.last?.id == responseID { messages.removeLast() }
                    append(ChatMessage(role: .error, text: error.localizedDescription))
                    if case JarvisAPIError.server = error {
                        connectionState = .online
                    } else {
                        connectionState = .offline(error.localizedDescription)
                    }
                }
            }
            isAwaitingReply = false
            inFlight = nil
            persist()
        }
    }

    private var activeConversation: ChatConversation? {
        conversations.first(where: { $0.id == activeConversationID })
    }

    private func streamReply(
        client: JarvisAPIClient,
        text: String,
        sessionID: String?,
        responseID: UUID
    ) async throws -> String? {
        try await client.stream(
            message: text,
            sessionID: sessionID,
            onSession: { [weak self] id in self?.setServerSessionID(id) }
        ) { [weak self] chunk in
            self?.appendChunk(chunk, to: responseID)
        }
    }

    /// Fetch the server-owned transcript after a stream ends or disconnects.
    /// A matching user turn must be followed by an assistant turn, preventing
    /// an older answer from being mistaken for recovery of the current request.
    private func reconcileCompletedTurn(
        client: JarvisAPIClient,
        sessionID: String,
        userText: String,
        responseID: UUID,
        attempts: Int
    ) async -> Bool {
        for attempt in 0..<max(1, attempts) {
            guard !Task.isCancelled else { return false }
            do {
                let remote = try await client.messages(for: sessionID)
                if let userIndex = remote.lastIndex(where: {
                    $0.role == "user" && $0.content == userText
                }) {
                    let replyIndex = remote.index(after: userIndex)
                    if replyIndex < remote.endIndex, remote[replyIndex].role == "assistant" {
                        let reply = remote[replyIndex].content.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !reply.isEmpty {
                            replaceMessage(responseID, text: reply)
                            return true
                        }
                    }
                }
            } catch {
                // The original error is more useful if recovery never succeeds.
            }
            if attempt + 1 < attempts {
                try? await Task.sleep(for: .seconds(2))
            }
        }
        return false
    }

    private func setServerSessionID(_ id: String?) {
        guard let index = conversations.firstIndex(where: { $0.id == activeConversationID }) else { return }
        conversations[index].serverSessionID = id
        persist()
    }

    private func appendChunk(_ chunk: String, to id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        let current = messages[index]
        messages[index] = ChatMessage(id: current.id, role: .jarvis, text: current.text + chunk, date: current.date)
        // Persist once when the turn completes. Rewriting the entire archive
        // for every streamed character causes visible typing and scrolling
        // stalls on longer conversations.
    }

    private func replaceMessage(_ id: UUID, text: String) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        let current = messages[index]
        messages[index] = ChatMessage(id: id, role: current.role, text: text, date: current.date)
        persist()
    }

    private func append(_ message: ChatMessage) {
        messages.append(message)
        persist()
    }

    private func recoverRetryState() {
        lastSentText = nil
        if messages.last?.role == .error,
           let unanswered = messages.dropLast().last,
           unanswered.role == .user {
            lastSentText = unanswered.text
        }
    }

    private func persist() {
        if let index = conversations.firstIndex(where: { $0.id == activeConversationID }) {
            conversations[index].messages = messages
            conversations[index].draft = draft.isEmpty ? nil : draft
            conversations[index].updatedAt = messages.last?.date ?? conversations[index].updatedAt
        }
        store.save(ChatArchive(
            conversations: conversations,
            activeConversationID: activeConversationID,
            inactiveSince: inactiveSince
        ))
    }
}
