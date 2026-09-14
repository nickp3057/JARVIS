import SwiftUI

/// Shared cockpit palette. Keep text scalable and reserve amber for activity.
enum JarvisTheme {
    static let background = Color(red: 0.025, green: 0.045, blue: 0.075)
    static let backgroundDeep = Color(red: 0.008, green: 0.018, blue: 0.035)
    static let panel = Color(red: 0.055, green: 0.085, blue: 0.125)
    static let panelRaised = Color(red: 0.075, green: 0.12, blue: 0.17)
    static let cyan = Color(red: 0.25, green: 0.85, blue: 0.95)
    static let blue = Color(red: 0.25, green: 0.50, blue: 1.0)
    static let amber = Color(red: 1, green: 0.73, blue: 0.30)
    static let border = cyan.opacity(0.18)
    static let success = Color(red: 0.30, green: 0.88, blue: 0.62)

    static let panelGradient = LinearGradient(
        colors: [panelRaised.opacity(0.94), panel.opacity(0.88)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

/// A quiet HUD field shared by Chat and the Control Room. It keeps the app
/// visually related to the desktop cockpit without competing with the text.
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
            GeometryReader { geometry in
                Path { path in
                    let step: CGFloat = 34
                    stride(from: 0, through: geometry.size.width, by: step).forEach { x in
                        path.move(to: CGPoint(x: x, y: 0))
                        path.addLine(to: CGPoint(x: x, y: geometry.size.height))
                    }
                    stride(from: 0, through: geometry.size.height, by: step).forEach { y in
                        path.move(to: CGPoint(x: 0, y: y))
                        path.addLine(to: CGPoint(x: geometry.size.width, y: y))
                    }
                }
                .stroke(JarvisTheme.cyan.opacity(0.025), lineWidth: 0.5)
            }
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
            Text(eyebrow.uppercased())
                .font(.caption2.monospaced().weight(.semibold))
                .tracking(2)
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
                Text(title.uppercased())
                    .font(.caption.monospaced().weight(.semibold))
                    .tracking(1.4)
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
        .shadow(color: .black.opacity(0.18), radius: 12, y: 6)
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
            Circle().fill(color).frame(width: 6, height: 6)
            Text(text.uppercased())
        }
        .font(.caption2.monospaced().weight(.semibold))
        .foregroundStyle(color)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(color.opacity(0.10), in: .capsule)
        .overlay(Capsule().strokeBorder(color.opacity(0.22)))
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
