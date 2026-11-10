import Foundation
import SwiftUI
import UserNotifications

/// Opens and closes Donna mode from anywhere (the Chat tab's logo hold).
@MainActor
final class DonnaPresenter: ObservableObject {
    static let shared = DonnaPresenter()
    @Published var showing = false
}

/// Donna mode's state: this session's messages, sending, ending, and the
/// check-ins she promised (scheduled here as local notifications, so nothing
/// about them leaves the phone except through her own endpoint).
@MainActor
final class DonnaModel: ObservableObject {
    @Published private(set) var messages: [DonnaMessage] = []
    @Published private(set) var thinking = false
    @Published private(set) var loading = false
    @Published var draft = ""
    @Published var error: String?
    @Published private(set) var hasCaseFile = false

    private let settings: AppSettings

    init(settings: AppSettings) {
        self.settings = settings
    }

    private func client() throws -> JarvisAPIClient { try JarvisAPIClient.make(from: settings) }

    func load() async {
        loading = true
        defer { loading = false }
        do {
            let state = try await client().donnaState()
            messages = state.session?.messages ?? []
            hasCaseFile = state.hasCaseFile
            await Self.schedule(state.checkins)
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
                let reply = try await client().donnaSend(text)
                messages.append(DonnaMessage(role: "assistant", text: reply.reply, at: Date().timeIntervalSince1970))
                await Self.schedule(reply.checkins)
                error = nil
            } catch {
                // Put the words back so nothing he wrote is lost.
                if messages.last?.text == text { messages.removeLast() }
                draft = text
                self.error = error.localizedDescription
            }
        }
    }

    /// End now: she writes her note; the next message starts a new session.
    func endSession() async {
        guard !messages.isEmpty else { return }
        do {
            try await client().donnaEnd()
            messages = []
            hasCaseFile = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    func importHistory(_ text: String) async -> Bool {
        do {
            _ = try await client().donnaImport(text)
            hasCaseFile = true
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    func memory() async throws -> DonnaMemory { try await client().donnaMemory() }

    func forgetEverything() async {
        do {
            try await client().donnaForget()
            messages = []
            hasCaseFile = false
            await Self.schedule([])
        } catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: - Check-ins

    private static let prefix = "donna-checkin-"

    /// Mirror Core's pending check-ins as local notifications: discreet text,
    /// replaced wholesale so a cancelled one disappears.
    static func schedule(_ checkins: [DonnaCheckin]) async {
        let center = UNUserNotificationCenter.current()
        let pending = await center.pendingNotificationRequests()
        center.removePendingNotificationRequests(withIdentifiers: pending.map(\.identifier).filter { $0.hasPrefix(prefix) })
        for checkin in checkins where checkin.dueAt > Date().timeIntervalSince1970 {
            let content = UNMutableNotificationContent()
            content.title = "Donna"
            content.body = checkin.message
            content.sound = .default
            let date = Date(timeIntervalSince1970: checkin.dueAt)
            let parts = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: date)
            let request = UNNotificationRequest(identifier: prefix + String(checkin.id), content: content,
                                                trigger: UNCalendarNotificationTrigger(dateMatching: parts, repeats: false))
            try? await center.add(request)
        }
    }
}
