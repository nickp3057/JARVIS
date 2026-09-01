import SwiftUI

/// The whole app: one scrolling transcript and one composer.
struct ChatView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: ChatViewModel

    @State private var showingSettings = false
    @State private var showingHistory = false
    @State private var confirmingClear = false
    @State private var historySearch = ""
    @State private var renameTarget: ChatConversation?
    @State private var renameText = ""
    @State private var deleteTarget: ChatConversation?
    @State private var showingDebugReport = false
    @State private var debugFocusMessageID: UUID?
    @FocusState private var composerFocused: Bool

    /// Anchor for `scrollTo` — the transcript's own ids are message ids, and
    /// the typing indicator needs a target of its own.
    private let bottomID = "transcript-bottom"

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                connectionBanner
                if model.messages.isEmpty && !model.isAwaitingReply {
                    emptyState
                } else {
                    transcript
                }
                Divider()
                composer
            }
            .navigationTitle("Jarvis")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("History", systemImage: "clock.arrow.circlepath") {
                        showingHistory = true
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("New chat", systemImage: "square.and.pencil") {
                            model.startNewConversation()
                        }
                        Button("Settings", systemImage: "gearshape") { showingSettings = true }
                        Button("Send to AI to debug", systemImage: "ladybug") {
                            beginDebugReport()
                        }
                        .disabled(model.messages.isEmpty || model.isAwaitingReply)
                        Button("Clear conversation", systemImage: "trash", role: .destructive) {
                            confirmingClear = true
                        }
                        .disabled(model.messages.isEmpty)
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .sheet(isPresented: $showingSettings, onDismiss: model.refreshConnectionStatus) {
                SettingsView().environmentObject(settings)
            }
            .sheet(isPresented: $showingHistory) {
                historySheet
            }
            .sheet(isPresented: $showingDebugReport) {
                DebugReportSheet(
                    messageCount: min(model.messages.count, 100),
                    focusedMessage: model.messages.first(where: { $0.id == debugFocusMessageID })?.text,
                    onSend: { issue in
                        try await model.submitDebugReport(
                            issue: issue,
                            focusMessageID: debugFocusMessageID
                        )
                    }
                )
            }
            .confirmationDialog(
                "Clear this conversation?",
                isPresented: $confirmingClear,
                titleVisibility: .visible
            ) {
                Button("Clear", role: .destructive) { model.clearTranscript() }
            } message: {
                Text("This permanently deletes the current conversation from this device and JARVIS Core.")
            }
            .alert("Rename conversation", isPresented: Binding(
                get: { renameTarget != nil },
                set: { if !$0 { renameTarget = nil } }
            )) {
                TextField("Name", text: $renameText)
                Button("Cancel", role: .cancel) { renameTarget = nil }
                Button("Save") {
                    if let renameTarget { model.renameConversation(renameTarget.id, to: renameText) }
                    renameTarget = nil
                }
            }
            .confirmationDialog(
                "Delete this conversation?",
                isPresented: Binding(
                    get: { deleteTarget != nil },
                    set: { if !$0 { deleteTarget = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Delete", role: .destructive) {
                    if let deleteTarget { model.deleteConversation(deleteTarget.id) }
                    deleteTarget = nil
                }
            } message: {
                Text("This deletes the conversation from this device and JARVIS Core.")
            }
        }
    }

    private var connectionBanner: some View {
        Button {
            if case .offline = model.connectionState { model.refreshConnectionStatus() }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: model.connectionState.symbol)
                Text(model.connectionState.label)
                if case .offline(let detail) = model.connectionState {
                    Text("· \(detail)").lineLimit(1)
                }
            }
            .font(.caption)
            .foregroundStyle(model.connectionState == .online ? Color.green : Color.secondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 5)
            .background(Color(.secondarySystemBackground))
        }
        .buttonStyle(.plain)
    }

    private var historySheet: some View {
        NavigationStack {
            Group {
                if model.visibleConversations.isEmpty {
                    ContentUnavailableView(
                        "No chat history",
                        systemImage: "bubble.left.and.bubble.right",
                        description: Text("Conversations saved on this device will appear here.")
                    )
                } else {
                    List(model.filteredConversations(matching: historySearch)) { conversation in
                        conversationRow(conversation)
                    }
                }
            }
            .searchable(text: $historySearch, prompt: "Search conversations")
            .navigationTitle("Chat history")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { showingHistory = false }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button("New chat", systemImage: "square.and.pencil") {
                        model.startNewConversation()
                        showingHistory = false
                    }
                }
            }
        }
    }

    private func conversationRow(_ conversation: ChatConversation) -> some View {
        Button {
            model.openConversation(conversation.id)
            showingHistory = false
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(conversation.title).font(.headline).lineLimit(1)
                Text(conversation.preview).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                Text(conversation.updatedAt, format: .dateTime.month().day().hour().minute())
                    .font(.caption).foregroundStyle(.tertiary)
            }
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) {
            Button("Delete", systemImage: "trash", role: .destructive) {
                deleteTarget = conversation
            }
            Button("Rename", systemImage: "pencil") { beginRename(conversation) }
                .tint(.blue)
        }
        .contextMenu {
            Button("Rename", systemImage: "pencil") { beginRename(conversation) }
            Button("Delete", systemImage: "trash", role: .destructive) {
                deleteTarget = conversation
            }
        }
    }

    private func beginRename(_ conversation: ChatConversation) {
        renameText = conversation.title
        renameTarget = conversation
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(model.messages) { message in
                        MessageBubble(
                            message: message,
                            onRetry: retryAction(for: message),
                            onReport: reportAction(for: message)
                        )
                        .id(message.id)
                    }

                    if model.isAwaitingReply {
                        TypingIndicator()
                    }

                    Color.clear
                        .frame(height: 1)
                        .id(bottomID)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .scrollDismissesKeyboard(.interactively)
            .defaultScrollAnchor(.bottom)
            .onChange(of: model.messages.count) { scrollToBottom(proxy) }
            .onChange(of: model.isAwaitingReply) { scrollToBottom(proxy) }
        }
    }

    /// Only the newest message gets a retry button; older failures are history.
    private func retryAction(for message: ChatMessage) -> (() -> Void)? {
        guard message.role == .error, message.id == model.messages.last?.id, model.canRetry else {
            return nil
        }
        return { model.retry() }
    }

    private func reportAction(for message: ChatMessage) -> (() -> Void)? {
        guard message.role != .user, !model.isAwaitingReply else { return nil }
        return { beginDebugReport(focusing: message.id) }
    }

    private func beginDebugReport(focusing messageID: UUID? = nil) {
        debugFocusMessageID = messageID
        // Menus and context menus dismiss in the same update cycle. Present on
        // the next cycle so the report sheet is not silently swallowed.
        DispatchQueue.main.async { showingDebugReport = true }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: settings.isConfigured ? "bubble.left.and.bubble.right" : "gearshape")
                .font(.largeTitle)
                .foregroundStyle(.secondary)

            Text(settings.isConfigured ? "No messages yet" : "Set up your server")
                .font(.headline)

            Text(settings.isConfigured
                 ? "Say something to JARVIS."
                 : "Add your JARVIS server address and API token in Settings to get started.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            if !settings.isConfigured {
                Button("Open Settings") { showingSettings = true }
                    .buttonStyle(.borderedProminent)
                    .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, 24)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Message JARVIS", text: $model.draft, axis: .vertical)
                .lineLimit(1...5)
                .textFieldStyle(.plain)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Color(.secondarySystemBackground), in: .rect(cornerRadius: 20))
                .focused($composerFocused)
                .submitLabel(.send)
                .onChange(of: model.draft) { model.draftChanged() }

            Button(action: model.isAwaitingReply ? model.stopGenerating : model.send) {
                Image(systemName: model.isAwaitingReply ? "stop.circle.fill" : "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .symbolRenderingMode(.hierarchical)
            }
            .disabled(!model.isAwaitingReply && !model.canSend)
            .accessibilityLabel(model.isAwaitingReply ? "Stop generating" : "Send")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.25)) {
            proxy.scrollTo(bottomID, anchor: .bottom)
        }
    }
}

private struct DebugReportSheet: View {
    @Environment(\.dismiss) private var dismiss
    let messageCount: Int
    let focusedMessage: String?
    let onSend: (String) async throws -> String

    @State private var issue = ""
    @State private var isSending = false
    @State private var errorText: String?
    @State private var sentReportID: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("What went wrong?") {
                    TextEditor(text: $issue)
                        .frame(minHeight: 120)
                        .accessibilityLabel("What went wrong")
                }
                if let focusedMessage {
                    Section("Response being reported") {
                        Text(focusedMessage).lineLimit(6)
                    }
                }
                Section("Included with your approval") {
                    Label("Up to \(messageCount) messages from this conversation", systemImage: "bubble.left.and.bubble.right")
                    Label("App version, device type, and connection state", systemImage: "iphone")
                    Text("Your API token and server address are never included.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                if let sentReportID {
                    Section {
                        Label("Saved as report \(sentReportID)", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    }
                }
                if let errorText {
                    Section {
                        Label(errorText, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }
            }
            .navigationTitle("Send to AI to debug")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(sentReportID == nil ? "Cancel" : "Done") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if isSending {
                        ProgressView()
                            .accessibilityLabel("Sending report")
                    } else {
                        Button("Send") { submit() }
                        .disabled(
                            sentReportID != nil
                            || issue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        )
                    }
                }
            }
            .interactiveDismissDisabled(isSending)
        }
    }

    private func submit() {
        isSending = true
        errorText = nil
        Task {
            do {
                sentReportID = try await onSend(issue)
            } catch {
                errorText = error.localizedDescription
            }
            isSending = false
        }
    }
}
