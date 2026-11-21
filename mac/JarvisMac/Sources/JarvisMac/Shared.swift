import AppKit
import SwiftUI

func adaptiveColor(_ light: UInt32, _ dark: UInt32) -> Color {
    Color(NSColor(name: nil) { appearance in
        let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        let hex = isDark ? dark : light
        return NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                       blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    })
}

/// The cockpit: deep navy, Arc cyan, and the icon's brushed gold. The main
/// window always runs dark (the reactor reads best on it); Donna keeps her rose.
enum JarvisTheme {
    static let accent = Color(hex: 0x59E0FF)
    static let accentDeep = Color(hex: 0x1FA8D8)
    static let gold = Color(hex: 0xE8B95A)
    static let goldDeep = Color(hex: 0xA8732A)
    static let success = Color(hex: 0x5BE3A3)
    static let warning = Color(hex: 0xF2B84B)
    static let critical = Color(hex: 0xFF6B6B)
    static let backdropTop = Color(hex: 0x0C1C2E)
    static let backdropBottom = Color(hex: 0x03070D)
    static let panel = Color(hex: 0x0E1E30).opacity(0.78)
    static let panelHigh = Color(hex: 0x143049)
    static let border = Color(hex: 0x59E0FF).opacity(0.16)
    static let userBubble = Color(hex: 0x0E5F86)
    static let card = panel
    static let text = Color.white.opacity(0.92)
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255, opacity: alpha)
    }
}

/// The window's backdrop: navy to near-black with a faint cyan bloom.
struct CockpitBackdrop: View {
    var body: some View {
        ZStack {
            LinearGradient(colors: [JarvisTheme.backdropTop, JarvisTheme.backdropBottom], startPoint: .top, endPoint: .bottom)
            RadialGradient(colors: [JarvisTheme.accent.opacity(0.10), .clear], center: .init(x: 0.5, y: 0.0),
                           startRadius: 0, endRadius: 700)
        }
        .ignoresSafeArea()
    }
}

/// A titled glass panel: the building block of every page.
struct Panel<Content: View, Trailing: View>: View {
    let title: String
    let symbol: String
    @ViewBuilder var trailing: () -> Trailing
    @ViewBuilder var content: () -> Content

    init(_ title: String, symbol: String, @ViewBuilder trailing: @escaping () -> Trailing = { EmptyView() },
         @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.symbol = symbol
        self.trailing = trailing
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: symbol).foregroundStyle(JarvisTheme.accent)
                Text(title.uppercased())
                    .font(.system(size: 11, weight: .semibold))
                    .tracking(1.2)
                    .foregroundStyle(.white.opacity(0.6))
                Spacer()
                trailing()
            }
            content()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(JarvisTheme.panel, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.border))
    }
}

/// A small coloured capsule: "3 down", "Autopilot on".
struct Pill: View {
    let text: String
    var color: Color = JarvisTheme.accent
    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold))
            .padding(.horizontal, 8).padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.14), in: .capsule)
            .overlay(Capsule().strokeBorder(color.opacity(0.35)))
    }
}

/// The app icon's reactor, drawn live: gold bezel, cyan orbit, white core.
struct ReactorEmblem: View {
    var size: CGFloat = 64
    var body: some View {
        ZStack {
            Circle().fill(RadialGradient(colors: [JarvisTheme.accent.opacity(0.35), .clear], center: .center,
                                         startRadius: 0, endRadius: size * 0.6))
            ForEach(0..<6, id: \.self) { i in
                Circle()
                    .trim(from: CGFloat(i) / 6 + 0.012, to: CGFloat(i + 1) / 6 - 0.012)
                    .stroke(LinearGradient(colors: [Color(hex: 0xFBE09A), JarvisTheme.gold, JarvisTheme.goldDeep],
                                           startPoint: .top, endPoint: .bottom),
                            style: StrokeStyle(lineWidth: size * 0.07))
                    .frame(width: size * 0.82, height: size * 0.82)
            }
            Circle()
                .trim(from: 0.06, to: 0.94)
                .stroke(JarvisTheme.accent, style: StrokeStyle(lineWidth: size * 0.025, lineCap: .round))
                .rotationEffect(.degrees(-125))
                .frame(width: size * 0.64, height: size * 0.64)
                .shadow(color: JarvisTheme.accent, radius: size * 0.05)
            Circle().fill(Color(hex: 0x0A2638)).frame(width: size * 0.5, height: size * 0.5)
            Circle()
                .fill(RadialGradient(colors: [.white, Color(hex: 0xBFF4FF), JarvisTheme.accent.opacity(0)],
                                     center: .center, startRadius: 0, endRadius: size * 0.22))
                .frame(width: size * 0.44, height: size * 0.44)
            Circle().fill(.white).frame(width: size * 0.13, height: size * 0.13)
                .shadow(color: Color(hex: 0xBFF4FF), radius: size * 0.06)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// A big glanceable tile with a glow when it's on: lights, scenes.
struct GlowTile<Content: View>: View {
    let active: Bool
    var tint: Color = JarvisTheme.accent
    @ViewBuilder var content: () -> Content
    @State private var hovering = false

    var body: some View {
        content()
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
            .background(active ? tint.opacity(0.13) : JarvisTheme.panel, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12)
                .strokeBorder(active ? tint.opacity(0.6) : (hovering ? JarvisTheme.accent.opacity(0.35) : JarvisTheme.border),
                              lineWidth: active ? 1.4 : 1))
            .shadow(color: active ? tint.opacity(0.28) : .clear, radius: 12)
            .onHover { hovering = $0 }
    }
}

/// Bytes and durations for the dashboards.
enum Format {
    static func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }

    static func speed(_ bytesPerSecond: Int) -> String {
        bytes(Int64(bytesPerSecond)) + "/s"
    }

    static func eta(_ seconds: Int) -> String {
        guard seconds > 0, seconds < 8_640_000 else { return "—" }
        let h = seconds / 3600, m = (seconds % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : (m > 0 ? "\(m)m" : "\(seconds)s")
    }

    static func clock(ms: Int) -> String {
        let minutes = ms / 60_000
        return minutes >= 60 ? "\(minutes / 60)h \(minutes % 60)m" : "\(minutes)m"
    }
}

/// Donna's palette: the phone's soft, warm rose. Calm on purpose, nothing pulses.
enum DonnaTheme {
    static let backgroundTop = adaptiveColor(0xFDF1F4, 0x2A141F)
    static let backgroundBottom = adaptiveColor(0xF6DCE4, 0x1A0C13)
    static let rose = adaptiveColor(0xC9506F, 0xF08AA6)
    static let bubble = adaptiveColor(0xD9637F, 0x8E3653)
    static let card = adaptiveColor(0xFFFFFF, 0x3A1D2B)
    static let border = adaptiveColor(0xEBC3CF, 0x553040)
}

/// Inline markdown (bold, italics, code, links) with line breaks kept.
func markdown(_ text: String) -> AttributedString {
    (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
        ?? AttributedString(text)
}

/// The message box: Return sends, Shift- or Option-Return adds a line.
struct Composer: View {
    @Binding var text: String
    let placeholder: String
    let tint: Color
    let fieldBackground: Color
    let border: Color
    let busy: Bool
    let onSend: () -> Void
    @FocusState private var focused: Bool

    private var empty: Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        HStack(alignment: .bottom, spacing: 10) {
            // A real text editor, so Shift-Return makes a genuine new line;
            // plain Return is intercepted to send. A hidden copy of the text
            // sizes the box: it grows with what's written, up to ~10 lines.
            ZStack(alignment: .topLeading) {
                Text(text.isEmpty ? " " : text + " ")
                    .font(.system(size: 14))
                    .padding(.horizontal, 5)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .hidden()
                if text.isEmpty {
                    Text(placeholder)
                        .font(.system(size: 14))
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 5)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $text)
                    .font(.system(size: 14))
                    .scrollContentBackground(.hidden)
                    .scrollIndicators(.never)
                    .focused($focused)
                    .onKeyPress(.return, phases: .down) { press in
                        if press.modifiers.contains(.shift) || press.modifiers.contains(.option) {
                            return .ignored  // the editor inserts the newline itself
                        }
                        onSend()
                        return .handled
                    }
            }
            .frame(maxHeight: 190)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(fieldBackground, in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(border))
            .onTapGesture { focused = true }
            Button(action: onSend) {
                Image(systemName: "arrow.up")
                    .font(.body.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    .background(tint, in: .circle)
            }
            .buttonStyle(.plain)
            .disabled(empty || busy)
            .opacity(empty || busy ? 0.45 : 1)
            .help("Send (Return)")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .onAppear { focused = true }
    }
}

/// A quiet "not connected / something failed" line.
struct ErrorLine: View {
    let text: String
    var body: some View {
        Label(text, systemImage: "exclamationmark.triangle")
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)
    }
}

/// Shown in place of a section until Settings has an address and token.
struct SetupPrompt: View {
    @Environment(\.openSettings) private var openSettings
    var body: some View {
        ContentUnavailableView {
            Label("Connect to JARVIS Core", systemImage: "network")
        } description: {
            Text("Add Core's address and the companion's API token.")
        } actions: {
            Button("Open Settings…") { openSettings() }
                .buttonStyle(.borderedProminent)
        }
    }
}
