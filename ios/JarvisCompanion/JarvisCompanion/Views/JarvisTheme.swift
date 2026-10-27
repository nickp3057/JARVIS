import SwiftUI

typealias RGB = (Double, Double, Double)

/// The app's colour themes, chosen in Settings. Each one supplies the accent
/// (`JarvisTheme.cyan`: tint, logo, borders), the secondary (`blue`: Nick's
/// bubbles), and the four surfaces, in dark and light variants.
enum JarvisPalette: String, CaseIterable, Identifiable {
    case arc, markIII

    static let storageKey = "jarvis.palette"
    var id: String { rawValue }

    var name: String {
        switch self {
        case .arc: return "Arc"
        case .markIII: return "Mark III"
        }
    }

    var blurb: String {
        switch self {
        case .arc: return "The original cyan HUD."
        case .markIII: return "Hot-rod red and gold on warm charcoal."
        }
    }

    static var current: JarvisPalette {
        JarvisPalette(rawValue: UserDefaults.standard.string(forKey: storageKey) ?? "") ?? .arc
    }

    /// (dark, light) per role.
    var accent: (RGB, RGB) {
        switch self {
        case .arc: return ((0.25, 0.85, 0.95), (0.0, 0.43, 0.54))
        case .markIII: return ((0.97, 0.36, 0.36), (0.72, 0.10, 0.14))
        }
    }

    var secondary: (RGB, RGB) {
        switch self {
        case .arc: return ((0.25, 0.50, 1.0), (0.08, 0.33, 0.85))
        case .markIII: return ((0.90, 0.68, 0.28), (0.55, 0.38, 0.02))
        }
    }

    var surfaces: (background: (RGB, RGB), deep: (RGB, RGB), panel: (RGB, RGB), raised: (RGB, RGB)) {
        switch self {
        case .arc:
            return (((0.025, 0.045, 0.075), (0.95, 0.965, 0.985)), ((0.008, 0.018, 0.035), (0.90, 0.93, 0.96)),
                    ((0.055, 0.085, 0.125), (1, 1, 1)), ((0.075, 0.12, 0.17), (0.975, 0.985, 1)))
        case .markIII:
            return (((0.06, 0.045, 0.045), (0.97, 0.955, 0.95)), ((0.025, 0.018, 0.018), (0.93, 0.91, 0.90)),
                    ((0.10, 0.075, 0.072), (1, 1, 1)), ((0.14, 0.10, 0.095), (1, 0.985, 0.98)))
        }
    }
}

/// Shared cockpit palette. Every colour is adaptive (dark = the original
/// cockpit look, light = a tuned daylight variant with AA-contrast accents),
/// so the app follows the system appearance. Keep text scalable and reserve
/// amber for activity. Palette colours resolve from `JarvisPalette.current`
/// each render; `RootView` re-identifies the hierarchy when it changes.
enum JarvisTheme {
    private static func adaptive(
        dark: (Double, Double, Double),
        light: (Double, Double, Double)
    ) -> Color {
        Color(UIColor { traits in
            let c = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: c.0, green: c.1, blue: c.2, alpha: 1)
        })
    }

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
    static let blue = themed { $0.secondary }
    static let amber = adaptive(dark: (1, 0.73, 0.30), light: (0.69, 0.40, 0.0))
    static let border = cyan.opacity(0.18)
    static let success = adaptive(dark: (0.30, 0.88, 0.62), light: (0.0, 0.48, 0.28))

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
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

/// The JARVIS reactor mark. When `animated`, the rings counter-rotate and
/// the glow breathes; `active` (JARVIS is thinking) spins faster and pulses.
///
/// Built for a steady 60 fps: a `TimelineView(.animation)` follows the
/// display, each frame changes only rotation and opacity, the rings render as
/// one Metal-backed layer (`drawingGroup`), and there is no per-frame blur
/// (the glow is a radial gradient, not a shadow). It stops entirely when off
/// screen, with Reduce Motion, or in Low Power Mode, and then draws the
/// original still pose.
struct JarvisCoreMark: View {
    var size: CGFloat = 112
    var animated = true
    var active = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var onScreen = false

    private var still: Bool {
        !animated || !onScreen || reduceMotion || ProcessInfo.processInfo.isLowPowerModeEnabled
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: nil, paused: still)) { context in
            mark(at: still ? 0 : context.date.timeIntervalSinceReferenceDate)
        }
        .frame(width: size, height: size)
        .onAppear { onScreen = true }
        .onDisappear { onScreen = false }
        .accessibilityHidden(true)
    }

    private func mark(at t: Double) -> some View {
        let speed = active ? 3.0 : 1.0
        let outer = (t * speed * 360 / 16).truncatingRemainder(dividingBy: 360)
        let inner = (t * speed * 360 / 10).truncatingRemainder(dividingBy: 360)
        let breathe = t == 0 ? 0.5 : 0.5 + 0.5 * sin(t * 2 * .pi / (active ? 1.2 : 4.5))
        return ZStack {
            ZStack {
                Circle().fill(RadialGradient(colors: [JarvisTheme.cyan.opacity(0.14 + 0.16 * breathe), .clear],
                                             center: .center, startRadius: 4, endRadius: size / 2))
                Circle().stroke(JarvisTheme.cyan.opacity(0.15), lineWidth: 1).padding(2)
                Circle().trim(from: 0.05, to: 0.82)
                    .stroke(JarvisTheme.cyan.opacity(0.6), style: StrokeStyle(lineWidth: max(1.5, size / 56), lineCap: .round))
                    .rotationEffect(.degrees(-60 + outer)).padding(size * 0.13)
                Circle().trim(from: 0.12, to: 0.92)
                    .stroke(JarvisTheme.cyan.opacity(0.3), lineWidth: 1)
                    .rotationEffect(.degrees(90 - inner)).padding(size * 0.21)
            }
            .drawingGroup()
            Image(systemName: "waveform")
                .font(.system(size: size * 0.25, weight: .light))
                .foregroundStyle(JarvisTheme.cyan)
                .opacity(active ? 0.7 + 0.3 * breathe : 1)
        }
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
