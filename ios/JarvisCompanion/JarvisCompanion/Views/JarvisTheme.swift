import SwiftUI

/// Shared cockpit palette. Keep text scalable and reserve amber for activity.
enum JarvisTheme {
    static let background = Color(red: 0.025, green: 0.045, blue: 0.075)
    static let panel = Color(red: 0.055, green: 0.085, blue: 0.125)
    static let cyan = Color(red: 0.25, green: 0.85, blue: 0.95)
    static let amber = Color(red: 1, green: 0.73, blue: 0.30)
    static let border = cyan.opacity(0.18)
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
