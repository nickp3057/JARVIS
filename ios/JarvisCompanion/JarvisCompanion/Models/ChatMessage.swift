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

    init(id: UUID = UUID(), role: Role, text: String, date: Date = Date()) {
        self.id = id
        self.role = role
        self.text = text
        self.date = date
    }
}

/// One device-local conversation. The server does not know about these yet;
/// they provide app scrollback until server-side sessions are available.
struct ChatConversation: Identifiable, Codable, Equatable {
    let id: UUID
    let createdAt: Date
    var updatedAt: Date
    var messages: [ChatMessage]

    init(
        id: UUID = UUID(),
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        messages: [ChatMessage] = []
    ) {
        self.id = id
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.messages = messages
    }

    var title: String {
        messages.first(where: { $0.role == .user })?.text ?? "New chat"
    }

    var preview: String {
        messages.last?.text ?? "No messages yet"
    }
}

struct ChatArchive: Codable, Equatable {
    var conversations: [ChatConversation]
    var activeConversationID: UUID
    var inactiveSince: Date?

    static func empty(now: Date = Date()) -> ChatArchive {
        let conversation = ChatConversation(createdAt: now, updatedAt: now)
        return ChatArchive(
            conversations: [conversation],
            activeConversationID: conversation.id,
            inactiveSince: nil
        )
    }
}
