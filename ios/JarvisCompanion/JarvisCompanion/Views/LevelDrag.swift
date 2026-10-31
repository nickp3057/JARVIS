import SwiftUI

/// Held while a control on the page owns the finger (the volume slider, a
/// tile being dragged), so `RootView`'s swipe-between-tabs ignores it. Stays
/// held a moment after release: the slider lets go before the swipe's own
/// end fires.
@MainActor
enum SwipeLock {
    private static var held = 0
    private static var releasedAt = Date.distantPast

    static var active: Bool { held > 0 || Date.now.timeIntervalSince(releasedAt) < 0.4 }

    static func hold() { held += 1 }

    static func release() {
        held = max(0, held - 1)
        releasedAt = .now
    }
}

/// Hold a light or fan tile on the iPad's Room tab, then drag up or down
/// without lifting: brightness (or fan speed) follows the finger. The hold
/// opens a big level bar over the panel (`LevelOverlay`) at 4 points per
/// percent, so 1% steps are easy to hit.
/// Steps go to Core at most four times a second; the last one on release.
/// Down to 0 turns the light (or fan) off. A hold released without dragging
/// still opens the light's (or fan's) sheet; `RoomView` does that.
@MainActor
final class LevelDragger: ObservableObject {
    /// The level each tile shows while (and just after) it's dragged.
    @Published private(set) var live: [String: Int] = [:]
    /// The tile being held: `RoomView` shows the big level bar for it.
    @Published private(set) var activeID: String?
    private var start = 0
    private var moved = false
    private var lastSent = Date.distantPast
    private var lastLevel: Int?
    private var clearTasks: [String: Task<Void, Never>] = [:]

    static let pointsPerPercent: CGFloat = 4
    /// Movement under this many points is a wobble, not a drag.
    static let deadZone: CGFloat = 8

    /// Where a drag starts: brightness for a light, speed for a fan, 0 when off.
    static func level(of entity: HomeEntity, on: Bool) -> Int {
        guard on else { return 0 }
        if let fan = entity.fan { return fan.percentage ?? 0 }
        return entity.light?.brightnessPct ?? 100
    }

    static func adjustable(_ entity: HomeEntity) -> Bool {
        entity.isToggleable && (entity.fan != nil || entity.light?.dimmable == true)
    }

    func begin(_ entity: HomeEntity, on: Bool) {
        let id = entity.entityID
        clearTasks[id]?.cancel()
        if activeID == nil { SwipeLock.hold() }
        activeID = id
        start = live[id] ?? Self.level(of: entity, on: on)
        live[id] = start
        moved = false
        lastSent = .distantPast
        lastLevel = start
    }

    /// The finger moved: up is brighter (negative translation).
    func update(_ entity: HomeEntity, translation: CGFloat, model: HubViewModel) {
        guard activeID == entity.entityID else { return }
        if !moved, abs(translation) < Self.deadZone { return }
        moved = true
        let level = min(100, max(0, start + Int((-translation / Self.pointsPerPercent).rounded())))
        live[entity.entityID] = level
        if level != lastLevel, Date.now.timeIntervalSince(lastSent) > 0.25 {
            lastSent = .now
            lastLevel = level
            model.setLevel(entity, level, final: false)
        }
    }

    /// The finger lifted. True if it was a drag (the level is sent); false
    /// for a plain hold, which opens the sheet instead.
    func end(_ entity: HomeEntity, model: HubViewModel) -> Bool {
        let id = entity.entityID
        guard activeID == id else { return false }
        activeID = nil
        SwipeLock.release()
        guard moved, let level = live[id] else {
            live[id] = nil
            return false
        }
        model.setLevel(entity, level, final: true)
        // Keep showing the new level until Core's refresh lands.
        clearTasks[id] = Task { [weak self] in
            try? await Task.sleep(for: .seconds(2.5))
            guard !Task.isCancelled else { return }
            self?.live[id] = nil
        }
        return true
    }
}

/// The room speaker's volume (JARVIS's voice, timers and chimes on the
/// Optiplex), 0-10 like "Hey JARVIS, volume 4". Sent when the finger lifts;
/// Core plays a soft chime at the new level. Inside Core's quiet hours it
/// sets the night level. Hidden on an older Core without `/api/room/volume`.
struct RoomVolumeControl: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.scenePhase) private var scenePhase
    @State private var level = 5.0
    @State private var night = false
    @State private var loaded = false
    @State private var unsupported = false
    @State private var editing = false
    @State private var failure: String?

    var body: some View {
        if !unsupported {
            HStack(spacing: 12) {
                Image(systemName: level == 0 ? "speaker.slash.fill" : "speaker.wave.2.fill")
                    .font(.title3)
                    .foregroundStyle(JarvisTheme.cyan)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(night ? "JARVIS volume · night" : "JARVIS volume")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(failure == nil ? Color.secondary : JarvisTheme.amber)
                        Spacer(minLength: 0)
                        Text(loaded ? "\(Int(level))" : "–")
                            .font(.headline)
                            .monospacedDigit()
                    }
                    Slider(value: $level, in: 0...10, step: 1) { isEditing in
                        if isEditing != editing { isEditing ? SwipeLock.hold() : SwipeLock.release() }
                        editing = isEditing
                        if !isEditing { send(Int(level)) }
                    }
                    .tint(JarvisTheme.cyan)
                    .disabled(!loaded)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("JARVIS volume")
            .accessibilityValue(failure ?? "\(Int(level)) of 10")
            .task(id: scenePhase) {
                // Voice ("louder") and the day/night switch change it too.
                while scenePhase == .active, !Task.isCancelled {
                    await load()
                    try? await Task.sleep(for: .seconds(60))
                }
            }
        }
    }

    private func load() async {
        guard !editing else { return }
        do {
            apply(try await JarvisAPIClient.make(from: settings).roomVolume())
        } catch {
            if !loaded { unsupported = true }
        }
    }

    private func send(_ value: Int) {
        Task {
            do {
                apply(try await JarvisAPIClient.make(from: settings).setRoomVolume(value))
                failure = nil
            } catch {
                failure = "Couldn't set the volume"
            }
        }
    }

    private func apply(_ volume: RoomVolume) {
        if let value = volume.level, !editing { level = Double(value) }
        night = volume.night
        loaded = true
    }
}

/// The expanded control while a tile is held: a tall bar (like Control
/// Center's) filled to the level, the big number and the device's name over
/// a dimmed panel. It only shows; the finger stays on the tile's gesture.
struct LevelOverlay: View {
    let name: String
    let symbol: String
    let level: Int

    var body: some View {
        ZStack {
            Color.black.opacity(0.55).ignoresSafeArea()
            VStack(spacing: 20) {
                Label(name, systemImage: symbol)
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.85))
                Text(level == 0 ? "Off" : "\(level)%")
                    .font(.system(size: 64, weight: .thin, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(.white)
                    .contentTransition(.numericText())
                ZStack(alignment: .bottom) {
                    RoundedRectangle(cornerRadius: 36).fill(Color.white.opacity(0.12))
                    RoundedRectangle(cornerRadius: 36)
                        .fill(JarvisTheme.amber.opacity(0.9))
                        .frame(height: 400 * CGFloat(level) / 100)
                }
                .frame(width: 130, height: 400)
                .clipShape(.rect(cornerRadius: 36))
                .overlay(RoundedRectangle(cornerRadius: 36).strokeBorder(.white.opacity(0.2)))
                Text("Drag up or down · let go to set")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.6))
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Hold-and-release on the fan tile: power, speed and the fan's modes
/// (Fresh / Nature on Nick's fan), `POST /api/home/fan`.
struct FanSheet: View {
    @ObservedObject var model: HubViewModel
    let entityID: String
    @Environment(\.dismiss) private var dismiss
    @State private var speed = 50.0
    @State private var loaded = false

    private var entity: HomeEntity? { model.entities.value?.first { $0.entityID == entityID } }

    var body: some View {
        NavigationStack {
            Group {
                if let entity, let fan = entity.fan {
                    content(entity, fan)
                } else {
                    ContentUnavailableView("Fan unavailable", systemImage: "fan.slash")
                }
            }
            .navigationTitle(entity?.friendlyName ?? "Fan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .presentationDetents([.medium])
        .onAppear {
            guard !loaded, let fan = entity?.fan else { return }
            loaded = true
            speed = Double(entity?.isOn == true ? (fan.percentage ?? 50) : 50)
        }
    }

    private func content(_ entity: HomeEntity, _ fan: FanDetails) -> some View {
        VStack(alignment: .leading, spacing: 24) {
            Button { model.toggle(entity, to: !entity.isOn) } label: {
                Label(entity.isOn ? "Turn off" : "Turn on", systemImage: "power")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .background((entity.isOn ? JarvisTheme.amber : JarvisTheme.cyan).opacity(0.16),
                                in: .rect(cornerRadius: 14))
            }
            .buttonStyle(CockpitButtonStyle())

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("Speed", systemImage: "fan").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text("\(Int(speed))%").monospacedDigit().foregroundStyle(.secondary)
                }
                Slider(value: $speed, in: 1...100, step: max(1, fan.step ?? 1)) { editing in
                    if !editing { model.setFan(entity, percentage: Int(speed)) }
                }
                .tint(JarvisTheme.amber)
            }

            if !fan.presetModes.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Mode", systemImage: "wind").font(.subheadline.weight(.semibold))
                    Picker("Mode", selection: Binding(
                        get: { fan.presetMode ?? "" },
                        set: { model.setFan(entity, presetMode: $0) }
                    )) {
                        ForEach(fan.presetModes, id: \.self) { Text($0.capitalized).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(24)
    }
}
