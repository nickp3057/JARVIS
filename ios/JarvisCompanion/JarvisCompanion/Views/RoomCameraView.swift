import SwiftUI

/// The sentry view on Nick's phone. While it's open it heartbeats Core every
/// ~1.5 s (so the iPads keep their lenses on) and pulls each angle's newest
/// frame about 8 times a second. Two angles: the door iPad (`door`) and the
/// nightstand mini (`bed`), side by side in landscape, stacked in portrait;
/// tap one (or pick it) to show it alone, centred; tap again (or Both) to go
/// back. The controls sit under the pictures, never over them.
/// Closing it stops the heartbeat, and the lenses go off within a few
/// seconds. A Kill button (and "Jarvis, kill the camera") locks every lens
/// off for five minutes. Barn Door engages with a slide (so it can't fire by
/// accident) and stands down with a tap, without leaving the view.
struct RoomCameraView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    /// Newest frame per angle ("door", "bed").
    @State private var frames: [String: UIImage] = [:]
    @State private var status: CameraStatus?
    /// nil = every angle; else just that one, full screen.
    @State private var solo: String?
    @State private var barnDoorOn = JarvisPalette.current == .barnDoor
    @State private var message: String?

    private var client: JarvisAPIClient? { try? JarvisAPIClient.make(from: settings) }

    /// The angles to draw: Core's live list (door first), or the door alone
    /// against a Core that doesn't name them.
    private var angles: [String] {
        let live = status?.lenses ?? (frames.isEmpty ? [] : ["door"])
        if let solo, live.contains(solo) { return [solo] }
        return live
    }

    /// Close and the angle picker on top, the feeds in the middle, Barn Door
    /// and Kill underneath: nothing sits on top of a picture any more.
    var body: some View {
        GeometryReader { screen in
            let wide = screen.size.width > screen.size.height
            VStack(spacing: 12) {
                topBar
                feeds(wide: wide)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipShape(.rect(cornerRadius: 12))
                controls(wide: wide)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .tint(.white)
        }
        .background(Color.black.ignoresSafeArea())
        .task { await drive() }
        // Closing the view tells Core at once, so the iPads' orbs go out
        // within a frame instead of after the heartbeat lapses.
        .onDisappear { let c = client; Task { _ = try? await c?.cameraLeave() } }
    }

    @ViewBuilder
    private func feeds(wide: Bool) -> some View {
        if angles.isEmpty || status?.killed == true {
            VStack(spacing: 14) {
                ProgressView().tint(.white)
                Text(waitingText).font(.subheadline).foregroundStyle(.white.opacity(0.7))
            }
        } else {
            let layout = wide ? AnyLayout(HStackLayout(spacing: 4)) : AnyLayout(VStackLayout(spacing: 4))
            layout {
                ForEach(angles, id: \.self) { angle in
                    angleView(angle, labelled: (status?.lenses?.count ?? 1) > 1)
                }
            }
        }
    }

    private var topBar: some View {
                HStack {
                    Button { dismiss() } label: {
                        Label("Close", systemImage: "xmark")
                            .labelStyle(.iconOnly)
                            .font(.headline).padding(.horizontal, 14).padding(.vertical, 10)
                            .background(.ultraThinMaterial, in: .capsule)
                    }
                    Spacer()
                    if (status?.lenses?.count ?? 0) > 1 { anglePicker }
                    Spacer()
                    if status?.viewing == true {
                        Label("LIVE", systemImage: "dot.radiowaves.left.and.right")
                            .font(.caption.weight(.bold))
                            .padding(.horizontal, 12).padding(.vertical, 8)
                            .background(Color(red: 0.20, green: 0.85, blue: 0.45).opacity(0.9), in: .capsule)
                            .foregroundStyle(.black)
                    }
                }
    }

    private static func title(_ lens: String) -> String {
        switch lens {
        case "door": return "Door"
        case "bed": return "Bed"
        default: return lens.capitalized
        }
    }

    /// Both / Door / Bed.
    private var anglePicker: some View {
        HStack(spacing: 2) {
            ForEach([nil] + (status?.lenses ?? []).map(Optional.some), id: \.self) { lens in
                Button { withAnimation(.easeInOut(duration: 0.25)) { solo = lens } } label: {
                    Text(lens.map(Self.title) ?? "Both")
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                        .fixedSize()  // "Both" was wrapping to "Bot / h" beside Close and LIVE
                        .padding(.horizontal, 10).padding(.vertical, 8)
                        .background(solo == lens ? Color.white.opacity(0.25) : .clear, in: .capsule)
                }
            }
        }
        .padding(3)
        .background(.ultraThinMaterial, in: .capsule)
    }

    /// One angle, fitted into its half (or the whole screen when it's solo).
    private func angleView(_ lens: String, labelled: Bool) -> some View {
        ZStack {
            Color.black
            // Centred, so a single angle (Door or Bed picked) sits in the middle.
            if let image = frames[lens] {
                Image(uiImage: image).resizable().aspectRatio(contentMode: .fit)
            } else {
                ProgressView().tint(.white)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .overlay(alignment: .bottomLeading) {
            if labelled {
                Text(Self.title(lens))
                    .font(.caption.weight(.bold))
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(.ultraThinMaterial, in: .capsule)
                    .padding(10)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            guard (status?.lenses?.count ?? 1) > 1 else { return }
            withAnimation(.easeInOut(duration: 0.25)) { solo = solo == nil ? lens : nil }
        }
        .accessibilityLabel("\(Self.title(lens)) camera")
    }

    private var waitingText: String {
        if let left = status?.killSecondsLeft, status?.killed == true, left > 0 {
            return "Camera locked off for \(left / 60 + 1) more min"
        }
        return "Waking the camera…"
    }

    /// Under the feeds: stacked in portrait, one row in landscape so the
    /// pictures keep the height.
    private func controls(wide: Bool) -> some View {
        VStack(spacing: 8) {
            let layout = wide ? AnyLayout(HStackLayout(spacing: 12)) : AnyLayout(VStackLayout(spacing: 12))
            layout { barnDoorControl; killButton }
            if let message {
                Text(message).font(.footnote).foregroundStyle(.white.opacity(0.75))
            }
        }
    }

    @ViewBuilder private var barnDoorControl: some View {
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
    }

    private var killButton: some View {
            Button(role: .destructive) { Task { await kill() } } label: {
                Label("Kill camera (5 min)", systemImage: "video.slash.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .background(.red.opacity(0.85), in: .rect(cornerRadius: 16))
                    .foregroundStyle(.white)
            }
    }

    /// Heartbeat + frame pull (every live angle at once), until the view closes.
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
                frames = [:]
            } else {
                // A Core before 6.34 names no angles: pull its one frame as the door's.
                let wanted: [String?] = status?.lenses.map { $0.map(Optional.some) } ?? [nil]
                let pulled = await withTaskGroup(of: (String, Data?).self) { group in
                    for lens in wanted {
                        group.addTask { (lens ?? "door", try? await client.cameraFrame(lens: lens)) }
                    }
                    var out: [(String, Data?)] = []
                    for await result in group { out.append(result) }
                    return out
                }
                var next = frames.filter { lens, _ in wanted.contains(where: { ($0 ?? "door") == lens }) }
                for (lens, data) in pulled {
                    if let data, let image = UIImage(data: data) { next[lens] = image }
                }
                frames = next
            }
            try? await Task.sleep(for: .milliseconds(120))
        }
    }

    private func kill() async {
        status = try? await client?.cameraKill()
        frames = [:]
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
