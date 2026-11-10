import SwiftUI
import UIKit

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
    @StateObject private var voiceInput = VoiceInputController()
    @FocusState private var composerFocused: Bool
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var followReply = true
    @State private var bottomVisible = true
    @State private var transcriptHeight: CGFloat = 0

    /// Anchor for `scrollTo` — the transcript's own ids are message ids, and
    /// the typing indicator needs a target of its own.
    private let bottomID = "transcript-bottom"

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if model.connectionState != .online || model.isAwaitingReply {
                    connectionBanner
                }
                if let error = model.historyError {
                    Label(error, systemImage: "arrow.triangle.2.circlepath")
                        .font(.caption).foregroundStyle(JarvisTheme.amber)
                        .padding(.horizontal, 20).padding(.vertical, 8)
                }
                Group {
                    if model.messages.isEmpty && !model.isAwaitingReply {
                        emptyState
                    } else {
                        transcript
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture { composerFocused = false }
                .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            }
            .background { JarvisBackdrop() }
            .navigationTitle(model.messages.isEmpty ? "JARVIS" : model.activeTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbar {
                // The logo beside the title is also the hidden way into Donna
                // mode: tap twice, then press and hold.
                ToolbarItem(placement: .principal) {
                    HStack(spacing: 7) {
                        JarvisCoreMark(size: 22, animated: false)
                            .donnaDoor()
                        Text(model.messages.isEmpty ? "JARVIS" : model.activeTitle)
                            .font(.headline).lineLimit(1)
                    }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button("History", systemImage: "clock.arrow.circlepath") {
                        showingHistory = true
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New chat", systemImage: "square.and.pencil") {
                        voiceInput.stop()
                        model.startNewConversation()
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
                // No keyboard "Done" bar: on iOS 26 it floats over the composer
                // (covering half of it and the send button). Like Messages,
                // swipe down on the transcript or tap it to dismiss.
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
            .alert("Voice input", isPresented: Binding(
                get: { voiceInput.errorMessage != nil },
                set: { if !$0 { voiceInput.errorMessage = nil } }
            )) {
                Button("OK") { voiceInput.errorMessage = nil }
            } message: {
                Text(voiceInput.errorMessage ?? "")
            }
            .onDisappear { voiceInput.stop() }
            .onChange(of: model.conversationID) { voiceInput.stop(); followReply = true }
            .onChange(of: scenePhase) { _, phase in if phase != .active { voiceInput.stop() } }
        }
    }

    private var connectionBanner: some View {
        Button {
            if model.connectionState == .unconfigured { showingSettings = true }
            else { model.refreshConnectionStatus() }
        } label: {
            HStack(spacing: 10) {
                ZStack {
                    Circle().fill(connectionColor.opacity(0.13)).frame(width: 26, height: 26)
                    Image(systemName: model.connectionState.symbol).font(.caption.weight(.semibold))
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(model.connectionState.label)
                        .font(.caption.weight(.semibold))
                    if case .offline(let detail) = model.connectionState {
                        Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer()
                if model.isAwaitingReply {
                    Text("Working on it").font(.caption).foregroundStyle(JarvisTheme.amber)
                    ProgressView().tint(JarvisTheme.amber).controlSize(.small)
                } else {
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.semibold)).foregroundStyle(.tertiary)
                }
            }
            .foregroundStyle(connectionColor)
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
        }
        .buttonStyle(.plain)
    }

    private var connectionColor: Color {
        switch model.connectionState {
        case .online: return JarvisTheme.success
        case .checking: return JarvisTheme.amber
        case .unconfigured, .offline: return JarvisTheme.amber
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
                } else if model.filteredConversations(matching: historySearch).isEmpty {
                    ContentUnavailableView.search(text: historySearch)
                } else {
                    List(model.filteredConversations(matching: historySearch)) { conversation in
                        conversationRow(conversation)
                    }
                }
            }
            .searchable(text: $historySearch, prompt: "Search conversations")
            .scrollContentBackground(.hidden)
            .background(JarvisTheme.background)
            .navigationTitle("Conversations")
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
        .listRowBackground(JarvisTheme.panel.opacity(0.7))
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
                    ForEach(model.messages.filter { !($0.role == .jarvis && $0.text.isEmpty) }) { message in
                        MessageBubble(
                            message: message,
                            onRetry: retryAction(for: message),
                            onReport: reportAction(for: message)
                        )
                        .id(message.id)
                    }

                    if !model.isAwaitingReply {
                        ForEach(model.approvals) { approval in
                            ApprovalCard(approval: approval,
                                         onApprove: { model.decide(approval, approve: true) },
                                         onDeny: { model.decide(approval, approve: false) })
                        }
                    }

                    if model.isAwaitingReply, let status = model.toolStatus {
                        Label(status + "…", systemImage: "gearshape.2")
                            .font(.footnote)
                            .foregroundStyle(JarvisTheme.amber)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .accessibilityLabel("JARVIS is \(status.lowercased())")
                    } else if model.isAwaitingReply && model.messages.last?.text.isEmpty == true {
                        TypingIndicator()
                    }

                    Color.clear
                        .frame(height: 1)
                        .id(bottomID)
                        .background(GeometryReader { geometry in
                            Color.clear.preference(key: TranscriptBottomPreference.self,
                                value: geometry.frame(in: .named("transcript")).maxY)
                        })
                }
                .frame(maxWidth: 760)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity)
            }
            .coordinateSpace(name: "transcript")
            .background(GeometryReader { geometry in
                Color.clear.onAppear { transcriptHeight = geometry.size.height }
                    .onChange(of: geometry.size.height) { _, height in transcriptHeight = height }
            })
            .onPreferenceChange(TranscriptBottomPreference.self) { bottom in
                bottomVisible = bottom <= transcriptHeight + 60
                if bottomVisible { followReply = true }
            }
            .simultaneousGesture(DragGesture().onChanged { _ in followReply = false }
                .onEnded { _ in followReply = bottomVisible })
            .overlay(alignment: .bottomTrailing) {
                if !followReply {
                    Button { followReply = true; scrollToBottom(proxy) } label: {
                        Label("Latest", systemImage: "arrow.down")
                            .font(.caption.weight(.semibold)).padding(12)
                            .background(JarvisTheme.panelRaised, in: .capsule)
                            .overlay(Capsule().strokeBorder(JarvisTheme.border))
                    }.padding(16)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .defaultScrollAnchor(.bottom)
            .keepsBottomOnResize()
            .onChange(of: model.messages.count) { if followReply { scrollToBottom(proxy) } }
            .onChange(of: model.approvals) { if followReply { scrollToBottom(proxy) } }
            .onChange(of: model.messages.last?.text) {
                if followReply { proxy.scrollTo(bottomID, anchor: .bottom) }
            }
            .onChange(of: model.isAwaitingReply) { _, replying in
                if replying { followReply = true }
                if followReply { scrollToBottom(proxy) }
            }
            .onChange(of: composerFocused) {
                // iOS 18+ keeps the bottom pinned while the keyboard resizes the
                // transcript; earlier systems need one nudge, un-animated so it
                // doesn't fight the keyboard's own animation.
                guard #unavailable(iOS 18), followReply else { return }
                proxy.scrollTo(bottomID, anchor: .bottom)
            }
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
        ScrollView {
            VStack(spacing: 22) {
                JarvisCoreMark(size: 120).donnaDoor().padding(.top, 14)
                VStack(spacing: 10) {
                    Text("At your service")
                        .font(.caption.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                    Text(settings.isConfigured ? "A thought away, sir." : "Let’s get connected.")
                        .font(.largeTitle.weight(.semibold))
                        .multilineTextAlignment(.center)
                    if !settings.isConfigured {
                        Text("Connect to JARVIS Core to bring your assistant along.")
                            .font(.subheadline).foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                }
                if !settings.isConfigured {
                    Button("Connect to JARVIS", systemImage: "network") { showingSettings = true }
                        .buttonStyle(.borderedProminent).foregroundStyle(JarvisTheme.background)
                }
            }
            .frame(maxWidth: 580)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// One rounded field with the mic and send buttons inside it. Every control
    /// is 44 pt, bottom-aligned, so single- and multi-line input sit on the same
    /// grid and the row never shifts when the keyboard or focus changes.
    private var composer: some View {
        VStack(spacing: 6) {
            if voiceInput.isListening {
                HStack(spacing: 7) {
                    Image(systemName: "waveform").symbolEffect(.variableColor.iterative, isActive: !reduceMotion)
                    Text("Listening — tap the mic when you’re done")
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(JarvisTheme.amber)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 6)
            }
            HStack(alignment: .bottom, spacing: 0) {
                TextField("Message JARVIS", text: $model.draft, axis: .vertical)
                    .lineLimit(1...5)
                    .textFieldStyle(.plain)
                    .focused($composerFocused)
                    .submitLabel(.return)
                    .onChange(of: model.draft) { model.draftChanged() }
                    .padding(.leading, 16)
                    .padding(.vertical, 11)
                    .frame(minHeight: 44)

                Button {
                    voiceInput.toggle(existingText: model.draft) { transcript in
                        model.draft = transcript
                        model.draftChanged()
                    }
                } label: {
                    Image(systemName: voiceInput.isListening ? "mic.fill" : "mic")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(voiceInput.isListening ? JarvisTheme.amber : JarvisTheme.cyan)
                        .frame(width: 44, height: 44)
                        .contentShape(.rect)
                }
                .disabled(model.isAwaitingReply)
                .accessibilityLabel(voiceInput.isListening || voiceInput.isStarting ? "Stop dictation" : "Dictate message")

                Button {
                    if model.isAwaitingReply {
                        model.stopGenerating()
                    } else {
                        voiceInput.stop()
                        model.send()
                    }
                } label: {
                    Image(systemName: model.isAwaitingReply ? "stop.fill" : "arrow.up")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(JarvisTheme.backgroundDeep)
                        .frame(width: 34, height: 34)
                        .background(model.isAwaitingReply ? JarvisTheme.amber : JarvisTheme.cyan, in: .circle)
                        .frame(width: 44, height: 44)
                        .contentShape(.rect)
                }
                .disabled(!model.isAwaitingReply && !model.canSend)
                .opacity(!model.isAwaitingReply && !model.canSend ? 0.45 : 1)
                .accessibilityLabel(model.isAwaitingReply ? "Stop generating" : "Send")
                .sensoryFeedback(.impact(flexibility: .soft), trigger: model.isAwaitingReply)
            }
            .padding(4)
            .jarvisSurface(in: .rect(cornerRadius: 26))
            .overlay(RoundedRectangle(cornerRadius: 26).strokeBorder(
                voiceInput.isListening ? JarvisTheme.amber.opacity(0.55) : JarvisTheme.border
            ))
        }
        .frame(maxWidth: 760)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) {
            proxy.scrollTo(bottomID, anchor: .bottom)
        }
    }
}

private extension View {
    /// iOS 18+: stay pinned to the newest message when the scroll view resizes
    /// (keyboard, composer growth). No-op on iOS 17, handled by an explicit scroll.
    @ViewBuilder func keepsBottomOnResize() -> some View {
        if #available(iOS 18, *) {
            defaultScrollAnchor(.bottom, for: .sizeChanges)
        } else {
            self
        }
    }
}

/// Something JARVIS staged that needs Nick's yes: a Schwab order, a fix, a
/// scene. Approve and Deny send "approve <code>" / "deny <code>" as his own
/// message; nothing runs until Core's gate reads it.
private struct ApprovalCard: View {
    let approval: PendingApproval
    let onApprove: () -> Void
    let onDeny: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Needs your approval", systemImage: "checkmark.shield")
                .font(.caption.weight(.semibold))
                .foregroundStyle(JarvisTheme.amber)
            Text(approval.description.prefix(1).uppercased() + approval.description.dropFirst())
                .font(.subheadline)
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                Button(action: onDeny) {
                    Label("Deny", systemImage: "xmark").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(.secondary)
                Button(action: onApprove) {
                    Label("Approve", systemImage: "checkmark").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(JarvisTheme.success)
            }
            .controlSize(.large)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(JarvisTheme.panel, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(JarvisTheme.amber.opacity(0.35)))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Needs your approval: \(approval.description)")
    }
}

private struct TranscriptBottomPreference: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
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
                            .foregroundStyle(JarvisTheme.success)
                    }
                }
                if let errorText {
                    Section {
                        Label(errorText, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(JarvisTheme.amber)
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background { JarvisBackdrop() }
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

private extension View {
    /// Tap twice, then press and hold, opens Donna mode. No hint, no label:
    /// it's meant to be found only by someone who knows, and a plain hold
    /// (easy to do by accident) no longer opens her.
    func donnaDoor() -> some View { modifier(DonnaDoor()) }
}

private struct DonnaDoor: ViewModifier {
    /// When the double tap landed; the hold has to start soon after.
    @State private var armedAt: Date?

    func body(content: Content) -> some View {
        content
            .onTapGesture(count: 2) { armedAt = Date() }
            .simultaneousGesture(
                LongPressGesture(minimumDuration: 0.6).onEnded { _ in
                    guard let armedAt, Date().timeIntervalSince(armedAt) < 2.0 else { return }
                    self.armedAt = nil
                    UIImpactFeedbackGenerator(style: .soft).impactOccurred()
                    withAnimation(.easeInOut(duration: 0.6)) { DonnaPresenter.shared.showing = true }
                }
            )
    }
}
