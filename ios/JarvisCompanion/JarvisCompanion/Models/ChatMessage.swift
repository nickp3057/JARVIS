import Foundation

/// One line in the transcript.
///
/// `Codable` so the whole conversation can be written to disk as JSON by the
/// `ChatStore`. Errors are modelled as a message role rather than as separate
/// view state so a failed turn stays anchored to the point in the conversation
/// where it happened.
struct ChatMessage: Identifiable, Codable, Equatable {
    enum Role: String, Codable {
        case user
        case jarvis
        case error
    }

    let id: UUID
    let role: Role
    let text: String
    let date: Date
    let serverID: Int?
    let requestID: String?

    init(id: UUID = UUID(), role: Role, text: String, date: Date = Date(), serverID: Int? = nil, requestID: String? = nil) {
        self.id = id
        self.role = role
        self.text = text
        self.date = date
        self.serverID = serverID
        self.requestID = requestID
    }
}

/// Offline cache of a server-owned conversation, including its local draft.
struct ChatConversation: Identifiable, Codable, Equatable {
    let id: UUID
    let createdAt: Date
    var updatedAt: Date
    var messages: [ChatMessage]
    var customTitle: String?
    var serverSessionID: String?
    var serverLastMessageID: Int?
    var draft: String?

    init(
        id: UUID = UUID(),
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        messages: [ChatMessage] = [],
        customTitle: String? = nil,
        serverSessionID: String? = nil,
        draft: String? = nil,
        serverLastMessageID: Int? = nil
    ) {
        self.id = id
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.messages = messages
        self.customTitle = customTitle
        self.serverSessionID = serverSessionID
        self.serverLastMessageID = serverLastMessageID
        self.draft = draft
    }

    var title: String {
        if let customTitle, !customTitle.isEmpty { return customTitle }
        return messages.first(where: { $0.role == .user })?.text ?? "New chat"
    }

    var preview: String {
        // Round breaks (U+001E) separate bubbles; read as spaces here.
        messages.last?.text.replacingOccurrences(of: "\u{1E}", with: " ") ?? "No messages yet"
    }
}

struct ChatArchive: Codable, Equatable {
    var conversations: [ChatConversation]
    var activeConversationID: UUID
    var inactiveSince: Date?
    var pendingEdits: [HistoryEdit]? = nil

    static func empty(now: Date = Date()) -> ChatArchive {
        let conversation = ChatConversation(createdAt: now, updatedAt: now)
        return ChatArchive(
            conversations: [conversation],
            activeConversationID: conversation.id,
            inactiveSince: nil
        )
    }
}

struct HistoryEdit: Codable, Equatable, Identifiable {
    var id: UUID = UUID()
    let sessionID: String
    /// Nil means delete; otherwise rename to this title.
    let title: String?
}
