import Foundation

/// The conversation in progress, mirroring the phone's DonnaModel. Core
/// keeps one shared session, so a chat started on the phone continues here.
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
            let state = try await DonnaAPI.make(settings).state()
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
                let reply = try await DonnaAPI.make(settings).send(text)
                messages.append(DonnaMessage(role: "assistant", text: reply.reply, at: Date().timeIntervalSince1970))
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
        guard !messages.isEmpty, !thinking else { return }
        do {
            try await DonnaAPI.make(settings).end()
            messages = []
            hasCaseFile = true
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func memory() async throws -> DonnaMemory { try await DonnaAPI.make(settings).memory() }
}
