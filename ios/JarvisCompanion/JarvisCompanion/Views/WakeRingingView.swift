import SwiftUI

/// Set when Nick taps the wake-up push itself (not one of its buttons): the
/// app opens straight to the alarm with I'm up / Snooze.
@MainActor
final class WakeRinging: ObservableObject {
    static let shared = WakeRinging()
    @Published var showing = false
    @Published var detail = ""
}

/// Full-screen alarm: two big buttons, no animation. Both tell JARVIS Core,
/// which tells the TV (the same calls as the notification's buttons).
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
                Button("Close") { ringing.showing = false }
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
                    ringing.showing = false
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
