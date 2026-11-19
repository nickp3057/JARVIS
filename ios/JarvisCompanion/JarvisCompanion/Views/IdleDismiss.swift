import SwiftUI
import UIKit

/// Pop-ups on the room iPads (the TV remote, the Room page, a light's
/// sheet, settings, the wake-up schedule, the maps) close by themselves
/// after a while with nobody touching the screen, so the panel never sits
/// stuck on a pop-up (Nick, 2026-11-19). Room settings › Pop-ups close
/// sets how long (`room.popupTimeout`, seconds; 0 = never). Only on iPad:
/// the phone and the Mac keep their sheets until closed.
enum TouchActivity {
    /// When a finger last touched any of the app's windows.
    @MainActor static private(set) var lastTouch = Date()

    /// Watch every window for touches without taking any from the views.
    @MainActor static func install() {
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
            for window in scene.windows where !(window.gestureRecognizers ?? []).contains(where: { $0 is Recognizer }) {
                window.addGestureRecognizer(Recognizer())
            }
        }
    }

    @MainActor fileprivate static func touched() { lastTouch = Date() }

    /// Sees every touch begin and end, recognises nothing, cancels nothing.
    private final class Recognizer: UIGestureRecognizer, UIGestureRecognizerDelegate {
        init() {
            super.init(target: nil, action: nil)
            cancelsTouchesInView = false
            delaysTouchesBegan = false
            delaysTouchesEnded = false
            delegate = self
        }

        override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
            TouchActivity.touched()
            state = .failed
        }

        override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) { TouchActivity.touched() }
        override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) { TouchActivity.touched() }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
    }

    static let timeoutKey = "room.popupTimeout"
    static let defaultTimeout = 60
    /// Room settings' choices (seconds; 0 = never).
    static let choices: [(name: String, seconds: Int)] = [
        ("30 seconds", 30), ("1 minute", 60), ("2 minutes", 120), ("5 minutes", 300), ("Never", 0),
    ]
}

private struct IdleDismiss: ViewModifier {
    @AppStorage(TouchActivity.timeoutKey) private var timeout = TouchActivity.defaultTimeout
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    let onTimeout: (() -> Void)?

    func body(content: Content) -> some View {
        content.task(id: "\(timeout)-\(scenePhase == .active)") {
            guard timeout > 0, scenePhase == .active,
                  UIDevice.current.userInterfaceIdiom == .pad, !RootView.isMac else { return }
            TouchActivity.install()
            while !Task.isCancelled {
                let idle = Date().timeIntervalSince(TouchActivity.lastTouch)
                if idle >= Double(timeout) {
                    if let onTimeout { onTimeout() } else { dismiss() }
                    return
                }
                try? await Task.sleep(for: .seconds(max(1, Double(timeout) - idle)))
            }
        }
    }
}

extension View {
    /// Close this pop-up after `room.popupTimeout` seconds untouched (iPad
    /// only). In a sheet it dismisses itself; an overlay passes `onTimeout`.
    func idleDismiss(onTimeout: (() -> Void)? = nil) -> some View {
        modifier(IdleDismiss(onTimeout: onTimeout))
    }
}
