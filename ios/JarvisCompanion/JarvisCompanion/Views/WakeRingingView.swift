import SwiftUI

/// The alarm screen's state. It opens when Nick taps the wake-up push, when
/// the push lands while the app is open, and whenever the app is open while
/// Core says an alarm is ringing (RootView polls `GET /api/wake`). Answered
/// anywhere (I'm up or Snooze, on any device), it closes; a snooze ringing
/// again is a new alarm and opens it again.
///
/// The Room tab's night screen is a full-screen cover too, and iOS shows only
/// one at a time: the night screen stays down while the alarm is up
/// (`RoomView.roomIsDark`), and opening the alarm over it drops it first.
@MainActor
final class WakeRinging: ObservableObject {
    static let shared = WakeRinging()
    @Published var showing = false
    @Published var detail = ""
    /// Waiting for the night screen to go before the alarm screen opens.
    @Published private(set) var opening = false
    /// The Room tab's night screen is up (RoomView keeps this current).
    var nightScreenUp = false
    /// The alarm on screen (Core's `since`) and the one Nick closed, so Close
    /// sticks for that alarm but the next one (a snooze ending) opens again.
    private var since: Double?
    private var closed: Double?

    func follow(_ ringing: WakeSchedule.Ringing?) {
        // Snoozed counts as answered: the screen closes everywhere.
        guard let ringing, ringing.state == "ringing" else {
            showing = false
            return
        }
        since = ringing.since
        if !ringing.detail.isEmpty { detail = ringing.detail }
        if ringing.since != closed { open() }
    }

    /// Show the alarm screen, taking the night screen down first if it's up.
    func open() {
        guard !showing, !opening else { return }
        guard nightScreenUp else { showing = true; return }
        opening = true
        Task {
            try? await Task.sleep(for: .milliseconds(700))
            opening = false
            showing = true
        }
    }

    func close() {
        closed = since
        showing = false
    }
}

/// Full-screen alarm: three big buttons, no animation. All tell JARVIS Core,
/// which tells the TV (the same calls as the notification's buttons). I'm up
/// stops the TV's music and runs the good-morning lights; "I'm awake, leave
/// the lights" stops the alarm and nothing else, so the room stays as it is.
struct WakeRingingView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var ringing = WakeRinging.shared
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        ZStack {
            JarvisBackdrop().ignoresSafeArea()
            VStack(spacing: 28) {
                Spacer()
                Text(Date.now, format: .dateTime.hour().minute())
                    .font(.system(size: 76, weight: .thin, design: .rounded))
                    .monospacedDigit()
                Text("Good morning, sir").font(.title2.weight(.semibold))
                if !ringing.detail.isEmpty {
                    Text(ringing.detail).font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                Spacer()
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                }
                button("Snooze 9 min", symbol: "zzz", tint: JarvisTheme.blue) { try await $0.snoozeWakeUp() }
                button("I'm up", symbol: "sun.max", tint: JarvisTheme.cyan) { try await $0.wakeUpAwake() }
                button("I'm awake, leave the lights", symbol: "lightbulb.slash", tint: JarvisTheme.blue) {
                    try await $0.wakeUpAwake(lights: false)
                }
                Button("Close") { ringing.close() }
                    .font(.footnote).foregroundStyle(.secondary)
            }
            .padding(24)
        }
        .disabled(busy)
    }

    private func button(_ title: String, symbol: String, tint: Color,
                        _ action: @escaping (JarvisAPIClient) async throws -> Void) -> some View {
        Button {
            Task {
                busy = true
                defer { busy = false }
                do {
                    try await action(try JarvisAPIClient.make(from: settings))
                    ringing.close()
                } catch {
                    self.error = error.localizedDescription
                }
            }
        } label: {
            Label(title, systemImage: symbol)
                .font(.title3.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 64)
                .background(tint.opacity(0.22), in: RoundedRectangle(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(tint.opacity(0.6)))
        }
        .buttonStyle(CockpitButtonStyle())
    }
}
