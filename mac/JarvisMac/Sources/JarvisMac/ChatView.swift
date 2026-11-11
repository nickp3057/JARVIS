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
        .listStyle(.inset)
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

    private var welcome: some View {
        VStack(spacing: 8) {
            Image(systemName: "circle.hexagongrid")
                .font(.system(size: 40, weight: .thin))
                .foregroundStyle(JarvisTheme.accent)
            Text("Good to see you, sir.")
                .font(.title2.weight(.semibold))
            Text("Ask for anything: the room, the media library, reminders, the markets.")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 100)
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
                    .background(JarvisTheme.userBubble, in: .rect(cornerRadius: 16))
                    .textSelection(.enabled)
            }
            .font(.system(size: 14))
        } else {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "circle.hexagongrid.fill")
                    .foregroundStyle(JarvisTheme.accent)
                    .font(.system(size: 15))
                    .padding(.top, 2)
                Text(markdown(line.text))
                    .font(.system(size: 14))
                    .lineSpacing(2)
                    .textSelection(.enabled)
                    .frame(maxWidth: 680, alignment: .leading)
                Spacer(minLength: 40)
            }
        }
    }
}
