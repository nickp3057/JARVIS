import SwiftUI
import UIKit

/// Follows the room voice on the Optiplex ("Hey JARVIS") and, on the iPad,
/// shows it above everything: JARVIS listening, what you're saying as you
/// say it, then the answer (or a tick for a quick home command).
///
/// The overlay lives in its own window above the app's, so it covers sheets,
/// the night screen and anything else that's open.
@MainActor
final class VoicePresence: ObservableObject {
    static let shared = VoicePresence()

    @Published private(set) var state = VoiceState.idle
    private var task: Task<Void, Never>?
    private var window: UIWindow?
    private var hideTask: Task<Void, Never>?

    var active: Bool { state.phase != "idle" }

    /// Connect while the app is in front (iPad only); reconnects on its own.
    func start(_ settings: AppSettings) {
        guard UIDevice.current.userInterfaceIdiom == .pad, task == nil, settings.isConfigured else { return }
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
        state = new
        if new.phase == "idle" {
            // Let a reply or the tick stay readable for a moment.
            hideTask?.cancel()
            hideTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(2.5))
                guard !Task.isCancelled, self?.active == false else { return }
                self?.window?.isHidden = true
            }
        } else {
            hideTask?.cancel()
            showWindow()
        }
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

    /// Tap to put it away early (the voice carries on regardless).
    func dismiss() {
        window?.isHidden = true
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
        .onTapGesture { presence.dismiss() }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
    }

    private var title: String {
        switch state.phase {
        case "listening": return "Listening…"
        case "thinking": return "One moment…"
        case "speaking": return "JARVIS"
        case "done": return "Done"
        case "error": return "That didn't work"
        default: return ""
        }
    }
}
