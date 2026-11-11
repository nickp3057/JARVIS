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

/// JARVIS's accent: the phone's Arc cyan.
enum JarvisTheme {
    static let accent = adaptiveColor(0x00739E, 0x59E0FF)
    static let userBubble = adaptiveColor(0x0A72AD, 0x0E5F86)
    static let card = Color(nsColor: .controlBackgroundColor)
    static let border = Color(nsColor: .separatorColor)
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
