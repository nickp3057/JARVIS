import Foundation
import SwiftUI
import UserNotifications

/// Opens and closes Donna mode from anywhere (the Chat tab's logo: tap twice, then hold).
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
    /// What was just sent, so the keyboard re-committing it after the box
    /// is cleared (predictive text does this) can be thrown away.
    private var lastSent: (text: String, at: Date)?

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
        lastSent = (draft, Date())
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
                // The request often dies while Core carries on (the app went
                // to the background, Wi-Fi handed off): her answer is saved
                // there. Fetch it before calling this a failure.
                if await recover(sent: text) { return }
                // Put the words back so nothing he wrote is lost.
                if messages.last?.text == text { messages.removeLast() }
                draft = text
                self.error = error.localizedDescription
            }
        }
    }

    /// After a failed send: wait for Core's copy of her reply (it may still be
    /// writing it). True once the session shows `sent` followed by an answer.
    private func recover(sent text: String) async -> Bool {
        for attempt in 0..<20 {
            if attempt > 0 { try? await Task.sleep(for: .seconds(3)) }
            guard let state = try? await client().donnaState() else { continue }
            let remote = state.session?.messages ?? []
            guard let asked = remote.lastIndex(where: { $0.fromNick && $0.text == text }) else {
                return false  // it never reached her
            }
            if remote.index(after: asked) < remote.endIndex {
                messages = remote
                await Self.schedule(state.checkins)
                error = nil
                return true
            }
        }
        return false
    }

    /// Back from the background: show anything that landed meanwhile
    /// (a reply finished on Core, or a message sent from the Mac).
    func refresh() async {
        guard !thinking, !loading else { return }
        await load()
    }

    /// True when the box suddenly refills with the message that just went.
    func isEcho(_ text: String) -> Bool {
        guard let lastSent, !text.isEmpty, Date().timeIntervalSince(lastSent.at) < 1.5 else { return false }
        let sent = lastSent.text.trimmingCharacters(in: .whitespacesAndNewlines)
        let echoed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return echoed == sent
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
