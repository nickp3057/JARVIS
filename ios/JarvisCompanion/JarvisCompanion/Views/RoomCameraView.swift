import SwiftUI

/// The sentry view on Nick's phone. While it's open it heartbeats Core every
/// ~1.5 s (so the iPad keeps the lens on) and pulls the newest frame about 8
/// times a second. Closing it stops the heartbeat, and the lens goes off
/// within a few seconds. A Kill button (and "Jarvis, kill the camera")
/// locks the lens off for five minutes. Barn Door engages with a slide (so it
/// can't fire by accident) and stands down with a tap, without leaving the view.
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
            if barnDoorOn {
                Button { barnDoorOn = false; Task { await setBarnDoor(false) } } label: {
                    Label("Stand down Barn Door", systemImage: "checkmark.shield")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 52)
                        .background(.ultraThinMaterial, in: .rect(cornerRadius: 16))
                        .foregroundStyle(.white)
                }
            } else {
                SlideToEngage(title: "Slide for Barn Door") {
                    barnDoorOn = true
                    Task { await setBarnDoor(true) }
                }
            }

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

/// Drag the block all the way across to fire; let go early and it springs
/// back. VoiceOver gets a plain activate action.
struct SlideToEngage: View {
    let title: String
    let action: () -> Void
    @State private var offset: CGFloat = 0
    private let knob: CGFloat = 52
    private let red = Color(red: 1.0, green: 0.18, blue: 0.16)

    var body: some View {
        GeometryReader { geo in
            let travel = max(1, geo.size.width - knob - 8)
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 16).fill(.ultraThinMaterial)
                RoundedRectangle(cornerRadius: 16).fill(red.opacity(0.35 * offset / travel))
                Text(title)
                    .font(.headline).foregroundStyle(.white.opacity(1 - offset / travel))
                    .frame(maxWidth: .infinity)
                RoundedRectangle(cornerRadius: 12)
                    .fill(red)
                    .frame(width: knob, height: knob - 8)
                    .overlay(Image(systemName: "exclamationmark.octagon.fill").foregroundStyle(.white))
                    .offset(x: 4 + offset)
                    .gesture(DragGesture()
                        .onChanged { offset = min(max(0, $0.translation.width), travel) }
                        .onEnded { _ in
                            if offset >= travel * 0.95 { action() }
                            withAnimation(.spring(duration: 0.3)) { offset = 0 }
                        })
            }
        }
        .frame(height: knob)
        .sensoryFeedback(.impact(weight: .heavy), trigger: offset == 0)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { action() }
    }
}
