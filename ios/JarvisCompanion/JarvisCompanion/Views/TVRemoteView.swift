import SwiftUI

/// The TV's HDMI ports, named the way the TV names them (Settings > Inputs).
/// Core maps `hdmi_N` to the input itself (`TV_HDMI_INPUTS`). Shared by the
/// remote and movie mode.
enum TVInputs {
    static let ports: [(port: Int, name: String, short: String, symbol: String)] = [
        (1, "PlayStation 5", "PS5", "playstation.logo"),
        (2, "Nintendo Switch", "Switch", "gamecontroller"),
        (3, "Wii U", "Wii U", "gamecontroller.fill"),
    ]

    /// A menu of the ports; `press` gets the remote key (`hdmi_N`).
    @ViewBuilder
    static func menuItems(press: @escaping (String) -> Void) -> some View {
        ForEach(ports, id: \.port) { input in
            Button("\(input.name) · HDMI \(input.port)", systemImage: input.symbol) { press("hdmi_\(input.port)") }
        }
    }
}

/// A remote for the TV in Nick's room: each press goes to JARVIS Core
/// (`POST /api/tv/remote`), which sends it through Home Assistant's Android
/// TV Remote connection, so it works whatever app is on screen. Inputs switch
/// to the PS5, Switch or Wii U (HDMI 1-3); app tiles open Plex, YouTube,
/// Spotify or the TV's Settings.
///
/// Phone: one scrolling column. iPad: a page-sized sheet laid out as a
/// console, the d-pad and navigation on the left, inputs, volume and apps on
/// the right, every input its own tile instead of a menu.
struct TVRemoteView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var error: String?
    @State private var tick = 0

    private var isPad: Bool { sizeClass == .regular }

    var body: some View {
        NavigationStack {
            ScrollView {
                Group {
                    if isPad { padLayout } else { phoneLayout }
                }
                .padding(isPad ? 32 : 24)
                .frame(maxWidth: .infinity)
            }
            .scrollBounceBehavior(.basedOnSize)
            .background { JarvisBackdrop() }
            .navigationTitle("TV remote").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .sensoryFeedback(.impact(weight: .light), trigger: tick)
        }
        .modifier(PageSizedOnPad())
    }

    // MARK: - Layouts

    private var phoneLayout: some View {
        VStack(spacing: 22) {
            HStack(spacing: 16) {
                pill("On", symbol: "power", key: "power_on")
                pill("Off", symbol: "poweroff", key: "power_off")
            }
            dpad(size: 230)
            navRow(size: 64)
            volumeRow(size: 64)
            inputMenu
            apps(columns: 2)
            errorLabel
        }
    }

    private var padLayout: some View {
        VStack(spacing: 28) {
            HStack(alignment: .top, spacing: 40) {
                VStack(spacing: 28) {
                    HStack(spacing: 16) {
                        pill("On", symbol: "power", key: "power_on")
                        pill("Off", symbol: "poweroff", key: "power_off")
                    }
                    dpad(size: 300)
                    navRow(size: 76)
                }
                .frame(maxWidth: .infinity)
                VStack(alignment: .leading, spacing: 24) {
                    section("Inputs") { inputTiles }
                    section("Volume") { volumeRow(size: 76) }
                    section("Apps") { apps(columns: 2) }
                }
                .frame(maxWidth: .infinity)
            }
            errorLabel
        }
        .frame(maxWidth: 900)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased())
                .font(.caption.weight(.semibold)).tracking(1.2)
                .foregroundStyle(.secondary)
            content()
        }
    }

    @ViewBuilder private var errorLabel: some View {
        if let error {
            Label(error, systemImage: "exclamationmark.triangle")
                .font(.footnote).foregroundStyle(JarvisTheme.amber)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Pieces

    private func navRow(size: CGFloat) -> some View {
        HStack(spacing: 22) {
            round("arrow.uturn.backward", label: "Back", key: "back", size: size)
            round("house", label: "Home", key: "home", size: size)
            round("playpause", label: "Play or pause", key: "play_pause", size: size)
        }
    }

    private func volumeRow(size: CGFloat) -> some View {
        HStack(spacing: 22) {
            round("speaker.minus", label: "Volume down", key: "volume_down", size: size, repeats: true)
            round("speaker.slash", label: "Mute", key: "mute", size: size)
            round("speaker.plus", label: "Volume up", key: "volume_up", size: size, repeats: true)
        }
    }

    /// Phone: one Input button that opens a menu of the ports.
    private var inputMenu: some View {
        Menu {
            TVInputs.menuItems(press: press)
        } label: {
            Label("Input", systemImage: "rectangle.on.rectangle").font(.subheadline.weight(.semibold))
                .frame(maxWidth: 360, minHeight: 46)
                .jarvisSurface(in: .rect(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel("Switch the TV's input")
    }

    /// iPad: one tile per port, no menu to open.
    private var inputTiles: some View {
        HStack(spacing: 12) {
            ForEach(TVInputs.ports, id: \.port) { input in
                Button { press("hdmi_\(input.port)") } label: {
                    VStack(spacing: 6) {
                        Image(systemName: input.symbol).font(.title2)
                        Text(input.short).font(.subheadline.weight(.semibold))
                        Text("HDMI \(input.port)").font(.caption2).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, minHeight: 96)
                    .jarvisSurface(in: .rect(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(JarvisTheme.border))
                }
                .buttonStyle(CockpitButtonStyle())
                .accessibilityLabel("Switch the TV to the \(input.name), HDMI \(input.port)")
            }
        }
    }

    /// Quick launch: opens the app on the TV (waking it if needed).
    private func apps(columns: Int) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: columns), spacing: 12) {
            app("Plex", symbol: "play.rectangle.fill", key: "app_plex")
            app("YouTube", symbol: "play.tv.fill", key: "app_youtube")
            app("Spotify", symbol: "music.note", key: "app_spotify")
            app("Settings", symbol: "gearshape.fill", key: "app_settings")
        }
        .frame(maxWidth: isPad ? .infinity : 360)
    }

    private func app(_ title: String, symbol: String, key: String) -> some View {
        Button { press(key) } label: {
            Label(title, systemImage: symbol).font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: isPad ? 56 : 46)
                .jarvisSurface(in: .rect(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel("Open \(title) on the TV")
    }

    private func dpad(size: CGFloat) -> some View {
        let ok = size * 0.365
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
                    .frame(width: ok, height: ok)
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

    private func round(_ symbol: String, label: String, key: String, size: CGFloat, repeats: Bool = false) -> some View {
        Button { press(key) } label: {
            Image(systemName: symbol).font(.title3)
                .frame(width: size, height: size)
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

/// On iPad the default form sheet is too short for the remote (the d-pad
/// pushed into the title); iOS 18 can give it a page-sized sheet instead.
private struct PageSizedOnPad: ViewModifier {
    func body(content: Content) -> some View {
        if #available(iOS 18.0, *) {
            content.presentationSizing(.page)
        } else {
            content
        }
    }
}
