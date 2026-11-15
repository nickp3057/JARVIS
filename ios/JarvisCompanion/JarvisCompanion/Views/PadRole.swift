import SwiftUI
import UIKit

/// Which job this iPad does in Nick's room. The same app runs on both:
///
/// - **Nightstand** (the iPad mini by the bed): the clock and alarm, the
///   room controls, the movie remote (movie mode lives here now) and the
///   camera's `bed` angle.
/// - **Door** (the iPad Air by the door): the room panel and the camera's
///   `door` angle. When the Movie scene is on it stays the normal panel,
///   just dimmed, instead of turning into the movie remote.
///
/// "Automatic" picks by screen size (a mini's short side is 744 pt, an
/// Air's 820 or more); Room settings can pin it either way.
enum PadRole: String, CaseIterable, Identifiable {
    case nightstand, door

    var id: String { rawValue }

    static let storageKey = "room.padRole"

    /// What Room settings shows.
    var title: String { self == .nightstand ? "Nightstand" : "Door" }

    /// The camera angle this iPad uploads as (Core's `brain/camera.py`).
    var lens: String { self == .nightstand ? "bed" : "door" }

    /// The name Core's battery warning uses.
    var deviceName: String { self == .nightstand ? "Nightstand iPad" : "Door iPad" }

    /// Movie mode (the dark movie remote) runs on the nightstand iPad only.
    var hostsMovieMode: Bool { self == .nightstand }

    /// The role from the screen alone.
    @MainActor
    static var automatic: PadRole {
        let bounds = (UIApplication.shared.connectedScenes.first as? UIWindowScene)?.screen.bounds
            ?? CGRect(x: 0, y: 0, width: 820, height: 1180)
        return min(bounds.width, bounds.height) < 800 ? .nightstand : .door
    }

    /// The stored choice ("" or missing = automatic).
    @MainActor
    static var current: PadRole { resolve(UserDefaults.standard.string(forKey: storageKey) ?? "") }

    @MainActor
    static func resolve(_ stored: String) -> PadRole { PadRole(rawValue: stored) ?? automatic }
}
