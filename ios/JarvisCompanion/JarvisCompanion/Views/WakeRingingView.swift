import SwiftUI

/// The alarm screen's state. It opens when Nick taps the wake-up push, when
/// the push lands while the app is open, and whenever the app is open while
/// Core says an alarm is going off (RootView polls `GET /api/wake`), and it
/// closes itself once the alarm is answered anywhere.
@MainActor
final class WakeRinging: ObservableObject {
    static let shared = WakeRinging()
    @Published var showing = false
    @Published var detail = ""
    /// When a snooze rings again; nil while ringing.
    @Published var snoozedUntil: Date?
    /// The alarm on screen (Core's `since`) and the one Nick closed, so Close
    /// sticks for that alarm but the next one (a snooze ending) opens again.
    private var since: Double?
    private var closed: Double?

    func follow(_ ringing: WakeSchedule.Ringing?) {
        guard let ringing else {
            showing = false
            snoozedUntil = nil
            return
        }
        since = ringing.since
        if !ringing.detail.isEmpty { detail = ringing.detail }
        snoozedUntil = ringing.until.map { Date(timeIntervalSince1970: $0) }
        if ringing.since != closed, !showing { showing = true }
    }

    func close() {
        closed = since
        showing = false
    }
}

/// Full-screen alarm: two big buttons, no animation. Both tell JARVIS Core,
/// which tells the TV (the same calls as the notification's buttons); I'm up
/// stops the TV's music. While snoozed it shows when it rings again.
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
                if let until = ringing.snoozedUntil {
                    Text("Snoozed. Back at \(until.formatted(date: .omitted, time: .shortened)).")
                        .font(.callout).foregroundStyle(.secondary)
                } else if !ringing.detail.isEmpty {
                    Text(ringing.detail).font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                Spacer()
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                }
                if ringing.snoozedUntil == nil {
                    button("Snooze 9 min", symbol: "zzz", tint: JarvisTheme.blue) { try await $0.snoozeWakeUp() }
                }
                button("I'm up", symbol: "sun.max", tint: JarvisTheme.cyan) { try await $0.wakeUpAwake() }
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
