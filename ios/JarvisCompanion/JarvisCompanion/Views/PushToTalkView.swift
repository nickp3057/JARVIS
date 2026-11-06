import SwiftUI
import UIKit

struct PushToTalkOrb: View {
    @ObservedObject var controller: PushToTalkController
    var size: CGFloat

    var body: some View {
        JarvisCoreMark(size: size, active: controller.busy)
            .overlay {
                OrbHoldGesture(onBegin: controller.begin, onRelease: controller.release, onCancel: controller.cancel)
            }
            .barnDoorTaps()
            .accessibilityHidden(false)
            .accessibilityLabel("JARVIS")
            .accessibilityHint("Hold to talk; release to send. Slide away to cancel. Triple-tap for Barn Door Protocol.")
            .accessibilityAction(named: "Start talking") { controller.begin() }
            .accessibilityAction(named: "Send voice message") { controller.release() }
            .accessibilityAction(named: "Cancel voice") { controller.cancel() }
    }
}

struct PushToTalkPanel: View {
    @ObservedObject var controller: PushToTalkController

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(title, systemImage: controller.listening ? "mic.fill" : "waveform")
                    .font(.headline).foregroundStyle(JarvisTheme.cyan)
                Spacer()
                Button { controller.cancel() } label: { Image(systemName: "xmark.circle.fill").font(.title2) }
                    .accessibilityLabel(controller.busy ? "Stop voice" : "Close voice")
            }
            if !controller.transcript.isEmpty {
                Text(controller.transcript).font(.body).lineLimit(3)
            }
            if !controller.reply.isEmpty {
                Text(controller.reply).font(.body).foregroundStyle(JarvisTheme.cyan).lineLimit(4)
            }
            if controller.listening {
                Text(controller.acknowledged ? "HQ is receiving · release to send" : "Hold to speak · release to send")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !controller.detail.isEmpty {
                Text(controller.detail).font(.caption).foregroundStyle(JarvisTheme.amber)
            }
        }
        .padding(18)
        .background(.ultraThinMaterial, in: .rect(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).strokeBorder(JarvisTheme.border))
        .frame(maxWidth: 600)
        .padding(.horizontal, 16)
        .padding(.bottom, 96)
    }

    private var title: String {
        switch controller.phase {
        case "connecting": return "Getting ready…"
        case "listening": return "Listening…"
        case "finalizing": return "Finishing your words…"
        case "thinking": return "One moment…"
        case "speaking": return "JARVIS"
        case "error": return "Voice needs attention"
        default: return "Done"
        }
    }
}

/// A native long press supplies a real cancelled/ended distinction. It can
/// coexist with the orb's quick triple-tap without sending on a cancelled hold.
private struct OrbHoldGesture: UIViewRepresentable {
    var onBegin: () -> Void
    var onRelease: () -> Void
    var onCancel: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.backgroundColor = .clear
        view.isAccessibilityElement = false
        let hold = UILongPressGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.press(_:)))
        hold.minimumPressDuration = 0.3
        hold.allowableMovement = 40
        hold.delegate = context.coordinator
        view.addGestureRecognizer(hold)
        return view
    }
    func updateUIView(_ view: UIView, context: Context) { context.coordinator.parent = self }
    static func dismantleUIView(_ view: UIView, coordinator: Coordinator) { coordinator.finish(send: false) }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var parent: OrbHoldGesture
        private var held = false
        init(_ parent: OrbHoldGesture) { self.parent = parent }

        @objc func press(_ gesture: UILongPressGestureRecognizer) {
            switch gesture.state {
            case .began:
                held = true
                SwipeLock.hold()
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                parent.onBegin()
            case .changed:
                if let view = gesture.view, !view.bounds.insetBy(dx: -40, dy: -40).contains(gesture.location(in: view)) {
                    gesture.isEnabled = false
                    gesture.isEnabled = true
                }
            case .ended: finish(send: true)
            case .cancelled, .failed: finish(send: false)
            default: break
            }
        }

        func finish(send: Bool) {
            guard held else { return }
            held = false
            SwipeLock.release()
            if send { parent.onRelease() } else { parent.onCancel() }
        }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
    }
}
