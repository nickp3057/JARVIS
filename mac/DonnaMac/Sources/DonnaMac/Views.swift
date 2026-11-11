import AppKit
import SwiftUI

/// The phone's Donna palette: a soft, warm rose. Calm on purpose, nothing pulses.
enum DonnaTheme {
    private static func adaptive(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(NSColor(name: nil) { appearance in
            let dark_ = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            let hex = dark_ ? dark : light
            return NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                           blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
        })
    }
    static let backgroundTop = adaptive(0xFDF1F4, 0x2A141F)
    static let backgroundBottom = adaptive(0xF6DCE4, 0x1A0C13)
    static let rose = adaptive(0xC9506F, 0xF08AA6)
    static let bubble = adaptive(0xD9637F, 0x8E3653)
    static let card = adaptive(0xFFFFFF, 0x3A1D2B)
    static let border = adaptive(0xEBC3CF, 0x553040)
}

struct DonnaWindow: View {
    @EnvironmentObject private var settings: MacSettings
    @StateObject private var model: DonnaModel
    @FocusState private var composing: Bool
    @State private var showingMemory = false
    @State private var confirmingEnd = false
    @Environment(\.openSettings) private var openSettings

    init(settings: MacSettings) {
        _model = StateObject(wrappedValue: DonnaModel(settings: settings))
    }

    var body: some View {
        VStack(spacing: 0) {
            transcript
            composer
        }
        .background {
            LinearGradient(colors: [DonnaTheme.backgroundTop, DonnaTheme.backgroundBottom],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
        }
        .tint(DonnaTheme.rose)
        .toolbar(removing: .title)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("Donna")
                    .font(.system(.title3, design: .serif).weight(.semibold))
                    .foregroundStyle(DonnaTheme.rose)
            }
            ToolbarItemGroup(placement: .primaryAction) {
                Button { Task { await model.load() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    .help("Pick up anything said on the phone")
                    .keyboardShortcut("r")
                Button { showingMemory = true } label: { Label("What Donna knows", systemImage: "book.closed") }
                    .help("What Donna knows")
                Button { confirmingEnd = true } label: { Label("End session", systemImage: "checkmark.circle") }
                    .help("End session — Donna writes her note")
                    .disabled(model.messages.isEmpty || model.thinking)
            }
        }
        .confirmationDialog("End this session?", isPresented: $confirmingEnd) {
            Button("End session") { Task { await model.endSession() } }
        } message: {
            Text("Donna writes her note now; your next message starts a fresh session.")
        }
        .sheet(isPresented: $showingMemory) { MemoryView(model: model) }
        .task(id: settings.isConfigured) {
            if settings.isConfigured { await model.load() }
            composing = true
        }
        // Catch up on anything said on the phone whenever the window comes back.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.load() }
        }
    }

    // MARK: Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    if !settings.isConfigured {
                        setupPrompt
                    } else if model.messages.isEmpty && !model.loading {
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
                    if let error = model.error, settings.isConfigured {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.callout).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
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
        .padding(.top, 80)
    }

    private var setupPrompt: some View {
        VStack(spacing: 12) {
            Text("Connect to JARVIS Core")
                .font(.system(.title2, design: .serif).weight(.semibold))
            Text("Donna lives on Core. Add its address and the companion's API token.")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Open Settings…") { openSettings() }
                .buttonStyle(.borderedProminent)
        }
        .padding(.top, 80)
    }

    private func bubble(_ message: DonnaMessage) -> some View {
        HStack {
            if message.fromNick { Spacer(minLength: 80) }
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
            if !message.fromNick { Spacer(minLength: 60) }
        }
        .font(.system(size: 14))
    }

    // MARK: Composer — Return sends, Shift-Return starts a new line.

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Talk to Donna", text: $model.draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .lineLimit(1...10)
                .focused($composing)
                .onKeyPress(.return, phases: .down) { press in
                    if press.modifiers.contains(.shift) || press.modifiers.contains(.option) {
                        model.draft += "\n"
                    } else {
                        model.send()
                    }
                    return .handled
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(DonnaTheme.card, in: .rect(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(DonnaTheme.border))
            Button { model.send() } label: {
                Image(systemName: "arrow.up")
                    .font(.body.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    .background(DonnaTheme.rose, in: .circle)
            }
            .buttonStyle(.plain)
            .disabled(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.thinking)
            .opacity(model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.thinking ? 0.5 : 1)
            .help("Send (Return)")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .disabled(!settings.isConfigured)
    }
}

// MARK: - What Donna knows

struct MemoryView: View {
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
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.secondary)
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }
                .textSelection(.enabled)
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(width: 520, height: 600)
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

// MARK: - Settings

struct SettingsView: View {
    @EnvironmentObject private var settings: MacSettings
    @State private var address = ""
    @State private var token = ""
    @State private var status: String?

    var body: some View {
        Form {
            TextField("Core address", text: $address, prompt: Text("10.0.0.73 or a Tailscale address"))
            SecureField("API token", text: $token, prompt: Text("JARVIS_API_TOKEN"))
            HStack {
                if let status { Text(status).font(.callout).foregroundStyle(.secondary) }
                Spacer()
                Button("Save") { save() }.keyboardShortcut(.defaultAction)
            }
        }
        .formStyle(.grouped)
        .frame(width: 440)
        .onAppear {
            address = settings.address
            token = settings.token
        }
    }

    private func save() {
        settings.address = address
        settings.token = token
        guard let url = settings.baseURL else {
            status = "That address doesn't look right."
            return
        }
        status = "Checking \(url.host() ?? "")…"
        Task {
            do {
                _ = try await DonnaAPI.make(settings).state()
                status = "Connected."
            } catch {
                status = error.localizedDescription
            }
        }
    }
}
