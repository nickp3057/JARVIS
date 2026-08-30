import SwiftUI

/// The whole app: one scrolling transcript and one composer.
struct ChatView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: ChatViewModel

    @State private var showingSettings = false
    @State private var showingHistory = false
    @State private var confirmingClear = false
    @FocusState private var composerFocused: Bool

    /// Anchor for `scrollTo` — the transcript's own ids are message ids, and
    /// the typing indicator needs a target of its own.
    private let bottomID = "transcript-bottom"

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
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
                        Button("Clear conversation", systemImage: "trash", role: .destructive) {
                            confirmingClear = true
                        }
                        .disabled(model.messages.isEmpty)
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView().environmentObject(settings)
            }
            .sheet(isPresented: $showingHistory) {
                historySheet
            }
            .confirmationDialog(
                "Clear this conversation?",
                isPresented: $confirmingClear,
                titleVisibility: .visible
            ) {
                Button("Clear", role: .destructive) { model.clearTranscript() }
            } message: {
                Text("This only clears the transcript stored on this device.")
            }
        }
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
                    List(model.visibleConversations) { conversation in
                        Button {
                            model.openConversation(conversation.id)
                            showingHistory = false
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(conversation.title)
                                    .font(.headline)
                                    .lineLimit(1)
                                Text(conversation.preview)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                Text(conversation.updatedAt, format: .dateTime.month().day().hour().minute())
                                    .font(.caption)
                                    .foregroundStyle(.tertiary)
                            }
                            .padding(.vertical, 4)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
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

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(model.messages) { message in
                        MessageBubble(
                            message: message,
                            onRetry: retryAction(for: message)
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

            Button(action: model.send) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .symbolRenderingMode(.hierarchical)
            }
            .disabled(!model.canSend)
            .accessibilityLabel("Send")
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
