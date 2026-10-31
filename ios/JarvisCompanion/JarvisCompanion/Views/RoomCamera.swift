import AVFoundation
import SwiftUI
import UIKit

/// The iPad as the room's camera. It polls Core every ~1.5 s: while Core says
/// the lens is `wanted` (Nick's phone is watching and there's no kill
/// lockout) it runs the front camera and uploads a downscaled JPEG a few
/// times a second; otherwise the session is off and nothing is captured.
/// Frames never touch disk. While capturing, `watching` is true and the app
/// shows the green JARVIS orb (`CameraWatchOrb`) so the room can see it's on.
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

    /// Start following Core's camera status (iPad only; the phone never
    /// captures). Safe to call repeatedly.
    func activate(settings: AppSettings) {
        self.settings = settings
        guard RootView.isPad, !RootView.isMac, poll == nil, settings.isConfigured else { return }
        poll = Task { [weak self] in
            while !Task.isCancelled {
                await self?.tick()
                try? await Task.sleep(for: .seconds(1.5))
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
        let wanted = (try? await JarvisAPIClient.make(from: settings).cameraStatus().wanted) ?? false
        if wanted { await start() } else { stop() }
    }

    private func start() async {
        guard !running else { return }
        guard await ensureAuthorized() else { return }
        running = true
        watching = true
        queue.async { [weak self] in self?.configureAndRun() }
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
            if let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front),
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
            do { try await JarvisAPIClient.make(from: settings).putCameraFrame(jpeg) }
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

/// The green "Nick is watching" orb: the TV's red-alert reactor shape in
/// green, less intimidating. Shown over the whole app on the iPad whenever
/// the room camera is capturing, so anyone in the room knows.
struct CameraWatchOrb: View {
    var size: CGFloat = 132

    var body: some View {
        VStack(spacing: 10) {
            RedAlertMark(size: size, moving: true,
                         base: Color(red: 0.20, green: 0.85, blue: 0.45),
                         hot: Color(red: 0.55, green: 1.0, blue: 0.70))
            Text("JARVIS is watching")
                .font(.headline)
                .foregroundStyle(Color(red: 0.55, green: 1.0, blue: 0.70))
        }
        .padding(28)
        .background(.black.opacity(0.75), in: .rect(cornerRadius: 28))
        .shadow(color: .black.opacity(0.5), radius: 24)
        .accessibilityLabel("JARVIS is watching the room on camera")
    }
}
