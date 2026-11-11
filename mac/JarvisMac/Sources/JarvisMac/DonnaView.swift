import AppKit
import SwiftUI

/// Donna, the private counsellor (brain/donna.py), inside JARVIS: her own
/// rose space, and nothing here touches JARVIS's chat or memory.
struct DonnaView: View {
    @ObservedObject var model: DonnaModel
    @State private var showingMemory = false
    @State private var confirmingEnd = false

    var body: some View {
        VStack(spacing: 0) {
            transcript
            Composer(text: $model.draft, placeholder: "Talk to Donna", tint: DonnaTheme.rose,
                     fieldBackground: DonnaTheme.card, border: DonnaTheme.border,
                     busy: model.thinking) { model.send() }
        }
        .background {
            LinearGradient(colors: [DonnaTheme.backgroundTop, DonnaTheme.backgroundBottom],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { Task { await model.load() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    .keyboardShortcut("r")
                    .help("Pick up anything said on the phone (⌘R)")
                Button { showingMemory = true } label: { Label("What Donna knows", systemImage: "book.closed") }
                    .help("What Donna knows")
                Button { confirmingEnd = true } label: { Label("End Session", systemImage: "checkmark.circle") }
                    .help("End session: Donna writes her note")
                    .disabled(model.messages.isEmpty || model.thinking)
            }
        }
        .confirmationDialog("End this session?", isPresented: $confirmingEnd) {
            Button("End Session") { Task { await model.endSession() } }
        } message: {
            Text("Donna writes her note now; your next message starts a fresh session.")
        }
        .sheet(isPresented: $showingMemory) { DonnaMemoryView(model: model) }
        .task { await model.load() }
        // Catch up on anything said on the phone whenever JARVIS comes back to the front.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.load() }
        }
    }

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
                            ProgressView().controlSize(.small)
                            Text("Donna's thinking…").font(.callout).foregroundStyle(.secondary)
                            Spacer()
                        }
                        .padding(.horizontal, 4)
                    }
                    if let error = model.error { ErrorLine(text: error) }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .frame(maxWidth: 760)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 24)
                .padding(.vertical, 18)
            }
            .onChange(of: model.messages.count) {
                withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: model.thinking) {
                withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo("bottom", anchor: .bottom) }
            }
        }
    }

    private var welcome: some View {
        VStack(spacing: 10) {
            Text(model.hasCaseFile ? "Welcome back, Nick." : "Hi, Nick. I'm Donna.")
                .font(.system(.title, design: .serif).weight(.semibold))
            Text(model.hasCaseFile ? "What's on your mind?"
                 : "This is just between us. Tell me what's going on, big or small.")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 100)
    }

    private func bubble(_ message: DonnaMessage) -> some View {
        HStack {
            if message.fromNick { Spacer(minLength: 100) }
            Text(message.text)
                .foregroundStyle(message.fromNick ? Color.white : Color.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(message.fromNick ? AnyShapeStyle(DonnaTheme.bubble) : AnyShapeStyle(DonnaTheme.card),
                            in: .rect(cornerRadius: 16))
                .overlay {
                    if !message.fromNick {
                        RoundedRectangle(cornerRadius: 16).strokeBorder(DonnaTheme.border)
                    }
                }
                .textSelection(.enabled)
            if !message.fromNick { Spacer(minLength: 80) }
        }
        .font(.system(size: 14))
    }
}

struct DonnaMemoryView: View {
    @ObservedObject var model: DonnaModel
    @Environment(\.dismiss) private var dismiss
    @State private var memory: DonnaMemory?
    @State private var error: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("What Donna knows").font(.system(.title2, design: .serif).weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(16)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if let memory {
                        section("Case file") {
                            Text(memory.caseFile.isEmpty ? "Nothing yet." : memory.caseFile)
                        }
                        if !memory.followups.isEmpty {
                            section("Following up") {
                                ForEach(memory.followups) { Text("• \($0.text)") }
                            }
                        }
                        if !memory.notes.isEmpty {
                            section("Session notes") {
                                ForEach(memory.notes) { note in
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(Date(timeIntervalSince1970: note.startedAt)
                                            .formatted(date: .abbreviated, time: .shortened))
                                            .font(.caption).foregroundStyle(.secondary)
                                        Text(note.note)
                                    }
                                }
                            }
                        }
                    } else if let error {
                        ErrorLine(text: error)
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }
                .textSelection(.enabled)
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(width: 540, height: 620)
        .task {
            do { memory = try await model.memory() } catch { self.error = error.localizedDescription }
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline).foregroundStyle(DonnaTheme.rose)
            content()
        }
    }
}
