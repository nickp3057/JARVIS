import Foundation

/// Owns the transcript and the single in-flight request.
///
/// The app is a thin client: this type sends a string and appends whatever
/// comes back. It has no idea which tools JARVIS ran to produce the reply.
@MainActor
final class ChatViewModel: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var conversations: [ChatConversation] = []
    @Published private(set) var isAwaitingReply = false
    @Published var draft = ""

    private let settings: AppSettings
    private let store: ChatStore
    private let inactivityLimit: TimeInterval
    private var activeConversationID: UUID
    private var inactiveSince: Date?

    /// The text of the last message sent, kept so a failed turn can be retried
    /// without the user retyping it.
    private var lastSentText: String?
    private var inFlight: Task<Void, Never>?

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
        self.conversations = archive.conversations
        self.activeConversationID = archive.activeConversationID
        self.inactiveSince = archive.inactiveSince
        self.messages = archive.conversations.first(where: { $0.id == archive.activeConversationID })?.messages ?? []

        if let inactiveSince = archive.inactiveSince,
           now.timeIntervalSince(inactiveSince) >= inactivityLimit,
           !messages.isEmpty {
            startNewConversation(now: now)
        } else {
            self.inactiveSince = nil
            persist()
        }

        // A turn that failed before the app was last closed stays retryable:
        // recover the text from the user message the trailing error belongs to.
        if messages.last?.role == .error,
           let unanswered = messages.dropLast().last,
           unanswered.role == .user {
            lastSentText = unanswered.text
        }
    }

    var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isAwaitingReply
    }

    /// True when the last turn failed, so the transcript can offer a retry.
    var canRetry: Bool {
        !isAwaitingReply && lastSentText != nil && messages.last?.role == .error
    }

    var visibleConversations: [ChatConversation] {
        conversations
            .filter { !$0.messages.isEmpty }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    func appBecameInactive(at date: Date = Date()) {
        inactiveSince = date
        persist()
    }

    func appBecameActive(at date: Date = Date()) {
        guard let inactiveSince else { return }
        self.inactiveSince = nil

        if date.timeIntervalSince(inactiveSince) >= inactivityLimit, !messages.isEmpty {
            startNewConversation(now: date)
        } else {
            persist()
        }
    }

    func startNewConversation(now: Date = Date()) {
        inFlight?.cancel()
        inFlight = nil
        isAwaitingReply = false
        lastSentText = nil
        inactiveSince = nil

        if messages.isEmpty { return }

        let conversation = ChatConversation(createdAt: now, updatedAt: now)
        conversations.append(conversation)
        activeConversationID = conversation.id
        messages = []
        persist()
    }

    func openConversation(_ id: UUID) {
        guard id != activeConversationID,
              let conversation = conversations.first(where: { $0.id == id }) else { return }

        inFlight?.cancel()
        inFlight = nil
        isAwaitingReply = false
        activeConversationID = id
        messages = conversation.messages
        lastSentText = nil

        if messages.last?.role == .error,
           let unanswered = messages.dropLast().last,
           unanswered.role == .user {
            lastSentText = unanswered.text
        }
        persist()
    }

    /// Called whenever the composer text changes.
    ///
    /// The composer is a vertical-axis `TextField`, and on that kind of field
    /// the Return key inserts a newline instead of firing `onSubmit` — so
    /// pressing Return used to do nothing at all, leaving the message sitting
    /// unsent in the composer where it was lost on quit. Treat a typed newline
    /// as an instruction to send.
    func draftChanged() {
        guard draft.contains("\n") else { return }

        draft = draft.replacingOccurrences(of: "\n", with: "")
        send()
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isAwaitingReply else { return }

        draft = ""
        append(ChatMessage(role: .user, text: text))
        request(text)
    }

    /// Drops the error bubble and re-sends the message that produced it. The
    /// user's own bubble stays put — it was delivered to the transcript, just
    /// not answered.
    func retry() {
        guard canRetry, let text = lastSentText else { return }

        messages.removeLast()
        persist()
        request(text)
    }

    func clearTranscript() {
        inFlight?.cancel()
        inFlight = nil
        isAwaitingReply = false
        lastSentText = nil
        messages.removeAll()
        persist()
    }

    private func request(_ text: String) {
        lastSentText = text
        isAwaitingReply = true

        inFlight = Task { [weak self] in
            guard let self else { return }

            do {
                let client = try JarvisAPIClient.make(from: self.settings)
                let reply = try await client.send(message: text)

                guard !Task.isCancelled else { return }
                let trimmed = reply.trimmingCharacters(in: .whitespacesAndNewlines)
                self.append(ChatMessage(role: .jarvis, text: trimmed.isEmpty ? "(no reply)" : trimmed))
            } catch {
                guard !Task.isCancelled else { return }
                let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                self.append(ChatMessage(role: .error, text: message))
            }

            self.isAwaitingReply = false
            self.inFlight = nil
        }
    }

    private func append(_ message: ChatMessage) {
        messages.append(message)
        persist()
    }

    private func persist() {
        if let index = conversations.firstIndex(where: { $0.id == activeConversationID }) {
            conversations[index].messages = messages
            conversations[index].updatedAt = messages.last?.date ?? conversations[index].updatedAt
        }
        store.save(ChatArchive(
            conversations: conversations,
            activeConversationID: activeConversationID,
            inactiveSince: inactiveSince
        ))
    }
}
