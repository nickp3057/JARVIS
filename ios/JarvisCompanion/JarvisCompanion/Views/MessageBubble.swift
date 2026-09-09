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
                    bubble(background: JarvisTheme.cyan.opacity(0.14), foreground: .primary)
                }
            case .jarvis:
                row(alignment: .leading) {
                    bubble(background: JarvisTheme.panel, foreground: .primary)
                }
            case .error:
                errorNotice
            }
        }
        .contextMenu {
            Button("Copy", systemImage: "doc.on.doc") {
                UIPasteboard.general.string = message.text
            }
            ShareLink(item: message.text) {
                Label("Share", systemImage: "square.and.arrow.up")
            }
            if let onReport {
                Button("Send to AI to debug", systemImage: "ladybug") {
                    onReport()
                }
            }
        }
    }

    private func row<Content: View>(
        alignment: HorizontalAlignment,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack {
            if alignment == .trailing { Spacer(minLength: 48) }
            content()
            if alignment == .leading { Spacer(minLength: 48) }
        }
    }

    private func bubble(background: Color, foreground: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(message.role == .user ? "YOU" : "JARVIS")
                    .font(.caption2.monospaced().weight(.semibold)).tracking(1.5)
                    .foregroundStyle(JarvisTheme.cyan)
                Spacer(minLength: 12)
                Text(message.date, style: .time).font(.caption2).foregroundStyle(.secondary)
            }
            Text(message.text)
                .textSelection(.enabled)
                .lineSpacing(4)
        }
            .foregroundStyle(foreground)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(background, in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(JarvisTheme.border))
    }

    private var errorNotice: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label {
                Text(message.text)
                    .font(.footnote)
                    .foregroundStyle(.primary)
            } icon: {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
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
        .background(JarvisTheme.panel, in: .rect(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(.orange.opacity(0.35), lineWidth: 1)
        )
    }
}

/// Three pulsing dots shown in JARVIS's slot while a request is in flight.
/// Server-side turns can involve tool calls and run for many seconds, so the
/// wait needs to look deliberate rather than hung.
struct TypingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var animating = false

    var body: some View {
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
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(JarvisTheme.panel, in: .rect(cornerRadius: 18))
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear { animating = !reduceMotion }
        .accessibilityLabel("JARVIS is replying")
    }
}
