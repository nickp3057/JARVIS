import SwiftUI

/// The sentry view on Nick's phone. While it's open it heartbeats Core every
/// ~1.5 s (so the iPad keeps the lens on) and pulls the newest frame about 8
/// times a second. Closing it stops the heartbeat, and the lens goes off
/// within a few seconds. A Kill button (and "Jarvis, kill the camera")
/// locks the lens off for five minutes. A Barn Door toggle engages or stands
/// down the protocol without leaving the view.
struct RoomCameraView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @State private var frame: UIImage?
    @State private var status: CameraStatus?
    @State private var barnDoorOn = JarvisPalette.current == .barnDoor
    @State private var message: String?

    private var client: JarvisAPIClient? { try? JarvisAPIClient.make(from: settings) }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if let frame {
                Image(uiImage: frame)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .ignoresSafeArea()
            } else {
                VStack(spacing: 14) {
                    ProgressView().tint(.white)
                    Text(waitingText).font(.subheadline).foregroundStyle(.white.opacity(0.7))
                }
            }

            VStack {
                HStack {
                    Button { dismiss() } label: {
                        Label("Close", systemImage: "xmark")
                            .font(.headline).padding(.horizontal, 16).padding(.vertical, 10)
                            .background(.ultraThinMaterial, in: .capsule)
                    }
                    Spacer()
                    if status?.viewing == true {
                        Label("LIVE", systemImage: "dot.radiowaves.left.and.right")
                            .font(.caption.weight(.bold))
                            .padding(.horizontal, 12).padding(.vertical, 8)
                            .background(Color(red: 0.20, green: 0.85, blue: 0.45).opacity(0.9), in: .capsule)
                            .foregroundStyle(.black)
                    }
                }
                Spacer()
                controls
            }
            .padding(20)
            .tint(.white)
        }
        .task { await drive() }
    }

    private var waitingText: String {
        if let left = status?.killSecondsLeft, status?.killed == true, left > 0 {
            return "Camera locked off for \(left / 60 + 1) more min"
        }
        return "Waking the camera…"
    }

    private var controls: some View {
        VStack(spacing: 14) {
            Toggle(isOn: $barnDoorOn) {
                Label("Barn Door Protocol", systemImage: "exclamationmark.octagon.fill")
                    .font(.headline).foregroundStyle(.white)
            }
            .tint(Color(red: 1.0, green: 0.18, blue: 0.16))
            .padding(.horizontal, 18).padding(.vertical, 12)
            .background(.ultraThinMaterial, in: .rect(cornerRadius: 16))
            .onChange(of: barnDoorOn) { _, on in Task { await setBarnDoor(on) } }

            Button(role: .destructive) { Task { await kill() } } label: {
                Label("Kill camera (5 min)", systemImage: "video.slash.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .background(.red.opacity(0.85), in: .rect(cornerRadius: 16))
                    .foregroundStyle(.white)
            }
            if let message {
                Text(message).font(.footnote).foregroundStyle(.white.opacity(0.75))
            }
        }
    }

    /// Heartbeat + frame pull, until the view closes.
    private func drive() async {
        guard let client else { return }
        var lastBeat = Date.distantPast
        while !Task.isCancelled {
            let now = Date()
            if now.timeIntervalSince(lastBeat) > 1.5 {
                lastBeat = now
                status = try? await client.cameraHeartbeat()
            }
            if status?.killed == true {
                frame = nil
            } else if let data = try? await client.cameraFrame(), let image = UIImage(data: data) {
                frame = image
            }
            try? await Task.sleep(for: .milliseconds(120))
        }
    }

    private func kill() async {
        status = try? await client?.cameraKill()
        frame = nil
        message = "Camera off for 5 minutes. Ask JARVIS why it was on."
    }

    private func setBarnDoor(_ on: Bool) async {
        _ = try? await client?.barnDoor(on ? "engage" : "stand_down")
        await ThemeSync.pull(settings)
    }
}
