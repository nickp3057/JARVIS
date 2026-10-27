import SwiftUI

typealias RGB = (Double, Double, Double)

/// The app's colour themes, chosen in Settings. Each one supplies every
/// colour the app uses: the accent (`JarvisTheme.cyan`: headings, borders),
/// the reactor (`reactor`: the mark and the control tint), the secondary (`blue`: Nick's bubbles), the three status colours
/// (warning, critical, nominal), and the four surfaces, dark and light.
enum JarvisPalette: String, CaseIterable, Identifiable {
    case arc, markIII, hotRod

    static let storageKey = "jarvis.palette"
    var id: String { rawValue }

    var name: String {
        switch self {
        case .arc: return "Arc"
        case .markIII: return "Mark III"
        case .hotRod: return "Hot Rod"
        }
    }

    var blurb: String {
        switch self {
        case .arc: return "The original cyan HUD."
        case .markIII: return "Gold HUD, hot-rod red, arc-reactor glow."
        case .hotRod: return "Mark III's gold and red, with arc-reactor cyan on the reactor and controls."
        }
    }

    static var current: JarvisPalette {
        JarvisPalette(rawValue: UserDefaults.standard.string(forKey: storageKey) ?? "") ?? .arc
    }

    /// (dark, light) per role.
    var accent: (RGB, RGB) {
        switch self {
        case .arc: return ((0.25, 0.85, 0.95), (0.0, 0.43, 0.54))
        case .markIII, .hotRod: return ((1.0, 0.77, 0.32), (0.55, 0.37, 0.0))
        }
    }

    /// The reactor mark and interactive tint (tab bar, toggles, buttons).
    /// The accent everywhere except Hot Rod, where it's reactor cyan.
    var reactor: (RGB, RGB) {
        switch self {
        case .arc, .markIII: return accent
        case .hotRod: return ((0.35, 0.88, 1.0), (0.0, 0.45, 0.62))
        }
    }

    var secondary: (RGB, RGB) {
        switch self {
        case .arc: return ((0.25, 0.50, 1.0), (0.08, 0.33, 0.85))
        case .markIII, .hotRod: return ((0.86, 0.14, 0.16), (0.70, 0.07, 0.10))
        }
    }

    /// Due soon, stalled, needs a look.
    var warning: (RGB, RGB) {
        switch self {
        case .arc: return ((1, 0.73, 0.30), (0.69, 0.40, 0.0))
        case .markIII, .hotRod: return ((1.0, 0.50, 0.18), (0.76, 0.30, 0.0))
        }
    }

    /// Overdue, down, failed.
    var critical: (RGB, RGB) {
        switch self {
        case .arc: return ((1.0, 0.36, 0.38), (0.78, 0.10, 0.14))
        case .markIII, .hotRod: return ((1.0, 0.22, 0.27), (0.80, 0.03, 0.09))
        }
    }

    /// Nominal. Mark III glows reactor blue-white against the gold.
    var success: (RGB, RGB) {
        switch self {
        case .arc: return ((0.30, 0.88, 0.62), (0.0, 0.48, 0.28))
        case .markIII, .hotRod: return ((0.55, 0.90, 1.0), (0.0, 0.45, 0.62))
        }
    }

    var surfaces: (background: (RGB, RGB), deep: (RGB, RGB), panel: (RGB, RGB), raised: (RGB, RGB)) {
        switch self {
        case .arc:
            return (((0.025, 0.045, 0.075), (0.95, 0.965, 0.985)), ((0.008, 0.018, 0.035), (0.90, 0.93, 0.96)),
                    ((0.055, 0.085, 0.125), (1, 1, 1)), ((0.075, 0.12, 0.17), (0.975, 0.985, 1)))
        case .markIII, .hotRod:
            return (((0.075, 0.035, 0.035), (0.97, 0.955, 0.95)), ((0.025, 0.012, 0.014), (0.93, 0.91, 0.90)),
                    ((0.12, 0.065, 0.06), (1, 1, 1)), ((0.17, 0.09, 0.08), (1, 0.985, 0.98)))
        }
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
    var body: some View {
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
/// Each moving part gets one `repeatForever` animation, scoped with
/// `.animation(_:body:)` so it never leaks into layout, instead of rebuilding
/// the whole mark every frame. Leaving the screen stops it at rest and it
/// starts again from there, so tab switches and scrolling never make it
/// jump. Reduce Motion, Low Power Mode, or `animated: false` draw the still
/// pose.
struct JarvisCoreMark: View {
    var size: CGFloat = 112
    var animated = true
    var active = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var running = false

    private var moving: Bool {
        running && animated && !reduceMotion && !ProcessInfo.processInfo.isLowPowerModeEnabled
    }

    private func spin(_ seconds: Double) -> Animation? {
        moving ? .linear(duration: seconds).repeatForever(autoreverses: false) : nil
    }

    var body: some View {
        let dial = Double.pi * size * 0.92
        ZStack {
            Circle().fill(RadialGradient(colors: [JarvisTheme.reactor.opacity(active ? 0.30 : 0.22), .clear],
                                         center: .center, startRadius: 4, endRadius: size / 2))
            Circle().fill(RadialGradient(colors: [JarvisTheme.success.opacity(0.16), .clear],
                                         center: .center, startRadius: 0, endRadius: size * 0.22))
            Circle().stroke(JarvisTheme.reactor.opacity(0.15), lineWidth: 1).padding(2)
            if size >= 60 {
                // HUD dial: sixty ticks drifting slowly the other way.
                Circle()
                    .stroke(JarvisTheme.reactor.opacity(0.28),
                            style: StrokeStyle(lineWidth: size * 0.025, dash: [1, dial / 60 - 1]))
                    .padding(size * 0.04)
                    .animation(spin(40)) { $0.rotationEffect(.degrees(moving ? -360 : 0)) }
            }
            Circle().trim(from: 0.05, to: 0.82)
                .stroke(JarvisTheme.reactor.opacity(0.6), style: StrokeStyle(lineWidth: max(1.5, size / 56), lineCap: .round))
                .padding(size * 0.13)
                .animation(spin(16)) { $0.rotationEffect(.degrees(-60 + (moving ? 360 : 0))) }
            Circle().trim(from: 0.12, to: 0.92)
                .stroke(JarvisTheme.reactor.opacity(0.3), lineWidth: 1)
                .padding(size * 0.21)
                .animation(spin(10)) { $0.rotationEffect(.degrees(90 - (moving ? 360 : 0))) }
            Image(systemName: "waveform")
                .font(.system(size: size * 0.25, weight: .light))
                .foregroundStyle(JarvisTheme.reactor)
        }
        .frame(width: size, height: size)
        .compositingGroup()
        .onAppear { running = true }
        .onDisappear { running = false }
        .accessibilityHidden(true)
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
        .background(JarvisTheme.panelGradient, in: .rect(cornerRadius: 20))
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
