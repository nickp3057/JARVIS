import SwiftUI
import UIKit

/// Follows the room voice on the Optiplex ("Hey JARVIS") and, on the iPad,
/// shows it above everything: JARVIS listening, what you're saying as you
/// say it, then the answer (or a tick for a quick home command).
///
/// On the Room tab the conversation shows in place (`RoomView`): the side
/// reactor grows and glows and the lights column turns into the dialogue, so
/// there's never a second orb on top of the first. Anywhere else (another
/// tab, a sheet, movie mode) it's an overlay in its own window above the
/// app's, covering whatever's open.
@MainActor
final class VoicePresence: ObservableObject {
    static let shared = VoicePresence()

    @Published private(set) var state = VoiceState.idle
    /// Something to show: the voice is busy, or its last words are lingering
    /// so they can be read.
    @Published private(set) var showing = false
    /// The Room tab is on screen and uncovered: it shows the conversation
    /// itself, so the overlay window stays hidden.
    private(set) var inlineHost = false
    private var task: Task<Void, Never>?
    private var window: UIWindow?
    private var hideTask: Task<Void, Never>?
    private var settings: AppSettings?

    var active: Bool { state.phase != "idle" }

    func setInlineHost(_ inline: Bool) {
        guard inline != inlineHost else { return }
        inlineHost = inline
        if inline {
            window?.isHidden = true
        } else if showing {
            showWindow()
        }
    }

    /// Connect while the app is in front (iPad only); reconnects on its own.
    func start(_ settings: AppSettings) {
        guard UIDevice.current.userInterfaceIdiom == .pad, task == nil, settings.isConfigured else { return }
        self.settings = settings
        task = Task { [weak self] in
            var delay: Double = 1
            while !Task.isCancelled {
                do {
                    let client = try JarvisAPIClient.make(from: settings)
                    try await client.streamVoice { state in self?.apply(state) }
                    delay = 1
                } catch {
                    // Core restarting, or an older Core without /api/voice: back off.
                    delay = min(delay * 2, 30)
                }
                self?.apply(.idle)
                try? await Task.sleep(for: .seconds(delay))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
        apply(.idle)
    }

    private func apply(_ new: VoiceState) {
        let was = state
        state = new
        if new.phase == "idle" {
            guard showing else { return }
            // Let a reply or the tick stay readable for a moment (longer for
            // more words).
            let linger = new.reply.isEmpty ? 2.5 : min(8, 3 + Double(new.reply.count) / 40)
            hideTask?.cancel()
            hideTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(linger))
                guard !Task.isCancelled, let self, !self.active else { return }
                self.hide()
            }
        } else {
            hideTask?.cancel()
            if was != new || !showing { showing = true }
            if !inlineHost { showWindow() }
        }
    }

    private func hide() {
        showing = false
        window?.isHidden = true
    }

    private func showWindow() {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        // Prefer the active scene; a system alert leaves it merely "inactive".
        if window == nil,
           let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first {
            let window = UIWindow(windowScene: scene)
            window.windowLevel = .alert + 1
            let host = UIHostingController(rootView: VoiceOverlayView(presence: self))
            host.view.backgroundColor = .clear
            window.rootViewController = host
            self.window = window
        }
        window?.isHidden = false
    }

    /// A tap on the conversation: if he's still listening, thinking or
    /// talking, Core ends the exchange (`POST /api/voice/hush`: he stops
    /// mid-sentence, drops a reply still being written and waits for "Hey
    /// JARVIS" again), so a room full of chatter can't keep him going.
    /// Either way the words go away.
    func hush() {
        if active, let settings {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            Task {
                // Best effort: an older Core without /api/voice/hush just keeps talking.
                try? await JarvisAPIClient.make(from: settings).hushVoice()
            }
        }
        dismiss()
    }

    /// Put the words away early (the voice carries on regardless).
    func dismiss() {
        hideTask?.cancel()
        hide()
    }
}

struct VoiceOverlayView: View {
    @ObservedObject var presence: VoicePresence

    private var state: VoiceState { presence.state }

    var body: some View {
        ZStack {
            Color.black.opacity(0.62).ignoresSafeArea()
            VStack(spacing: 26) {
                ZStack {
                    JarvisCoreMark(size: 132, animated: state.phase == "listening" || state.phase == "thinking",
                                   active: state.phase != "error")
                    if state.phase == "done" {
                        Image(systemName: "checkmark")
                            .font(.system(size: 44, weight: .bold))
                            .foregroundStyle(JarvisTheme.success)
                    }
                }
                Text(title)
                    .font(.headline)
                    .foregroundStyle(state.phase == "error" ? JarvisTheme.amber : JarvisTheme.cyan)
                if !state.transcript.isEmpty {
                    Text(state.transcript)
                        .font(.system(size: 34, weight: .medium, design: .rounded))
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.white)
                        .lineLimit(4)
                        .animation(nil, value: state.transcript)
                }
                if !state.reply.isEmpty {
                    Text(state.reply)
                        .font(.title3)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.white.opacity(0.8))
                        .lineLimit(6)
                }
            }
            .padding(40)
            .frame(maxWidth: 640)
        }
        .contentShape(Rectangle())
        .onTapGesture { presence.hush() }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
    }

    private var title: String { Self.title(for: state.phase) }

    static func title(for phase: String) -> String {
        switch phase {
        case "listening": return "Listening…"
        case "thinking": return "One moment…"
        case "speaking": return "JARVIS"
        case "done": return "Done"
        case "error": return "That didn't work"
        default: return ""
        }
    }
}

/// The Room tab's conversation, in place of the lights and scenes: what Nick
/// said, smaller, then JARVIS's answer big enough to read across the room.
/// An announcement (nothing said to him first) is just its words. Tap to
/// stop him and put it away (`VoicePresence.hush`).
struct RoomDialogue: View {
    @ObservedObject var presence: VoicePresence

    private var state: VoiceState { presence.state }

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(spacing: 10) {
                if state.phase == "done" {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(JarvisTheme.success)
                }
                Text(VoiceOverlayView.title(for: state.phase).isEmpty ? "JARVIS" : VoiceOverlayView.title(for: state.phase))
                    .font(.headline)
                    .foregroundStyle(state.phase == "error" ? JarvisTheme.amber : JarvisTheme.cyan)
            }
            if !state.transcript.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("You").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Text(state.transcript)
                        .font(.system(size: state.reply.isEmpty ? 38 : 26, weight: .medium, design: .rounded))
                        .foregroundStyle(.white.opacity(state.reply.isEmpty ? 1 : 0.7))
                        .lineLimit(4)
                        .animation(nil, value: state.transcript)
                }
            }
            if !state.reply.isEmpty {
                Text(state.reply)
                    .font(.system(size: 42, weight: .regular, design: .rounded))
                    .foregroundStyle(.white)
                    .lineLimit(8)
                    .minimumScaleFactor(0.5)
            }
            Spacer(minLength: 0)
        }
        .multilineTextAlignment(.leading)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .contentShape(Rectangle())
        .onTapGesture { presence.hush() }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
        .accessibilityHint("Tap to stop JARVIS and put it away")
    }
}
