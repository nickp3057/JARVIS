import SwiftUI
import UIKit

/// A single transcript row. Alignment carries the speaker: the user is on the
/// right in the accent colour, JARVIS on the left in a neutral fill, and a
/// failed turn is a full-width notice rather than a bubble so it doesn't read
/// as something JARVIS said.
struct MessageBubble: View {
    let message: ChatMessage
    var onRetry: (() -> Void)?

    var body: some View {
        Group {
            switch message.role {
            case .user:
                row(alignment: .trailing) {
                    bubble(background: Color.accentColor, foreground: .white)
                }
            case .jarvis:
                row(alignment: .leading) {
                    bubble(background: Color(.secondarySystemBackground), foreground: .primary)
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
        Text(message.text)
            .textSelection(.enabled)
            .foregroundStyle(foreground)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(background, in: .rect(cornerRadius: 18))
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
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color(.secondarySystemBackground), in: .rect(cornerRadius: 12))
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
    @State private var animating = false

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(.secondary)
                    .frame(width: 7, height: 7)
                    .opacity(animating ? 1 : 0.25)
                    .animation(
                        .easeInOut(duration: 0.6)
                        .repeatForever()
                        .delay(Double(index) * 0.18),
                        value: animating
                    )
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Color(.secondarySystemBackground), in: .rect(cornerRadius: 18))
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear { animating = true }
        .accessibilityLabel("JARVIS is replying")
    }
}
