import AVFoundation
import SwiftUI
import UIKit

/// Each iPad as one of the room's two camera angles (its `PadRole.lens`:
/// `door` for the Air, `bed` for the nightstand mini; Nick's phone shows
/// both). While Core says the lens is `wanted`
/// (Nick's phone is watching and there's no kill lockout) it runs the front
/// camera and uploads a downscaled JPEG a few times a second; otherwise the
/// session is off and nothing is captured. Core pushes `wanted` on the home
/// stream (`follow`), so the lens starts and stops within a beat; the poll
/// behind it is every 20 s while the stream is up and the lens is off, and
/// every ~1.5 s (as it always was) while capturing, with the stream down, or
/// against a Core that doesn't push it.
/// Frames never touch disk. While capturing, `watching` is true and the app
/// draws its reactor as the green JARVIS orb (`CameraWatchOrb`) so the room
/// can see it's on.
/// iOS's own green dot is on too. Camera needs the foreground, which the
/// wall-panel iPad always is.
@MainActor
final class RoomCameraController: NSObject, ObservableObject {
    @Published private(set) var watching = false

    private var settings: AppSettings?
    private let session = AVCaptureSession()
    private let output = AVCaptureVideoDataOutput()
    private let queue = DispatchQueue(label: "room-camera")
    private var poll: Task<Void, Never>?
    private var running = false
    private var uploading = false
    private var lastUpload = Date.distantPast
    private var authorized = false
    /// Last battery reading sent to Core (it only changes in 5% steps).
    private var reportedBattery: (Float, Bool)?
    /// What Core's home stream last said about the lens; nil = not telling us.
    private var pushed: Bool?
    /// Keeps the frames upright whichever way the iPad stands (the mini
    /// sideways the other way round from the Air came out upside down).
    private var rotation: AVCaptureDevice.RotationCoordinator?
    private var rotationWatch: NSKeyValueObservation?

    /// Seconds until the next status check: slow only while the stream is
    /// reporting and the lens is off, so a stop is never left to a slow poll.
    static func pollSeconds(pushed: Bool?, running: Bool) -> Double {
        pushed == false && !running ? 20 : 1.5
    }

    /// The home stream's word on the lens (`HubViewModel.cameraWanted`).
    /// A change is checked with Core at once rather than trusted blindly:
    /// `tick` asks /api/camera/status, which stays the only thing that
    /// starts or stops the capture.
    func follow(_ wanted: Bool?) {
        guard wanted != pushed else { return }
        pushed = wanted
        guard poll != nil, wanted != nil else { return }
        Task { [weak self] in await self?.tick() }
    }

    /// Start following Core's camera status (iPad only; the phone never
    /// captures). Safe to call repeatedly.
    func activate(settings: AppSettings) {
        self.settings = settings
        guard RootView.isPad, !RootView.isMac, poll == nil, settings.isConfigured else { return }
        poll = Task { [weak self] in
            while !Task.isCancelled {
                await self?.tick()
                let wait = Self.pollSeconds(pushed: self?.pushed, running: self?.running ?? false)
                try? await Task.sleep(for: .seconds(wait))
            }
        }
    }

    func deactivate() {
        poll?.cancel()
        poll = nil
        stop()
    }

    private func tick() async {
        guard let settings else { return }
        let client = try? JarvisAPIClient.make(from: settings)
        let wanted = (try? await client?.cameraStatus().wanted) ?? false
        if wanted { await start() } else { stop() }
        await reportBatteryIfChanged(client)
    }

    /// Send the battery to Core when it changes; Core warns the phone below 20%.
    private func reportBatteryIfChanged(_ client: JarvisAPIClient?) async {
        let device = UIDevice.current
        device.isBatteryMonitoringEnabled = true
        let level = device.batteryLevel
        let charging = device.batteryState == .charging || device.batteryState == .full
        guard level >= 0, let client, reportedBattery.map({ $0 != (level, charging) }) ?? true else { return }
        if (try? await client.reportBattery(device: PadRole.current.deviceName, level: level,
                                            charging: charging)) != nil {
            reportedBattery = (level, charging)
        }
    }

    private func start() async {
        guard !running else { return }
        guard await ensureAuthorized() else { return }
        running = true
        watching = true
        if rotation == nil, let device = Self.frontCamera() {
            let coordinator = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: nil)
            rotation = coordinator
            rotationWatch = coordinator.observe(\.videoRotationAngleForHorizonLevelCapture, options: [.new]) { [weak self] c, _ in
                let angle = c.videoRotationAngleForHorizonLevelCapture
                self?.queue.async { self?.applyRotation(angle) }
            }
        }
        let angle = rotation?.videoRotationAngleForHorizonLevelCapture
        queue.async { [weak self] in
            self?.configureAndRun()
            if let angle { self?.applyRotation(angle) }
        }
    }

    private nonisolated static func frontCamera() -> AVCaptureDevice? {
        AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front)
    }

    /// On `queue`: turn the frames to match how the iPad is standing.
    private nonisolated func applyRotation(_ angle: CGFloat) {
        guard let connection = output.connection(with: .video),
              connection.isVideoRotationAngleSupported(angle) else { return }
        connection.videoRotationAngle = angle
    }

    private func stop() {
        guard running else { return }
        running = false
        watching = false
        queue.async { [weak self] in
            guard let self else { return }
            if self.session.isRunning { self.session.stopRunning() }
        }
    }

    private func ensureAuthorized() async -> Bool {
        if authorized { return true }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: authorized = true
        case .notDetermined: authorized = await AVCaptureDevice.requestAccess(for: .video)
        default: authorized = false
        }
        return authorized
    }

    private nonisolated func configureAndRun() {
        // Called on `queue`. Build the session once, then just start it.
        if session.inputs.isEmpty {
            session.beginConfiguration()
            session.sessionPreset = .vga640x480  // small: it's a sentry view, not a recording
            if let device = Self.frontCamera(),
               let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) {
                session.addInput(input)
            }
            output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
            output.alwaysDiscardsLateVideoFrames = true
            output.setSampleBufferDelegate(self, queue: queue)
            if session.canAddOutput(output) { session.addOutput(output) }
            session.commitConfiguration()
        }
        if !session.isRunning { session.startRunning() }
    }
}

extension RoomCameraController: AVCaptureVideoDataOutputSampleBufferDelegate {
    nonisolated func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                                   from connection: AVCaptureConnection) {
        // Throttle to ~6 fps and one upload in flight, so a slow link can't pile up.
        let now = Date()
        Task { @MainActor [weak self] in
            guard let self, self.running, !self.uploading,
                  now.timeIntervalSince(self.lastUpload) > 0.16 else { return }
            self.lastUpload = now
            guard let jpeg = Self.jpeg(from: sampleBuffer) else { return }
            self.uploading = true
            defer { self.uploading = false }
            // A kill mid-stream returns 409: stop at once.
            guard let settings = self.settings else { return }
            do { try await JarvisAPIClient.make(from: settings).putCameraFrame(jpeg, lens: PadRole.current.lens) }
            catch { self.stop() }
        }
    }

    private nonisolated static func jpeg(from sampleBuffer: CMSampleBuffer) -> Data? {
        guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return nil }
        let ci = CIImage(cvPixelBuffer: pixels)
        let context = CIContext()
        guard let cg = context.createCGImage(ci, from: ci.extent) else { return nil }
        return UIImage(cgImage: cg).jpegData(compressionQuality: 0.4)
    }
}

/// Opens the phone's camera viewer from `RootView`, outside the
/// palette-keyed tab tree, so the cover survives a theme change.
@MainActor
final class RoomCameraPresenter: ObservableObject {
    static let shared = RoomCameraPresenter()
    @Published var showing = false
}

private struct CameraWatchingKey: EnvironmentKey { static let defaultValue = false }

extension EnvironmentValues {
    /// True on the iPad while its lens is capturing: `JarvisCoreMark` then
    /// draws `CameraWatchOrb` in place of the blue reactor.
    var cameraWatching: Bool {
        get { self[CameraWatchingKey.self] }
        set { self[CameraWatchingKey.self] = newValue }
    }
}

/// The green "Nick is watching" orb: the TV's red-alert reactor shape in
/// green, less intimidating. `JarvisCoreMark` draws it in place of the blue
/// reactor on the iPad whenever the room camera is capturing.
struct CameraWatchOrb: View {
    var size: CGFloat = 132
    var moving = true

    var body: some View {
        RedAlertMark(size: size, moving: moving,
                     base: Color(red: 0.20, green: 0.85, blue: 0.45),
                     hot: Color(red: 0.55, green: 1.0, blue: 0.70))
            .accessibilityLabel("JARVIS is watching the room on camera")
    }
}
