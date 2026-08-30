import Foundation
import os

/// Local persistence for the transcript.
///
/// Deliberately a protocol with one dumb JSON-file implementation. Server-side
/// history sync is planned for a later session and will slot in behind this
/// same two-method API, so nothing above this layer should assume a file.
protocol ChatStore {
    func load() -> ChatArchive
    func save(_ archive: ChatArchive)
}

/// Stores the transcript as a single pretty-printed JSON file in Application
/// Support.
///
/// The API stays non-throwing — losing local scrollback is never worth failing
/// a launch over — but failures are logged rather than discarded, so a store
/// that silently stops persisting can actually be diagnosed. Watch for them
/// with: `xcrun simctl spawn booted log stream --predicate
/// 'subsystem == "JarvisCompanion"'`
final class JSONFileChatStore: ChatStore {
    private static let log = Logger(subsystem: "JarvisCompanion", category: "ChatStore")

    private let directory: URL
    private let fileURL: URL
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    init(filename: String = "transcript.json") {
        let base: URL
        do {
            base = try FileManager.default.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )
        } catch {
            // Application Support is unavailable; fall back rather than crash,
            // but make it loud — this is the case where saves would vanish.
            Self.log.error("Application Support unavailable, falling back to tmp: \(error.localizedDescription, privacy: .public)")
            base = URL.temporaryDirectory
        }

        directory = base
        fileURL = base.appending(path: filename)
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        decoder.dateDecodingStrategy = .iso8601
    }

    func load() -> ChatArchive {
        do {
            let data = try Data(contentsOf: fileURL)
            if let archive = try? decoder.decode(ChatArchive.self, from: data) {
                return normalized(archive)
            }

            // Versions 1.0–1.1 stored one bare message array. Promote it to
            // the first conversation without dropping the user's scrollback.
            let messages = try decoder.decode([ChatMessage].self, from: data)
            let now = Date()
            let conversation = ChatConversation(
                createdAt: messages.first?.date ?? now,
                updatedAt: messages.last?.date ?? now,
                messages: messages
            )
            return ChatArchive(
                conversations: [conversation],
                activeConversationID: conversation.id,
                inactiveSince: nil
            )
        } catch CocoaError.fileReadNoSuchFile {
            return .empty()  // First launch: no transcript yet, not an error.
        } catch {
            Self.log.error("Failed to load transcript from \(self.fileURL.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
            return .empty()
        }
    }

    func save(_ archive: ChatArchive) {
        do {
            // Recreate the directory defensively: it is made once at init, but
            // a write into a directory that has since gone missing would
            // otherwise fail every time from then on.
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try encoder.encode(archive)
            try data.write(to: fileURL, options: .atomic)
        } catch {
            Self.log.error("Failed to save transcript to \(self.fileURL.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
    }

    private func normalized(_ archive: ChatArchive) -> ChatArchive {
        guard !archive.conversations.isEmpty else { return .empty() }
        guard archive.conversations.contains(where: { $0.id == archive.activeConversationID }) else {
            var repaired = archive
            repaired.activeConversationID = archive.conversations[0].id
            return repaired
        }
        return archive
    }
}
