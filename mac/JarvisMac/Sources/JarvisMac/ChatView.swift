import AppKit
import SwiftUI

/// Conversations with JARVIS: history on the left, the transcript on the right.
struct ChatView: View {
    @ObservedObject var model: ChatModel
    @State private var showingHistory = true

    var body: some View {
        HSplitView {
            if showingHistory {
                history
                    .frame(minWidth: 200, idealWidth: 240, maxWidth: 340)
            }
            VStack(spacing: 0) {
                transcript
                Divider()
                Composer(text: $model.draft, placeholder: "Message JARVIS", tint: JarvisTheme.accent,
                         fieldBackground: JarvisTheme.card, border: JarvisTheme.border,
                         busy: model.thinking) { model.send() }
            }
            .frame(minWidth: 380)
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { showingHistory.toggle() } label: {
                    Label("History", systemImage: "sidebar.squares.left")
                }
                .help(showingHistory ? "Hide history" : "Show history")
                Button { model.newChat() } label: { Label("New Chat", systemImage: "square.and.pencil") }
                    .help("New chat (⌘N)")
                    .disabled(model.thinking)
            }
        }
        .task { await model.loadSessions() }
        .onReceive(NotificationCenter.default.publisher(for: .jarvisNewChat)) { _ in model.newChat() }
    }

    // MARK: History

    private var history: some View {
        List(selection: Binding(get: { model.selectedSession },
                                set: { id in Task { await model.open(id) } })) {
            ForEach(model.sessions) { session in
                Text(session.label)
                    .lineLimit(2)
                    .padding(.vertical, 3)
                    .tag(Optional(session.sessionID))
                    .contextMenu {
                        Button("Delete Conversation", role: .destructive) {
                            Task { await model.delete(session.sessionID) }
                        }
                    }
            }
        }
        .listStyle(.sidebar)
        .scrollContentBackground(.hidden)
        .background(Color.black.opacity(0.18))
        .overlay {
            if model.sessions.isEmpty {
                Text("No conversations yet").foregroundStyle(.secondary)
            }
        }
        .refreshable { await model.loadSessions() }
    }

    // MARK: Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if model.lines.isEmpty && !model.thinking {
                        welcome
                    }
                    ForEach(model.lines) { line in
                        bubble(line).id(line.id)
                    }
                    if model.thinking, model.lines.last?.text.isEmpty ?? true {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("Thinking…").font(.callout).foregroundStyle(.secondary)
                        }
                    }
                    if let error = model.error { ErrorLine(text: error) }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 18)
            }
            .onChange(of: model.lines) { proxy.scrollTo("bottom", anchor: .bottom) }
            .onChange(of: model.selectedSession) { proxy.scrollTo("bottom", anchor: .bottom) }
        }
    }

    private static let suggestions = [
        ("sun.horizon", "Brief me on my day"),
        ("lightbulb.2", "Set the room for movie night"),
        ("arrow.down.circle", "What's downloading?"),
        ("chart.line.uptrend.xyaxis", "How's my portfolio?"),
        ("wrench.and.screwdriver", "How are your systems?"),
        ("checklist", "What's on my to-do list?"),
    ]

    private var welcome: some View {
        VStack(spacing: 14) {
            ReactorEmblem(size: 96)
            Text("Good to see you, sir.")
                .font(.system(size: 26, weight: .semibold))
            Text("Ask for anything: the room, the media library, reminders, the markets.")
                .foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 10)], spacing: 10) {
                ForEach(Self.suggestions, id: \.1) { symbol, text in
                    Button {
                        model.draft = text
                        model.send()
                    } label: {
                        GlowTile(active: false) {
                            Label(text, systemImage: symbol).foregroundStyle(.white.opacity(0.85))
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            .frame(maxWidth: 720)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 70)
    }

    private func copyButton(_ text: String) -> some View {
        Button("Copy") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
        }
    }

    @ViewBuilder
    private func bubble(_ line: ChatLine) -> some View {
        if line.text.isEmpty {
            EmptyView()
        } else if line.fromNick {
            HStack {
                Spacer(minLength: 120)
                Text(line.text)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 9)
                    .background(LinearGradient(colors: [JarvisTheme.userBubble, JarvisTheme.accentDeep.opacity(0.8)],
                                               startPoint: .topLeading, endPoint: .bottomTrailing),
                                in: .rect(cornerRadius: 16))
                    .textSelection(.enabled)
                    .contextMenu { copyButton(line.text) }
            }
            .font(.system(size: 14))
        } else {
            HStack(alignment: .top, spacing: 12) {
                ReactorEmblem(size: 26)
                    .padding(.top, 1)
                Text(markdown(line.text))
                    .font(.system(size: 14))
                    .lineSpacing(3)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(JarvisTheme.panel, in: .rect(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(JarvisTheme.border))
                    .frame(maxWidth: 720, alignment: .leading)
                    .contextMenu { copyButton(line.text) }
                Spacer(minLength: 40)
            }
        }
    }
}
