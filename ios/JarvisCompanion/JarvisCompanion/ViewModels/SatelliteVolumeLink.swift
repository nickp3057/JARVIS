import AVFoundation
import MediaPlayer
import UIKit

/// One volume knob for the room while the app is open (a live voice
/// satellite, see SatelliteVoice): the iPad's own volume mirrors Core's room
/// level (0-10: the desk's), and pressing this iPad's volume buttons sets
/// the room's level, so the desk and every other iPad follow.
///
/// Core sends `{"type":"volume","level":n}` (and `level` in its hello)
/// whenever the room's level changes; this iPad sets its system volume to
/// n/10 through a hidden MPVolumeView (which also keeps the system volume
/// HUD away). A change Nick makes with the buttons goes back to Core the
/// same way. iOS's volume curve and ALSA's mapped curve are both close to
/// cubic, so level/10 on both sounds alike; Match the desk trims the rest.
@MainActor
final class SatelliteVolumeLink {
    static let shared = SatelliteVolumeLink()

    /// The room's level as Core last said it (nil before the first one).
    private(set) var level: Int?
    private var volumeView: MPVolumeView?
    private var observation: NSKeyValueObservation?
    private var onUserChange: ((Int) -> Void)?
    private var lastSet: Float = -1
    private var setAt = Date.distantPast

    func start(onUserChange: @escaping (Int) -> Void) {
        self.onUserChange = onUserChange
        attachVolumeView()
        guard observation == nil else { return }
        observation = AVAudioSession.sharedInstance().observe(\.outputVolume, options: [.new]) { [weak self] _, change in
            guard let value = change.newValue else { return }
            Task { @MainActor in self?.changed(value) }
        }
    }

    func stop() {
        observation = nil
        onUserChange = nil
        volumeView?.removeFromSuperview()
        volumeView = nil
    }

    /// Core's room level: set this iPad's volume to match.
    func apply(level: Int) {
        let clamped = max(0, min(10, level))
        self.level = clamped
        let target = Float(clamped) / 10
        guard abs(AVAudioSession.sharedInstance().outputVolume - target) > 0.01 else { return }
        lastSet = target
        setAt = Date()
        attachVolumeView()
        // The slider appears a beat after the view joins the window.
        Task { @MainActor in
            for _ in 0..<5 {
                if let slider = self.volumeView?.subviews.compactMap({ $0 as? UISlider }).first {
                    slider.value = target
                    return
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
    }

    /// The loudness factor for a clip Core sent at `gain`: the iPad's volume
    /// already carries the room's level, so only a clip louder or quieter
    /// than the room (the alarm, quiet hours) is scaled, relative to it.
    func clipVolume(gain: Double) -> Double {
        guard let level, level > 0 else { return gain }
        let room = pow(Double(level) / 10, 3)
        return min(1, gain / room)
    }

    private func changed(_ value: Float) {
        // Our own set (or its echo) isn't Nick pressing a button.
        if Date().timeIntervalSince(setAt) < 1.0, abs(value - lastSet) < 0.03 { return }
        let newLevel = Int((value * 10).rounded())
        guard newLevel != level else { return }
        level = newLevel
        onUserChange?(newLevel)
    }

    private func attachVolumeView() {
        guard volumeView?.window == nil else { return }
        let window = UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }.first
        guard let window else { return }
        let view = volumeView ?? MPVolumeView(frame: CGRect(x: -1000, y: -1000, width: 1, height: 1))
        view.alpha = 0.01
        view.isUserInteractionEnabled = false
        window.addSubview(view)
        volumeView = view
    }
}
