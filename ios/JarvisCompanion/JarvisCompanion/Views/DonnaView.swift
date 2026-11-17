import SwiftUI
import UIKit

/// Donna mode's colours: a soft, warm rose. Calm on purpose, nothing pulses.
enum DonnaTheme {
    private static func adaptive(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light) })
    }
    static let backgroundTop = adaptive(0xFDF1F4, 0x2A141F)
    static let backgroundBottom = adaptive(0xF6DCE4, 0x1A0C13)
    static let rose = adaptive(0xC9506F, 0xF08AA6)
    static let bubble = adaptive(0xD9637F, 0x8E3653)
    static let card = adaptive(0xFFFFFF, 0x3A1D2B)
    static let border = adaptive(0xEBC3CF, 0x553040)
}

private extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                  blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}

/// The whole of Donna mode: covers the app (tab bar included) until he leaves.
struct DonnaView: View {
    @EnvironmentObject private var settings: AppSettings
    @StateObject private var model: DonnaModel
    @ObservedObject private var presenter = DonnaPresenter.shared
    @FocusState private var composing: Bool
    @State private var unwound = false
    @State private var showingMemory = false
    @State private var showingImport = false
    @State private var ending = false
    /// Rebuilt whenever the box empties, so a long draft's height collapses
    /// and the keyboard can't commit a half-finished word back into it.
    @State private var composerID = UUID()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase

    init(settings: AppSettings) {
        _model = StateObject(wrappedValue: DonnaModel(settings: settings))
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            transcript
            composer
        }
        .background {
            LinearGradient(colors: [DonnaTheme.backgroundTop, DonnaTheme.backgroundBottom],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
        }
        .tint(DonnaTheme.rose)
        .task {
            // Unwind straight away; the load can take seconds (Core writes an
            // idle session's note first) and the rings used to sit until it returned.
            withAnimation(reduceMotion ? nil : .spring(duration: 1.1, bounce: 0.15)) { unwound = true }
            await model.load()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await model.refresh() } }
        }
        .sheet(isPresented: $showingMemory) { DonnaMemoryView(model: model) }
        .sheet(isPresented: $showingImport) { DonnaImportView(model: model) }
    }

    // MARK: Header: the orb's rings unwind into one quiet line.

    private var header: some View {
        VStack(spacing: 10) {
            HStack {
                Button { leave() } label: {
                    Image(systemName: "chevron.down").font(.body.weight(.semibold))
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Leave Donna")
                Spacer()
                Menu {
                    Button("End session", systemImage: "checkmark.circle") { end() }
                        .disabled(model.messages.isEmpty)
                    Button("What Donna knows", systemImage: "book.closed") { showingMemory = true }
                    Button("Bring old chats over", systemImage: "square.and.arrow.down") { showingImport = true }
                } label: {
                    Image(systemName: "ellipsis.circle").font(.body.weight(.semibold))
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Donna options")
            }
            .foregroundStyle(DonnaTheme.rose)
            .padding(.horizontal, 8)
            .overlay {
                Capsule()
                    .stroke(DonnaTheme.rose, lineWidth: 2)
                    .frame(width: unwound ? 150 : 54, height: unwound ? 2 : 54)
                    .opacity(unwound ? 0.9 : 0.6)
                    .contentShape(Rectangle().inset(by: -16))
                    .onLongPressGesture(minimumDuration: 0.6) { leave() }
                    .accessibilityHidden(true)
            }
            Text("Donna")
                .font(.system(.title3, design: .serif).weight(.semibold))
                .foregroundStyle(DonnaTheme.rose)
                .opacity(unwound ? 1 : 0)
        }
        .padding(.bottom, 6)
    }

    // MARK: Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    if model.messages.isEmpty && !model.loading {
                        welcome
                    }
                    ForEach(model.messages) { bubble($0).id($0.id) }
                    if model.thinking {
                        HStack(spacing: 8) {
                            ProgressView().tint(DonnaTheme.rose)
                            Text("Donna's thinking…").font(.footnote).foregroundStyle(.secondary)
                            Spacer()
                        }
                        .padding(.horizontal, 4)
                        .id("thinking")
                    }
                    if let error = model.error {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: model.messages.count) {
                withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: model.thinking) {
                withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onAppear { proxy.scrollTo("bottom", anchor: .bottom) }
        }
    }

    private var welcome: some View {
        VStack(spacing: 10) {
            Text(model.hasCaseFile ? "Welcome back, Nick." : "Hi, Nick. I'm Donna.")
                .font(.system(.title2, design: .serif).weight(.semibold))
            Text(model.hasCaseFile ? "What's on your mind?"
                 : "This is just between us. Tell me what's going on, big or small.")
                .font(.subheadline).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 60)
        .padding(.horizontal, 24)
    }

    private func bubble(_ message: DonnaMessage) -> some View {
        HStack {
            if message.fromNick { Spacer(minLength: 48) }
            Text(message.text)
                .font(.body)
                .foregroundStyle(message.fromNick ? Color.white : Color.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(message.fromNick ? AnyShapeStyle(DonnaTheme.bubble) : AnyShapeStyle(DonnaTheme.card),
                            in: .rect(cornerRadius: 18))
                .overlay {
                    if !message.fromNick {
                        RoundedRectangle(cornerRadius: 18).strokeBorder(DonnaTheme.border)
                    }
                }
                .textSelection(.enabled)
            if !message.fromNick { Spacer(minLength: 32) }
        }
    }

    // MARK: Composer

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Talk to Donna", text: $model.draft, axis: .vertical)
                .lineLimit(1...6)
                .focused($composing)
                .id(composerID)
                .onChange(of: model.draft) { old, new in
                    if model.isEcho(new) {
                        model.draft = ""
                    } else if new.isEmpty && !old.isEmpty {
                        resetComposer()
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 11)
                .background(DonnaTheme.card, in: .rect(cornerRadius: 22))
                .overlay(RoundedRectangle(cornerRadius: 22).strokeBorder(DonnaTheme.border))
            Button { model.send() } label: {
                Image(systemName: "arrow.up")
                    .font(.body.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 44, height: 44)
                    .background(DonnaTheme.rose, in: .circle)
            }
            .disabled(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.thinking)
            .opacity(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.5 : 1)
            .accessibilityLabel("Send")
            .sensoryFeedback(.impact(weight: .light), trigger: model.messages.count)
        }
        .padding(.horizontal, 12)
        .padding(.top, 6)
        .padding(.bottom, 10)
    }

    // MARK: Actions

    /// A fresh text field: drops the old height and any pending keyboard
    /// text, then takes focus back so the keyboard stays up.
    private func resetComposer() {
        let wasFocused = composing
        composerID = UUID()
        if wasFocused { DispatchQueue.main.async { composing = true } }
    }

    private func leave() {
        composing = false
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.45)) { unwound = false }
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.5).delay(reduceMotion ? 0 : 0.15)) {
            presenter.showing = false
        }
    }

    private func end() {
        guard !ending else { return }
        ending = true
        Task {
            let ended = await model.endSession()
            ending = false
            if ended { leave() }  // ending the session closes Donna
        }
    }
}

// MARK: - What Donna knows

struct DonnaMemoryView: View {
    @ObservedObject var model: DonnaModel
    @Environment(\.dismiss) private var dismiss
    @State private var memory: DonnaMemory?
    @State private var error: String?
    @State private var confirmingForget = false

    var body: some View {
        NavigationStack {
            List {
                if let memory {
                    Section("Case file") {
                        Text(memory.caseFile.isEmpty ? "Nothing yet. She starts one after your first session."
                                                     : memory.caseFile)
                            .font(.callout)
                            .foregroundStyle(memory.caseFile.isEmpty ? .secondary : .primary)
                            .textSelection(.enabled)
                    }
                    if !memory.followups.isEmpty {
                        Section("She'll ask about") {
                            ForEach(memory.followups) { Text($0.text).font(.callout) }
                        }
                    }
                    if !memory.moods.isEmpty {
                        Section("Mood") {
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(alignment: .bottom, spacing: 6) {
                                    ForEach(memory.moods) { mood in
                                        VStack(spacing: 4) {
                                            RoundedRectangle(cornerRadius: 3)
                                                .fill(DonnaTheme.rose.opacity(0.35 + Double(mood.score) * 0.065))
                                                .frame(width: 14, height: CGFloat(mood.score) * 7)
                                            Text(Date(timeIntervalSince1970: mood.at), format: .dateTime.day())
                                                .font(.caption2).foregroundStyle(.secondary)
                                        }
                                        .accessibilityElement(children: .ignore)
                                        .accessibilityLabel("\(mood.score) out of 10")
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                        }
                    }
                    Section("Session notes") {
                        if memory.notes.isEmpty {
                            Text("None yet.").foregroundStyle(.secondary)
                        }
                        ForEach(memory.notes) { note in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(Date(timeIntervalSince1970: note.startedAt),
                                     format: .dateTime.weekday(.wide).month(.abbreviated).day())
                                    .font(.caption.weight(.semibold)).foregroundStyle(DonnaTheme.rose)
                                Text(note.note).font(.callout)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                    // Out of the way on purpose: the last thing on the page.
                    Section {
                        Button("Erase Donna's memory") { confirmingForget = true }
                            .font(.footnote)
                            .foregroundStyle(.red.opacity(0.8))
                            .frame(maxWidth: .infinity)
                    }
                    .listRowBackground(Color.clear)
                } else if let error {
                    Text(error).foregroundStyle(.secondary)
                } else {
                    ProgressView()
                }
            }
            .scrollContentBackground(.hidden)
            .background(DonnaTheme.backgroundTop)
            .navigationTitle("What Donna knows")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .confirmationDialog("Erase everything Donna knows?", isPresented: $confirmingForget,
                                titleVisibility: .visible) {
                Button("Erase everything", role: .destructive) {
                    Task {
                        await model.forgetEverything()
                        dismiss()
                    }
                }
            } message: {
                Text("Her notes, your case file, moods and check-ins are deleted for good. This can't be undone.")
            }
            .task {
                do { memory = try await model.memory() } catch { self.error = error.localizedDescription }
            }
        }
        .tint(DonnaTheme.rose)
    }
}

// MARK: - Bring old chats over

struct DonnaImportView: View {
    @ObservedObject var model: DonnaModel
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var working = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Text("Paste a past conversation, like your Help Mk III chat. Donna reads it once and keeps a case file, so she doesn't start from zero. The chat itself isn't stored.")
                    .font(.footnote).foregroundStyle(.secondary)
                TextEditor(text: $text)
                    .scrollContentBackground(.hidden)
                    .padding(10)
                    .background(DonnaTheme.card, in: .rect(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(DonnaTheme.border))
                if let error = model.error, !working {
                    Text(error).font(.footnote).foregroundStyle(.secondary)
                }
            }
            .padding(16)
            .background(DonnaTheme.backgroundTop)
            .navigationTitle("Bring old chats over")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if working { ProgressView() } else {
                        Button("Bring it over") {
                            working = true
                            Task {
                                let ok = await model.importHistory(text)
                                working = false
                                if ok { dismiss() }
                            }
                        }
                        .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).count < 50)
                    }
                }
            }
        }
        .tint(DonnaTheme.rose)
        .interactiveDismissDisabled(working)
    }
}
