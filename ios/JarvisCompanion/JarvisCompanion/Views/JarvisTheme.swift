import SwiftUI

/// Shared cockpit palette. Every colour is adaptive (dark = the original
/// cockpit look, light = a tuned daylight variant with AA-contrast accents),
/// so the app follows the system appearance. Keep text scalable and reserve
/// amber for activity.
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

    static let background = adaptive(dark: (0.025, 0.045, 0.075), light: (0.95, 0.965, 0.985))
    static let backgroundDeep = adaptive(dark: (0.008, 0.018, 0.035), light: (0.90, 0.93, 0.96))
    static let panel = adaptive(dark: (0.055, 0.085, 0.125), light: (1, 1, 1))
    static let panelRaised = adaptive(dark: (0.075, 0.12, 0.17), light: (0.975, 0.985, 1))
    static let cyan = adaptive(dark: (0.25, 0.85, 0.95), light: (0.0, 0.43, 0.54))
    static let blue = adaptive(dark: (0.25, 0.50, 1.0), light: (0.08, 0.33, 0.85))
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

struct JarvisCoreMark: View {
    var size: CGFloat = 112

    var body: some View {
        ZStack {
            Circle().fill(RadialGradient(colors: [JarvisTheme.cyan.opacity(0.22), .clear],
                                         center: .center, startRadius: 5, endRadius: size / 2))
            Circle().stroke(JarvisTheme.cyan.opacity(0.15), lineWidth: 1).padding(2)
            Circle().trim(from: 0.05, to: 0.82)
                .stroke(JarvisTheme.cyan.opacity(0.6), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .rotationEffect(.degrees(-60)).padding(size * 0.13)
            Circle().trim(from: 0.12, to: 0.92)
                .stroke(JarvisTheme.cyan.opacity(0.3), lineWidth: 1)
                .rotationEffect(.degrees(90)).padding(size * 0.21)
            Image(systemName: "waveform")
                .font(.system(size: size * 0.25, weight: .light))
                .foregroundStyle(JarvisTheme.cyan)
        }
        .frame(width: size, height: size)
        .shadow(color: JarvisTheme.cyan.opacity(0.24), radius: size * 0.15)
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
