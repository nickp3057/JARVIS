import SwiftUI
import UIKit

/// A single transcript row. Alignment carries the speaker: the user is on the
/// right in the accent colour, JARVIS on the left in a neutral fill, and a
/// failed turn is a full-width notice rather than a bubble so it doesn't read
/// as something JARVIS said.
struct MessageBubble: View {
    let message: ChatMessage
    var onRetry: (() -> Void)?
    var onReport: (() -> Void)?

    var body: some View {
        Group {
            switch message.role {
            case .user:
                row(alignment: .trailing) {
                    VStack(alignment: .trailing, spacing: 4) {
                        bubble(isUser: true, text: message.text, isLast: true)
                        footer(isUser: true)
                    }
                }
            case .jarvis:
                row(alignment: .leading) {
                    // No avatar: replies use the full width. One bubble per
                    // model round ("Checking." / "Added.") instead of one
                    // run-on paragraph.
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(segments.enumerated()), id: \.offset) { index, segment in
                            bubble(isUser: false, text: segment, isLast: index == segments.count - 1)
                        }
                        footer(isUser: false)
                    }
                }
            case .error:
                errorNotice
            }
        }
        .contextMenu {
            Button("Copy", systemImage: "doc.on.doc") {
                UIPasteboard.general.string = plainText
            }
            ShareLink(item: plainText) {
                Label("Share", systemImage: "square.and.arrow.up")
            }
            if let onReport {
                Button("Send to AI to debug", systemImage: "ladybug") {
                    onReport()
                }
            }
        }
    }

    /// The server separates model rounds with U+001E (record separator).
    private static let roundBreak = "\u{1E}"

    private var segments: [String] {
        let parts = message.text.components(separatedBy: Self.roundBreak)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        return parts.isEmpty ? [""] : parts
    }

    private var plainText: String {
        segments.joined(separator: "\n\n")
    }

    private func row<Content: View>(
        alignment: HorizontalAlignment,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack {
            if alignment == .trailing { Spacer(minLength: 32) }
            content()
            if alignment == .leading { Spacer(minLength: 0) }
        }
    }

    /// Flat bubbles; only the last bubble of a turn gets the "tail" corner.
    /// Who/when sits once under the turn instead of inside every bubble.
    private func bubble(isUser: Bool, text: String, isLast: Bool) -> some View {
        Group {
            if isUser {
                Text(text).textSelection(.enabled).lineSpacing(4)
            } else {
                AssistantMessageText(text: text)
            }
        }
        .foregroundStyle(.primary)
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(isUser ? JarvisTheme.blue.opacity(0.28) : JarvisTheme.panel,
                    in: bubbleShape(isUser: isUser, isLast: isLast))
        .overlay {
            if !isUser {
                bubbleShape(isUser: false, isLast: isLast).strokeBorder(JarvisTheme.border)
            }
        }
    }

    private func bubbleShape(isUser: Bool, isLast: Bool) -> UnevenRoundedRectangle {
        UnevenRoundedRectangle(
            topLeadingRadius: 18,
            bottomLeadingRadius: !isUser && isLast ? 5 : 18,
            bottomTrailingRadius: isUser && isLast ? 5 : 18,
            topTrailingRadius: 18
        )
    }

    private func footer(isUser: Bool) -> some View {
        HStack(spacing: 4) {
            if !isUser {
                Text("JARVIS").font(.caption2.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                Text("·")
            }
            Text(message.date, style: .time)
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 6)
    }

    private var errorNotice: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label {
                Text(message.text)
                    .font(.footnote)
                    .foregroundStyle(.primary)
            } icon: {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(JarvisTheme.amber)
            }

            if let onRetry {
                Button("Try again", systemImage: "arrow.clockwise", action: onRetry)
                    .font(.footnote.weight(.medium))
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
            if let onReport {
                Button("Send to AI to debug", systemImage: "ladybug", action: onReport)
                    .font(.footnote.weight(.medium))
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .jarvisSurface(in: .rect(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(JarvisTheme.amber.opacity(0.35), lineWidth: 1)
        )
    }
}

/// Keep line breaks and inline Markdown readable; fenced code gets its own
/// horizontal scrolling surface rather than stretching a message offscreen.
struct AssistantMessageText: View {
    let text: String
    var body: some View {
        let blocks = text.components(separatedBy: "```")
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { index, block in
                if index.isMultiple(of: 2) {
                    if !block.isEmpty {
                        Text((try? AttributedString(markdown: block,
                            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(block))
                            .lineSpacing(5).textSelection(.enabled)
                    }
                } else {
                    let parts = block.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false)
                    let code = parts.count > 1 ? String(parts[1]).trimmingCharacters(in: .newlines) : block
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text(parts.count > 1 && !parts[0].isEmpty ? String(parts[0]).uppercased() : "CODE")
                                .font(.caption2.monospaced()).foregroundStyle(.secondary)
                            Spacer()
                            Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = code }
                                .font(.caption).accessibilityLabel("Copy code")
                        }
                        ScrollView(.horizontal) {
                            Text(code).font(.system(.footnote, design: .monospaced)).textSelection(.enabled)
                                .fixedSize(horizontal: true, vertical: false)
                        }
                    }.padding(12).background(.black.opacity(0.25), in: .rect(cornerRadius: 12))
                }
            }
        }
    }
}

/// Three pulsing dots shown in JARVIS's slot while a request is in flight.
/// Server-side turns can involve tool calls and run for many seconds, so the
/// wait needs to look deliberate rather than hung.
struct TypingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var animating = false

    var body: some View {
        HStack(spacing: 8) {
            // The reactor spins fast while JARVIS thinks (still with Reduce Motion).
            JarvisCoreMark(size: 26, active: true)
            HStack(spacing: 5) {
                ForEach(0..<3, id: \.self) { index in
                    Circle()
                        .fill(JarvisTheme.amber)
                        .frame(width: 7, height: 7)
                        .opacity(animating ? 1 : 0.25)
                        .animation(
                            reduceMotion ? nil : .easeInOut(duration: 0.6)
                            .repeatForever()
                            .delay(Double(index) * 0.18),
                            value: animating
                        )
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .jarvisSurface(in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(JarvisTheme.border))
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear { animating = !reduceMotion }
        .accessibilityLabel("JARVIS is replying")
    }
}
