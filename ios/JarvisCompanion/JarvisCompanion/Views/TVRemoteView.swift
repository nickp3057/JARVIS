import SwiftUI

/// A remote for the TV in Nick's room: each press goes to JARVIS Core
/// (`POST /api/tv/remote`), which sends it through Home Assistant's Android
/// TV Remote connection, so it works whatever app is on screen. A 2x2 at the
/// bottom opens Plex, YouTube, Spotify or the TV's Settings.
struct TVRemoteView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @State private var error: String?
    @State private var tick = 0

    var body: some View {
        NavigationStack {
            VStack(spacing: 22) {
                HStack(spacing: 16) {
                    pill("On", symbol: "power", key: "power_on")
                    pill("Off", symbol: "poweroff", key: "power_off")
                }
                dpad
                HStack(spacing: 22) {
                    round("arrow.uturn.backward", label: "Back", key: "back")
                    round("house", label: "Home", key: "home")
                    round("playpause", label: "Play or pause", key: "play_pause")
                }
                HStack(spacing: 22) {
                    round("speaker.minus", label: "Volume down", key: "volume_down", repeats: true)
                    round("speaker.slash", label: "Mute", key: "mute")
                    round("speaker.plus", label: "Volume up", key: "volume_up", repeats: true)
                }
                apps
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                        .multilineTextAlignment(.center)
                }
                Spacer(minLength: 0)
            }
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background { JarvisBackdrop() }
            .navigationTitle("TV remote").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .sensoryFeedback(.impact(weight: .light), trigger: tick)
        }
    }

    /// Quick launch: opens the app on the TV (waking it if needed).
    private var apps: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
            app("Plex", symbol: "play.rectangle.fill", key: "app_plex")
            app("YouTube", symbol: "play.tv.fill", key: "app_youtube")
            app("Spotify", symbol: "music.note", key: "app_spotify")
            app("Settings", symbol: "gearshape.fill", key: "app_settings")
        }
        .frame(maxWidth: 360)
    }

    private func app(_ title: String, symbol: String, key: String) -> some View {
        Button { press(key) } label: {
            Label(title, systemImage: symbol).font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 46)
                .jarvisSurface(in: .rect(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel("Open \(title) on the TV")
    }

    private var dpad: some View {
        let size: CGFloat = 230
        return ZStack {
            Circle().fill(JarvisTheme.panelGradient)
                .overlay(Circle().strokeBorder(JarvisTheme.border))
            VStack {
                arrow("chevron.up", label: "Up", key: "up")
                Spacer()
                arrow("chevron.down", label: "Down", key: "down")
            }
            .padding(.vertical, 10)
            HStack {
                arrow("chevron.left", label: "Left", key: "left")
                Spacer()
                arrow("chevron.right", label: "Right", key: "right")
            }
            .padding(.horizontal, 10)
            Button { press("ok") } label: {
                Text("OK").font(.headline)
                    .frame(width: 84, height: 84)
                    .foregroundStyle(JarvisTheme.background)
                    .background(JarvisTheme.reactor, in: .circle)
            }
            .buttonStyle(CockpitButtonStyle())
            .accessibilityLabel("Select")
        }
        .frame(width: size, height: size)
    }

    private func arrow(_ symbol: String, label: String, key: String) -> some View {
        Button { press(key) } label: {
            Image(systemName: symbol).font(.title2.weight(.semibold))
                .frame(width: 60, height: 60).contentShape(Rectangle())
                .foregroundStyle(JarvisTheme.cyan)
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel(label)
    }

    private func round(_ symbol: String, label: String, key: String, repeats: Bool = false) -> some View {
        Button { press(key) } label: {
            Image(systemName: symbol).font(.title3)
                .frame(width: 64, height: 64)
                .jarvisSurface(in: .circle)
                .overlay(Circle().strokeBorder(JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .buttonRepeatBehavior(repeats ? .enabled : .disabled)
        .accessibilityLabel(label)
    }

    private func pill(_ title: String, symbol: String, key: String) -> some View {
        Button { press(key) } label: {
            Label(title, systemImage: symbol).font(.subheadline.weight(.semibold))
                .padding(.horizontal, 22).padding(.vertical, 12)
                .jarvisSurface(in: .capsule)
                .overlay(Capsule().strokeBorder(JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel("Turn TV \(title.lowercased())")
    }

    private func press(_ key: String) {
        tick += 1
        Task {
            do {
                try await JarvisAPIClient.make(from: settings).pressTVRemote(key)
                error = nil
            } catch {
                self.error = "The TV didn't get that: \(error.localizedDescription)"
            }
        }
    }
}
