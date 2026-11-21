import SwiftUI

typealias RGB = (Double, Double, Double)

/// The app's colour themes, chosen in Settings. Each one supplies every
/// colour the app uses: the accent (`JarvisTheme.cyan`: headings, borders),
/// the reactor (`reactor`: the mark and the control tint), the secondary (`blue`: Nick's bubbles), the three status colours
/// (warning, critical, nominal), and the four surfaces, dark and light.
enum JarvisPalette: String, CaseIterable, Identifiable {
    case arc, hotRod, malibu, crimson, mark85, spiderMan, mcqueen, experimenting, barnDoor

    /// What Settings offers; barnDoor is Barn Door Protocol's red alert,
    /// switched on by JARVIS (brain/protocols.py), never picked.
    static let pickable: [JarvisPalette] = allCases.filter { $0 != .barnDoor }

    static let storageKey = "jarvis.palette"
    /// The pick Barn Door replaced (ThemeSync), so the core can tell that
    /// Experimenting is underneath the red alert.
    static let beforeAlertKey = "jarvis.palette.beforeAlert"
    var id: String { rawValue }

    var name: String {
        switch self {
        case .arc: return "Arc"
        case .hotRod: return "Hot Rod"
        case .malibu: return "Malibu"
        case .crimson: return "Crimson"
        case .mark85: return "Mark 85"
        case .spiderMan: return "Spider-Man"
        case .mcqueen: return "Lightning McQueen"
        case .experimenting: return "Experimenting"
        case .barnDoor: return "Barn Door Protocol"
        }
    }

    var blurb: String {
        switch self {
        case .arc: return "The original cyan HUD."
        case .hotRod: return "The Mark III's gold and red, with arc-reactor cyan on the reactor and controls."
        case .malibu: return "Sunset over the cliff house: orange, ocean blue, a cyan core."
        case .crimson: return "Red, but calm: crimson on neutral charcoal."
        case .mark85: return "Endgame's suit: gold and crimson on deep navy, no red glare."
        case .spiderMan: return "Suit red and web blue, after dark in Queens."
        case .mcqueen: return "Ka-chow. Lightning yellow and candy red on race-day asphalt."
        case .experimenting: return "The visual lab: Spider-Man colours plus whatever's being tried out (switches below)."
        case .barnDoor: return "Red alert. Say \"stand down\" to JARVIS to undo."
        }
    }

    static var current: JarvisPalette {
        JarvisPalette(rawValue: UserDefaults.standard.string(forKey: storageKey) ?? "") ?? .arc
    }

    /// Every colour role, (dark, light). Mirrored for the TV in
    /// tv/launcher/src/com/tvhome/Theme.java (dark variants only).
    private struct Roles {
        let accent, reactor, secondary, warning, critical, success: (RGB, RGB)
        let background, deep, panel, raised: (RGB, RGB)
    }

    private static let arcAccent: (RGB, RGB) = ((0.25, 0.85, 0.95), (0.0, 0.43, 0.54))
    private static let gold: (RGB, RGB) = ((1.0, 0.77, 0.32), (0.55, 0.37, 0.0))
    private static let cyanCore: (RGB, RGB) = ((0.35, 0.88, 1.0), (0.0, 0.45, 0.62))
    private static let redSecondary: (RGB, RGB) = ((0.86, 0.14, 0.16), (0.70, 0.07, 0.10))
    private static let redSurfaces: [(RGB, RGB)] = [
        ((0.075, 0.035, 0.035), (0.97, 0.955, 0.95)), ((0.025, 0.012, 0.014), (0.93, 0.91, 0.90)),
        ((0.12, 0.065, 0.06), (1, 1, 1)), ((0.17, 0.09, 0.08), (1, 0.985, 0.98))]

    private var roles: Roles {
        switch self {
        case .arc:
            return Roles(accent: Self.arcAccent, reactor: Self.arcAccent,
                         secondary: ((0.25, 0.50, 1.0), (0.08, 0.33, 0.85)),
                         warning: ((1, 0.73, 0.30), (0.69, 0.40, 0.0)),
                         critical: ((1.0, 0.36, 0.38), (0.78, 0.10, 0.14)),
                         success: ((0.30, 0.88, 0.62), (0.0, 0.48, 0.28)),
                         background: ((0.025, 0.045, 0.075), (0.95, 0.965, 0.985)),
                         deep: ((0.008, 0.018, 0.035), (0.90, 0.93, 0.96)),
                         panel: ((0.055, 0.085, 0.125), (1, 1, 1)),
                         raised: ((0.075, 0.12, 0.17), (0.975, 0.985, 1)))
        case .hotRod:
            return Roles(accent: Self.gold, reactor: Self.cyanCore,
                         secondary: Self.redSecondary,
                         warning: ((1.0, 0.50, 0.18), (0.76, 0.30, 0.0)),
                         critical: ((1.0, 0.22, 0.27), (0.80, 0.03, 0.09)),
                         success: ((0.55, 0.90, 1.0), (0.0, 0.45, 0.62)),
                         background: Self.redSurfaces[0], deep: Self.redSurfaces[1],
                         panel: Self.redSurfaces[2], raised: Self.redSurfaces[3])
        case .malibu:
            return Roles(accent: ((1.0, 0.58, 0.28), (0.72, 0.33, 0.0)), reactor: Self.cyanCore,
                         secondary: ((0.16, 0.45, 0.85), (0.08, 0.33, 0.70)),
                         warning: ((1.0, 0.78, 0.30), (0.66, 0.42, 0.0)),
                         critical: ((1.0, 0.33, 0.36), (0.80, 0.08, 0.12)),
                         success: ((0.35, 0.88, 0.70), (0.0, 0.50, 0.38)),
                         background: ((0.04, 0.05, 0.10), (0.97, 0.955, 0.94)),
                         deep: ((0.015, 0.02, 0.05), (0.93, 0.91, 0.89)),
                         panel: ((0.08, 0.10, 0.17), (1, 1, 1)),
                         raised: ((0.11, 0.14, 0.23), (1, 0.985, 0.975)))
        case .crimson:
            return Roles(accent: ((0.95, 0.28, 0.33), (0.75, 0.07, 0.13)),
                         reactor: ((0.80, 0.90, 1.0), (0.20, 0.35, 0.55)),
                         secondary: ((0.70, 0.12, 0.18), (0.62, 0.06, 0.12)),
                         warning: ((1.0, 0.68, 0.25), (0.70, 0.38, 0.0)),
                         critical: ((1.0, 0.45, 0.25), (0.80, 0.18, 0.0)),
                         success: ((0.40, 0.85, 0.60), (0.0, 0.50, 0.28)),
                         background: ((0.06, 0.06, 0.07), (0.97, 0.96, 0.96)),
                         deep: ((0.02, 0.02, 0.025), (0.92, 0.91, 0.91)),
                         panel: ((0.10, 0.10, 0.11), (1, 1, 1)),
                         raised: ((0.14, 0.14, 0.155), (0.99, 0.98, 0.98)))
        case .mark85:
            return Roles(accent: ((1.0, 0.80, 0.40), (0.58, 0.40, 0.0)), reactor: Self.cyanCore,
                         secondary: ((0.78, 0.12, 0.16), (0.66, 0.06, 0.10)),
                         warning: ((1.0, 0.58, 0.22), (0.76, 0.32, 0.0)),
                         critical: ((1.0, 0.30, 0.34), (0.80, 0.05, 0.10)),
                         success: ((0.50, 0.88, 0.95), (0.0, 0.45, 0.60)),
                         background: ((0.035, 0.045, 0.085), (0.96, 0.96, 0.975)),
                         deep: ((0.012, 0.016, 0.035), (0.91, 0.915, 0.935)),
                         panel: ((0.07, 0.085, 0.14), (1, 1, 1)),
                         raised: ((0.10, 0.12, 0.19), (0.98, 0.98, 0.995)))
        case .spiderMan, .experimenting:
            return Roles(accent: ((0.93, 0.18, 0.22), (0.75, 0.05, 0.10)),
                         reactor: ((0.55, 0.75, 1.0), (0.10, 0.30, 0.70)),
                         secondary: ((0.15, 0.38, 0.90), (0.08, 0.28, 0.75)),
                         warning: ((1.0, 0.70, 0.25), (0.70, 0.38, 0.0)),
                         critical: ((1.0, 0.42, 0.30), (0.80, 0.15, 0.05)),
                         success: ((0.40, 0.85, 0.65), (0.0, 0.50, 0.32)),
                         background: ((0.03, 0.04, 0.09), (0.96, 0.965, 0.98)),
                         deep: ((0.01, 0.015, 0.04), (0.91, 0.92, 0.95)),
                         panel: ((0.06, 0.08, 0.16), (1, 1, 1)),
                         raised: ((0.09, 0.11, 0.22), (0.98, 0.985, 1.0)))
        case .mcqueen:
            // Race day: asphalt, not red, underneath; red is the paint, yellow the bolt.
            return Roles(accent: ((1.0, 0.82, 0.15), (0.62, 0.45, 0.0)),
                         reactor: ((0.96, 0.20, 0.17), (0.78, 0.08, 0.06)),
                         secondary: ((0.88, 0.12, 0.14), (0.72, 0.06, 0.10)),
                         warning: ((1.0, 0.60, 0.18), (0.76, 0.34, 0.0)),
                         critical: ((1.0, 0.36, 0.38), (0.80, 0.05, 0.10)),
                         success: ((0.45, 0.88, 0.55), (0.0, 0.50, 0.25)),
                         background: ((0.075, 0.075, 0.08), (0.97, 0.965, 0.955)),
                         deep: ((0.03, 0.03, 0.035), (0.92, 0.915, 0.905)),
                         panel: ((0.12, 0.12, 0.13), (1, 1, 1)),
                         raised: ((0.17, 0.17, 0.18), (0.99, 0.985, 0.975)))
        case .barnDoor:
            let red: (RGB, RGB) = ((1.0, 0.18, 0.16), (0.80, 0.04, 0.04))
            return Roles(accent: red, reactor: red, secondary: ((0.70, 0.05, 0.06), (0.62, 0.02, 0.04)),
                         warning: ((1.0, 0.45, 0.30), (0.78, 0.20, 0.08)),
                         critical: ((1.0, 0.82, 0.80), (0.50, 0.0, 0.0)),
                         success: ((1.0, 0.55, 0.50), (0.70, 0.12, 0.10)),
                         background: ((0.10, 0.0, 0.0), (0.98, 0.93, 0.93)),
                         deep: ((0.04, 0.0, 0.0), (0.94, 0.86, 0.86)),
                         panel: ((0.16, 0.02, 0.02), (1, 0.97, 0.97)),
                         raised: ((0.22, 0.03, 0.03), (1, 0.95, 0.95)))
        }
    }

    var accent: (RGB, RGB) { roles.accent }
    /// The reactor mark and interactive tint (tab bar, toggles, buttons).
    var reactor: (RGB, RGB) { roles.reactor }
    var secondary: (RGB, RGB) { roles.secondary }
    /// Due soon, stalled, needs a look.
    var warning: (RGB, RGB) { roles.warning }
    /// Overdue, down, failed.
    var critical: (RGB, RGB) { roles.critical }
    /// Nominal.
    var success: (RGB, RGB) { roles.success }

    var surfaces: (background: (RGB, RGB), deep: (RGB, RGB), panel: (RGB, RGB), raised: (RGB, RGB)) {
        let r = roles
        return (r.background, r.deep, r.panel, r.raised)
    }
}

/// Shared cockpit palette. Every colour comes from the chosen
/// `JarvisPalette` (never a system red/orange/green) and is adaptive (dark =
/// the cockpit look, light = AA-contrast daylight variants), so the app
/// follows both the theme and the system appearance. Colours resolve from
/// `JarvisPalette.current` each render; `RootView` re-identifies the
/// hierarchy when it changes.
enum JarvisTheme {
    private static func themed(_ role: @escaping (JarvisPalette) -> (RGB, RGB)) -> Color {
        Color(UIColor { traits in
            let pair = role(JarvisPalette.current)
            let c = traits.userInterfaceStyle == .dark ? pair.0 : pair.1
            return UIColor(red: c.0, green: c.1, blue: c.2, alpha: 1)
        })
    }

    static let background = themed { $0.surfaces.background }
    static let backgroundDeep = themed { $0.surfaces.deep }
    static let panel = themed { $0.surfaces.panel }
    static let panelRaised = themed { $0.surfaces.raised }
    /// The accent. Named for the original palette; it follows the theme.
    static let cyan = themed { $0.accent }
    /// The reactor mark and control tint; the accent except on Hot Rod.
    static let reactor = themed { $0.reactor }
    static let blue = themed { $0.secondary }
    /// The warning colour (amber on Arc, molten orange on Mark III).
    static let amber = themed { $0.warning }
    static let critical = themed { $0.critical }
    static let success = themed { $0.success }
    static let border = cyan.opacity(0.18)

    static let panelGradient = LinearGradient(
        colors: [panelRaised.opacity(0.94), panel.opacity(0.88)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

/// A quiet gradient field shared by every screen. It keeps the app related to
/// the desktop cockpit without competing with the text.
struct JarvisBackdrop: View {
    /// Lifts the blacks a little (0 = as designed; the door iPad's Room uses
    /// 0.1, Nick 2026-11-20: "bring the black down by like 10%").
    var lift: Double = 0
    // Observed so flipping the theme or the experiment switch redraws at once.
    @AppStorage(JarvisPalette.storageKey) private var palette = JarvisPalette.arc.rawValue
    @AppStorage(Experiment.plasma.storageKey) private var plasmaOn = Experiment.plasma.defaultOn

    var body: some View {
        Group {
            if palette == JarvisPalette.experimenting.rawValue && plasmaOn {
                PlasmaBackdrop(lift: lift)
            } else {
                classic
            }
        }
        .overlay {
            if lift > 0 {
                Color.white.opacity(lift * 0.5).blendMode(.screen).ignoresSafeArea().allowsHitTesting(false)
            }
        }
    }

    private var classic: some View {
        ZStack {
            LinearGradient(
                colors: [JarvisTheme.background, JarvisTheme.backgroundDeep],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            RadialGradient(
                colors: [JarvisTheme.cyan.opacity(0.10), .clear],
                center: .topTrailing,
                startRadius: 10,
                endRadius: 330
            )
            RadialGradient(
                colors: [JarvisTheme.blue.opacity(0.07), .clear],
                center: .bottomLeading,
                startRadius: 10,
                endRadius: 420
            )
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

/// The JARVIS reactor mark. When `animated`, the rings counter-rotate and a
/// tick dial drifts; the glow holds steady (he doesn't breathe), a touch
/// brighter while `active` (JARVIS is thinking). Drawn in the palette's
/// reactor colour.
///
/// Every angle is computed from the clock inside one `TimelineView`, rather
/// than by `repeatForever` animations: those restarted (and stacked, so the
/// rings sped up or jumped) whenever the view was rebuilt mid-turn. Off
/// screen the timeline pauses; Reduce Motion, Low Power Mode, or
/// `animated: false` draw the still pose.
struct JarvisCoreMark: View {
    var size: CGFloat = 112
    var animated = true
    var active = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.cameraWatching) private var cameraWatching
    @AppStorage(JarvisPalette.storageKey) private var palette = JarvisPalette.arc.rawValue
    @AppStorage(Experiment.neural.storageKey) private var neuralOn = Experiment.neural.defaultOn
    @AppStorage(Experiment.orb3D.storageKey) private var orb3DOn = Experiment.orb3D.defaultOn
    @AppStorage(JarvisPalette.beforeAlertKey) private var beforeAlert = ""
    @State private var visible = false

    private var moving: Bool {
        animated && !reduceMotion && !ProcessInfo.processInfo.isLowPowerModeEnabled
    }

    private var barnDoor: Bool { palette == JarvisPalette.barnDoor.rawValue }

    /// Experimenting is the pick, or it's what Barn Door's red replaced.
    private var experimenting: Bool {
        palette == JarvisPalette.experimenting.rawValue
            || (barnDoor && beforeAlert == JarvisPalette.experimenting.rawValue)
    }

    var body: some View {
        Group {
            if experimenting && neuralOn && size >= 60 {
                // One view for all three moods, so the camera's eye and Barn
                // Door's spikes grow out of the brain instead of cutting to it.
                // (Below 60 pt the brain is a smudge; the chat's thinking dot keeps the classic mark.)
                NeuralCore(size: size, moving: moving && visible, active: active,
                           mood: cameraWatching ? .watching : (barnDoor ? .alert : .thinking))
            } else if cameraWatching {
                // iPad lens on: the reactor itself turns into the green orb.
                CameraWatchOrb(size: size, moving: moving && visible)
            } else if barnDoor {
                // Barn Door: the same hostile reactor as the TV's red-alert screen.
                RedAlertMark(size: size, moving: moving && visible)
            } else if experimenting && orb3DOn {
                OrbitOrb(size: size, moving: moving && visible, active: active)
            } else {
                TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !(moving && visible))) { context in
                    mark(seconds: moving ? context.date.timeIntervalSinceReferenceDate : 0)
                }
            }
        }
        .frame(width: size, height: size)
        .onAppear { visible = true }
        .onDisappear { visible = false }
        .accessibilityHidden(true)
    }

    /// One full turn every `period` seconds, from the clock.
    private func turn(_ seconds: Double, every period: Double) -> Double {
        (seconds / period).truncatingRemainder(dividingBy: 1) * 360
    }

    private func mark(seconds: Double) -> some View {
        let dial = Double.pi * size * 0.92
        // The Room orb can fill a large iPad panel. Keep the phone's fine
        // strokes instead of making its rings heavier as the canvas grows.
        let strokeSize = min(size, 190)
        return ZStack {
            Circle().fill(RadialGradient(colors: [JarvisTheme.reactor.opacity(active ? 0.30 : 0.22), .clear],
                                         center: .center, startRadius: 4, endRadius: size / 2))
            Circle().fill(RadialGradient(colors: [JarvisTheme.success.opacity(0.16), .clear],
                                         center: .center, startRadius: 0, endRadius: size * 0.22))
            Circle().stroke(JarvisTheme.reactor.opacity(0.15), lineWidth: 1).padding(2)
            if size >= 60 {
                // HUD dial: sixty ticks drifting slowly the other way.
                Circle()
                    .stroke(JarvisTheme.reactor.opacity(0.28),
                            style: StrokeStyle(lineWidth: strokeSize * 0.025, dash: [1, dial / 60 - 1]))
                    .padding(size * 0.04)
                    .rotationEffect(.degrees(-turn(seconds, every: 40)))
            }
            Circle().trim(from: 0.05, to: 0.82)
                .stroke(JarvisTheme.reactor.opacity(0.6), style: StrokeStyle(lineWidth: max(1.5, strokeSize / 56), lineCap: .round))
                .padding(size * 0.13)
                .rotationEffect(.degrees(-60 + turn(seconds, every: 16)))
            Circle().trim(from: 0.12, to: 0.92)
                .stroke(JarvisTheme.reactor.opacity(0.3), lineWidth: 1)
                .padding(size * 0.21)
                .rotationEffect(.degrees(90 - turn(seconds, every: 10)))
            Image(systemName: "waveform")
                .font(.system(size: size * 0.25, weight: size > 190 ? .ultraLight : .light))
                .foregroundStyle(JarvisTheme.reactor)
        }
        .compositingGroup()
    }
}

struct CockpitHeader: View {
    let eyebrow: String
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(eyebrow)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(JarvisTheme.cyan)
            Text(title).font(.largeTitle.weight(.semibold))
            Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 12)
    }
}

struct CockpitPanel<Content: View, Trailing: View>: View {
    let title: String
    let symbol: String
    @ViewBuilder let trailing: () -> Trailing
    @ViewBuilder let content: () -> Content

    init(
        _ title: String,
        symbol: String,
        @ViewBuilder trailing: @escaping () -> Trailing,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.title = title
        self.symbol = symbol
        self.trailing = trailing
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 9) {
                Image(systemName: symbol)
                    .foregroundStyle(JarvisTheme.cyan)
                    .frame(width: 22)
                    .accessibilityHidden(true)
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                trailing()
            }
            content()
        }
        .padding(16)
        .jarvisSurface(in: .rect(cornerRadius: 20))
        .overlay {
            RoundedRectangle(cornerRadius: 20)
                .strokeBorder(
                    LinearGradient(
                        colors: [JarvisTheme.cyan.opacity(0.30), JarvisTheme.border.opacity(0.35)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
        }
    }
}

extension CockpitPanel where Trailing == EmptyView {
    init(
        _ title: String,
        symbol: String,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.init(title, symbol: symbol, trailing: { EmptyView() }, content: content)
    }
}

struct StatusPill: View {
    let text: String
    let color: Color

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 6, height: 6).accessibilityHidden(true)
            Text(text)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(color)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(color.opacity(0.12), in: .capsule)
    }
}

/// Restrained press feedback that also respects Reduce Motion.
struct CockpitButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.78 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: configuration.isPressed)
    }
}
