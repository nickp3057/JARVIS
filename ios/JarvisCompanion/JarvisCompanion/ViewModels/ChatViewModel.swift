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
    @Published var historyError: String?

    private let settings: AppSettings
    private let makeClient: () throws -> JarvisAPIClient
    private let store: ChatStore
    private let inactivityLimit: TimeInterval
    private var activeConversationID: UUID
    private var inactiveSince: Date?
    private var lastSentText: String?
    private var inFlight: Task<Void, Never>?
    private var generation: UUID?
    private var pendingEdits: [HistoryEdit] = []
    private var flushingEdits = false
    private var deletedSessionIDs: Set<String> = []
    private var draftSave: Task<Void, Never>?
    private var connectionCheck: Task<Void, Never>?

    init(
        settings: AppSettings,
        store: ChatStore = JSONFileChatStore(),
        inactivityLimit: TimeInterval = 5 * 60,
        now: Date = Date(),
        clientFactory: (() throws -> JarvisAPIClient)? = nil
    ) {
        self.settings = settings
        self.makeClient = clientFactory ?? { try JarvisAPIClient.make(from: settings) }
        self.store = store
        self.inactivityLimit = inactivityLimit
        let archive = store.load()
        pendingEdits = archive.pendingEdits ?? []
        deletedSessionIDs = Set(pendingEdits.filter { $0.title == nil }.map(\.sessionID))
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
        if messages.last?.role == .jarvis, messages.last?.text.isEmpty == true {
            messages.removeLast()
            append(ChatMessage(role: .error, text: "The connection ended before a reply arrived. Retry to recover this turn."))
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

    var conversationID: UUID { activeConversationID }

    func filteredConversations(matching query: String) -> [ChatConversation] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return visibleConversations }
        return visibleConversations.filter { conversation in
            conversation.title.localizedCaseInsensitiveContains(needle)
                || conversation.messages.contains { $0.text.localizedCaseInsensitiveContains(needle) }
        }
    }

    func appBecameInactive(at date: Date = Date()) {
        if inactiveSince == nil { inactiveSince = date }
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
                try await makeClient().checkHealth()
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
            let client = try makeClient()
            await flushHistoryEdits()
            let remoteSessions = try await client.sessions()
            guard !Task.isCancelled else { return }
            for remote in remoteSessions {
                if pendingEdits.contains(where: { $0.sessionID == remote.sessionID && $0.title == nil }) { continue }
                let remoteMessages = try await client.messages(for: remote.sessionID)
                guard !Task.isCancelled else { return }
                if deletedSessionIDs.contains(remote.sessionID) { continue }
                let existing = conversations.first(where: { $0.serverSessionID == remote.sessionID })
                let converted = remoteMessages.compactMap { item -> ChatMessage? in
                    let role: ChatMessage.Role
                    switch item.role {
                    case "user": role = .user
                    case "assistant": role = .jarvis
                    default: return nil
                    }
                    let previous = existing?.messages.first(where: {
                        (item.id != nil && $0.serverID == item.id)
                            || (item.requestID != nil && $0.requestID == item.requestID && $0.role == role)
                    })
                    return ChatMessage(id: previous?.id ?? UUID(), role: role, text: item.content,
                                       date: parseServerDate(item.createdAt), serverID: item.id, requestID: item.requestID)
                }
                guard !converted.isEmpty else { continue }

                if let index = conversations.firstIndex(where: { $0.serverSessionID == remote.sessionID }) {
                    // Never replace a live stream or an unresolved local turn.
                    if conversations[index].id == activeConversationID && isAwaitingReply { continue }
                    let local = conversations[index].messages
                    if let pendingUser = local.last(where: { $0.role == .user }),
                       let requestID = pendingUser.requestID,
                       !converted.contains(where: { $0.requestID == requestID && $0.role == .jarvis }) {
                        continue
                    }
                    conversations[index].messages = converted
                    if !pendingEdits.contains(where: { $0.sessionID == remote.sessionID }),
                       let title = remote.title, !title.isEmpty { conversations[index].customTitle = title }
                    conversations[index].updatedAt = converted.last?.date ?? conversations[index].updatedAt
                    if conversations[index].id == activeConversationID {
                        messages = conversations[index].messages
                        recoverRetryState()
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
        queueHistoryEdit(sessionID: serverID, title: String(clean.prefix(120)))
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
        deletedSessionIDs.insert(serverID)
        queueHistoryEdit(sessionID: serverID, title: nil)
    }

    func clearTranscript() {
        guard !messages.isEmpty else { return }
        deleteConversation(activeConversationID)
        if !messages.isEmpty { startNewConversation() }
    }

    func draftChanged() {
        draftSave?.cancel()
        draftSave = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(350)) } catch { return }
            self?.persist()
        }
    }

    private func queueHistoryEdit(sessionID: String, title: String?) {
        pendingEdits.removeAll { $0.sessionID == sessionID }
        pendingEdits.append(HistoryEdit(sessionID: sessionID, title: title))
        persist()
        Task { await flushHistoryEdits() }
    }

    private func flushHistoryEdits() async {
        guard !flushingEdits else { return }
        flushingEdits = true
        defer { flushingEdits = false }
        do {
            let client = try makeClient()
            while let edit = pendingEdits.first {
                do {
                    if let title = edit.title {
                        try await client.renameSession(edit.sessionID, title: title)
                    } else {
                        try await client.deleteSession(edit.sessionID)
                    }
                } catch JarvisAPIError.server(let status, _) where status == 404 && edit.title == nil {
                    // Already absent on Core is a successful deletion.
                }
                pendingEdits.removeAll { $0.id == edit.id }
                persist()
            }
            historyError = nil
        } catch {
            historyError = "History changes are saved on this device and will retry when you reconnect. " + error.localizedDescription
        }
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isAwaitingReply else { return }
        draft = ""
        let requestID = UUID().uuidString
        append(ChatMessage(role: .user, text: text, requestID: requestID))
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
            let reportID = try await makeClient().submitDebugReport(
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
        guard isAwaitingReply else { return }
        generation = nil
        inFlight?.cancel()
        inFlight = nil
        isAwaitingReply = false
        if messages.last?.role == .jarvis { messages.removeLast() }
        append(ChatMessage(role: .error, text: "Stopped receiving. JARVIS may still finish this turn; reconnect or retry to recover its result."))
        recoverRetryState()
    }

    private func request(_ text: String) {
        lastSentText = text
        isAwaitingReply = true
        let turn = UUID()
        generation = turn
        let conversationID = activeConversationID
        let requestID = messages.last(where: { $0.role == .user })?.requestID ?? UUID().uuidString
        if let index = messages.lastIndex(where: { $0.role == .user }), messages[index].requestID == nil {
            let message = messages[index]
            messages[index] = ChatMessage(id: message.id, role: .user, text: message.text,
                                          date: message.date, serverID: message.serverID, requestID: requestID)
        }
        let responseID = UUID()
        append(ChatMessage(id: responseID, role: .jarvis, text: "", requestID: requestID))
        var sessionID = activeConversation?.serverSessionID

        inFlight = Task { [weak self] in
            guard let self else { return }
            let isCurrent = { self.generation == turn && self.activeConversationID == conversationID && !Task.isCancelled }
            var recoveryClient: JarvisAPIClient?
            do {
                let client = try makeClient()
                recoveryClient = client
                if sessionID == nil {
                    sessionID = try await client.createSession()
                    guard isCurrent() else { return }
                    setServerSessionID(sessionID)
                }
                do {
                    _ = try await client.stream(message: text, sessionID: sessionID, requestID: requestID,
                        onSession: { [weak self] id in
                            guard isCurrent() else { return }
                            self?.setServerSessionID(id)
                        }, onChunk: { [weak self] chunk in
                            guard isCurrent() else { return }
                            self?.appendChunk(chunk, to: responseID)
                        })
                } catch JarvisAPIError.server(let status, _) where status == 404 {
                    guard isCurrent() else { return }
                    // Preserve the same session and request on legacy Core.
                    let reply = try await client.send(message: text, sessionID: sessionID, requestID: requestID)
                    guard isCurrent() else { return }
                    replaceMessage(responseID, text: reply.reply)
                }
                guard isCurrent() else { return }
                if let sessionID {
                    _ = await reconcileCompletedTurn(client: client, sessionID: sessionID,
                        requestID: requestID, responseID: responseID, attempts: 2)
                }
                guard isCurrent() else { return }
                if messages.last?.id == responseID, messages.last?.text.isEmpty == true {
                    throw JarvisAPIError.malformedResponse("No completed reply was received.")
                }
                connectionState = .online
            } catch {
                guard isCurrent() else { return }
                var recovered = false
                if let client = recoveryClient, let sessionID {
                    recovered = await reconcileCompletedTurn(client: client, sessionID: sessionID,
                        requestID: requestID, responseID: responseID, attempts: 12)
                }
                guard isCurrent() else { return }
                if recovered {
                    connectionState = .online
                } else {
                    if messages.last?.id == responseID { messages.removeLast() }
                    append(ChatMessage(role: .error, text: error.localizedDescription))
                    if case JarvisAPIError.transport = error {
                        connectionState = .offline(error.localizedDescription)
                    }
                }
            }
            guard isCurrent() else { return }
            isAwaitingReply = false
            generation = nil
            inFlight = nil
            persist()
        }
    }

    private var activeConversation: ChatConversation? {
        conversations.first(where: { $0.id == activeConversationID })
    }

    private func reconcileCompletedTurn(
        client: JarvisAPIClient,
        sessionID: String,
        requestID: String,
        responseID: UUID,
        attempts: Int
    ) async -> Bool {
        for attempt in 0..<max(1, attempts) {
            guard !Task.isCancelled else { return false }
            do {
                let remote = try await client.messages(for: sessionID)
                guard !Task.isCancelled else { return false }
                if let userIndex = remote.lastIndex(where: {
                    $0.role == "user" && $0.requestID == requestID
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
        messages[index] = ChatMessage(id: current.id, role: .jarvis, text: current.text + chunk, date: current.date, serverID: current.serverID, requestID: current.requestID)
        // Persist once when the turn completes. Rewriting the entire archive
        // for every streamed character causes visible typing and scrolling
        // stalls on longer conversations.
    }

    private func replaceMessage(_ id: UUID, text: String) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        let current = messages[index]
        messages[index] = ChatMessage(id: id, role: current.role, text: text, date: current.date, serverID: current.serverID, requestID: current.requestID)
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
            inactiveSince: inactiveSince,
            pendingEdits: pendingEdits
        ))
    }
}
