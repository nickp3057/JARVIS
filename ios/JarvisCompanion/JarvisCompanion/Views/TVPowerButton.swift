import SwiftUI

/// The square power button beside "TV remote" on the iPad's Room tab: the
/// room TV on or off in one tap (`POST /api/tv/remote` power_on/power_off,
/// through Home Assistant's Android TV Remote). Lit amber while the TV is on
/// (`GET /api/tv/power`, checked every 15 s while the Room tab is up and
/// again a few seconds after a press, since the TV takes a moment).
struct TVPowerButton: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.scenePhase) private var scenePhase
    var size: CGFloat = 52
    @State private var on: Bool?
    @State private var busy = false
    @State private var failed = 0

    var body: some View {
        Button(action: press) {
            ZStack {
                if busy {
                    ProgressView()
                } else {
                    Image(systemName: "power").font(.title2.weight(.semibold))
                }
            }
            .foregroundStyle(on == true ? JarvisTheme.amber : JarvisTheme.cyan)
            .frame(width: size, height: size)
            .background((on == true ? JarvisTheme.amber : JarvisTheme.cyan).opacity(0.14), in: .rect(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14)
                .strokeBorder((on == true ? JarvisTheme.amber : JarvisTheme.cyan).opacity(0.45)))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(busy)
        .sensoryFeedback(.impact(weight: .medium), trigger: on)
        .sensoryFeedback(.error, trigger: failed)
        .accessibilityLabel(on == true ? "Turn the TV off" : "Turn the TV on")
        .task(id: scenePhase) {
            while scenePhase == .active, !Task.isCancelled {
                if !busy { await load() }
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    private func load() async {
        if let state = try? await JarvisAPIClient.make(from: settings).tvPowerState() { on = state }
    }

    private func press() {
        let target = !(on ?? false)
        busy = true
        Task {
            defer { busy = false }
            do {
                try await JarvisAPIClient.make(from: settings).pressTVRemote(target ? "power_on" : "power_off")
                on = target
                try? await Task.sleep(for: .seconds(4))
                await load()
            } catch {
                failed += 1
            }
        }
    }
}
