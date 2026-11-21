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

/// The iPad Room tab's light and fan tiles. Drag one up or down (landscape:
/// straight away; portrait, where the panel scrolls: after a short hold) and
/// the big level panel (`LevelPanel`) opens while brightness (or fan speed)
/// follows the finger at 4 points per percent, so 1% steps are easy to hit.
/// Steps go to Core at most four times a second; the last one on release.
/// Down to 0 turns the light (or fan) off. Hold and let go without dragging
/// and the same panel stays open to use: drag its bar, pick a colour.
@MainActor
final class LevelDragger: ObservableObject {
    /// The level each tile shows while (and just after) it's dragged.
    @Published private(set) var live: [String: Int] = [:]
    /// The tile (or open panel's bar) being dragged.
    @Published private(set) var activeID: String?
    /// The panel left open after a hold-and-release.
    @Published private(set) var panelID: String?
    private var start = 0
    private var baseline: CGFloat = 0
    private var moved = false
    private var lastSent = Date.distantPast
    private var lastLevel: Int?
    private var clearTasks: [String: Task<Void, Never>] = [:]

    static let pointsPerPercent: CGFloat = 4
    /// Movement under this many points (past where the drag began) is a wobble.
    static let deadZone: CGFloat = 4

    /// Where a drag starts: brightness for a light, speed for a fan, 0 when off.
    static func level(of entity: HomeEntity, on: Bool) -> Int {
        guard on else { return 0 }
        if let fan = entity.fan { return fan.percentage ?? 0 }
        return entity.light?.brightnessPct ?? 100
    }

    static func adjustable(_ entity: HomeEntity) -> Bool {
        entity.isToggleable && (entity.fan != nil || entity.light?.dimmable == true)
    }

    /// The level to show for a tile: mid-drag, just set, or Core's.
    func shown(_ entity: HomeEntity, on: Bool) -> Int {
        live[entity.entityID] ?? Self.level(of: entity, on: on)
    }

    /// `baseline`: the finger's vertical travel when the drag was recognised,
    /// so the level doesn't jump by the distance it took to recognise it.
    func begin(_ entity: HomeEntity, on: Bool, baseline: CGFloat = 0) {
        let id = entity.entityID
        clearTasks[id]?.cancel()
        if activeID == nil { SwipeLock.hold() }
        activeID = id
        start = live[id] ?? Self.level(of: entity, on: on)
        live[id] = start
        self.baseline = baseline
        moved = false
        lastSent = .distantPast
        lastLevel = start
    }

    /// The finger moved: up is brighter (negative translation).
    func update(_ entity: HomeEntity, translation: CGFloat, model: HubViewModel) {
        guard activeID == entity.entityID else { return }
        let travel = translation - baseline
        if !moved, abs(travel) < Self.deadZone { return }
        moved = true
        let level = min(100, max(0, start + Int((-travel / Self.pointsPerPercent).rounded())))
        live[entity.entityID] = level
        if level != lastLevel, Date.now.timeIntervalSince(lastSent) > 0.25 {
            lastSent = .now
            lastLevel = level
            model.setLevel(entity, level, final: false)
        }
    }

    /// The finger lifted. True if it moved (the level is sent).
    @discardableResult
    func end(_ entity: HomeEntity, model: HubViewModel) -> Bool {
        let id = entity.entityID
        guard activeID == id else { return false }
        activeID = nil
        SwipeLock.release()
        guard moved, let level = live[id] else {
            if panelID != id { live[id] = nil }
            return false
        }
        model.setLevel(entity, level, final: true)
        keepShowing(id)
        return true
    }

    /// A colour or mode pick in the panel: the level stays as shown until Core catches up.
    func keepShowing(_ id: String) {
        clearTasks[id]?.cancel()
        clearTasks[id] = Task { [weak self] in
            try? await Task.sleep(for: .seconds(2.5))
            guard !Task.isCancelled, self?.activeID != id else { return }
            self?.live[id] = nil
        }
    }

    func openPanel(_ id: String) {
        if panelID == nil { SwipeLock.hold() }
        panelID = id
    }

    func closePanel() {
        guard panelID != nil else { return }
        panelID = nil
        SwipeLock.release()
    }
}

/// The room speaker's volume (JARVIS's voice, timers and chimes on the
/// Surface), 0-10 like "Hey JARVIS, volume 4". Sent when the finger lifts;
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

/// The big level panel: a tall bar (like Control Center's) filled to the
/// level, the big number, and what else the device does: power, Rainbow,
/// whites and colours for a colour light (the strip, the desk), the modes
/// for the fan. Over a dimmed room panel. While a tile is being dragged it
/// only shows (`interactive` false: the finger stays on the tile); after a
/// hold-and-release it's live: drag the bar, tap a colour, tap outside or
/// Done to close.
struct LevelPanel: View {
    @ObservedObject var model: HubViewModel
    @ObservedObject var dragger: LevelDragger
    let entity: HomeEntity
    let symbol: String
    let interactive: Bool
    @State private var custom = Color.white

    private static let barHeight: CGFloat = 400

    private var on: Bool { model.pendingEntityStates[entity.entityID] ?? entity.isOn }
    private var level: Int { dragger.shown(entity, on: on) }
    private var hasOptions: Bool {
        entity.fan.map { !$0.presetModes.isEmpty } ?? (entity.light?.color == true || entity.light?.temperature == true)
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.6)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { dragger.closePanel() }
            card
        }
        .allowsHitTesting(interactive)
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(spacing: 12) {
                Image(systemName: symbol)
                    .font(.title2)
                    .foregroundStyle(on ? JarvisTheme.amber : Color.secondary)
                Text(entity.friendlyName).font(.title2.weight(.semibold))
                Spacer(minLength: 20)
                if interactive {
                    Button("Done") { dragger.closePanel() }
                        .font(.headline)
                        .buttonStyle(CockpitButtonStyle())
                        .foregroundStyle(JarvisTheme.cyan)
                } else {
                    Text("Let go to set").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            HStack(alignment: .top, spacing: 36) {
                VStack(spacing: 14) {
                    Text(level == 0 ? "Off" : "\(level)%")
                        .font(.system(size: 56, weight: .thin, design: .rounded))
                        .monospacedDigit()
                        .contentTransition(.numericText())
                        .frame(minWidth: 150)
                    bar
                }
                if hasOptions || interactive {
                    options.frame(width: hasOptions ? 360 : 240, alignment: .topLeading)
                }
            }
        }
        .padding(28)
        .fixedSize()
        .jarvisSurface(in: .rect(cornerRadius: 28))
        .overlay(RoundedRectangle(cornerRadius: 28).strokeBorder(JarvisTheme.border))
        .shadow(color: .black.opacity(0.5), radius: 30)
        .padding(20)
    }

    private var bar: some View {
        ZStack(alignment: .bottom) {
            RoundedRectangle(cornerRadius: 34).fill(Color.white.opacity(0.08))
            Rectangle()
                .fill(LinearGradient(colors: [JarvisTheme.amber.opacity(0.75), JarvisTheme.amber],
                                     startPoint: .bottom, endPoint: .top))
                .frame(height: Self.barHeight * CGFloat(level) / 100)
            Image(systemName: entity.fan != nil ? "fan.fill" : "sun.max.fill")
                .font(.title)
                .foregroundStyle(level > 8 ? JarvisTheme.backgroundDeep.opacity(0.7) : Color.secondary)
                .padding(.bottom, 22)
        }
        .frame(width: 130, height: Self.barHeight)
        .clipShape(.rect(cornerRadius: 34))
        .overlay(RoundedRectangle(cornerRadius: 34).strokeBorder(.white.opacity(0.18)))
        .contentShape(Rectangle())
        .gesture(DragGesture(minimumDistance: 0)
            .onChanged { drag in
                if dragger.activeID != entity.entityID { dragger.begin(entity, on: on) }
                dragger.update(entity, translation: drag.translation.height, model: model)
            }
            .onEnded { _ in dragger.end(entity, model: model) })
        .sensoryFeedback(.selection, trigger: level / 10)
        .accessibilityElement()
        .accessibilityLabel(entity.fan != nil ? "Speed" : "Brightness")
        .accessibilityValue("\(level) percent")
        .accessibilityAdjustableAction { direction in
            let next = min(100, max(0, level + (direction == .increment ? 10 : -10)))
            model.setLevel(entity, next, final: true)
        }
    }

    @ViewBuilder private var options: some View {
        VStack(alignment: .leading, spacing: 20) {
            Button { model.toggle(entity, to: !on) } label: {
                Label(on ? "Turn off" : "Turn on", systemImage: "power")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .background((on ? JarvisTheme.amber : JarvisTheme.cyan).opacity(0.16), in: .rect(cornerRadius: 14))
            }
            .buttonStyle(CockpitButtonStyle())

            if let fan = entity.fan, !fan.presetModes.isEmpty {
                section("Mode", symbol: "wind") {
                    ForEach(fan.presetModes, id: \.self) { mode in
                        let picked = fan.presetMode == mode
                        Button { model.setFan(entity, presetMode: mode) } label: {
                            Text(mode.capitalized)
                                .font(.headline)
                                .frame(maxWidth: .infinity, minHeight: 48)
                                .background(picked ? JarvisTheme.cyan.opacity(0.85) : JarvisTheme.panelRaised,
                                            in: .rect(cornerRadius: 12))
                                .foregroundStyle(picked ? JarvisTheme.backgroundDeep : .primary)
                        }
                        .buttonStyle(CockpitButtonStyle())
                    }
                }
            }

            if let light = entity.light {
                if light.rainbow == true {
                    Button { model.setLight(entity, rainbow: true) } label: {
                        Label("Rainbow", systemImage: "rainbow")
                            .font(.headline)
                            .foregroundStyle(.white)
                            .frame(maxWidth: .infinity, minHeight: 50)
                            .background(LinearGradient(colors: [.red, .orange, .yellow, .green, .cyan, .blue, .purple],
                                                       startPoint: .leading, endPoint: .trailing)
                                .opacity(0.85), in: .rect(cornerRadius: 14))
                            .shadow(color: .black.opacity(0.35), radius: 1)
                    }
                    .buttonStyle(CockpitButtonStyle())
                }
                if light.temperature {
                    section("Whites", symbol: "thermometer.sun") {
                        ForEach(LightSheet.whites.filter { inRange($0.1, light) }, id: \.1) { name, kelvin in
                            swatch(name, color: LightSheet.kelvinColor(kelvin)) { model.setLight(entity, kelvin: kelvin) }
                        }
                    }
                }
                if light.color {
                    section("Colours", symbol: "paintpalette") {
                        ForEach(LightSheet.colours, id: \.0) { name, rgb in
                            swatch(name, color: Color(red: Double(rgb[0]) / 255, green: Double(rgb[1]) / 255,
                                                      blue: Double(rgb[2]) / 255)) {
                                model.setLight(entity, rgb: rgb)
                            }
                        }
                    }
                    ColorPicker("Any colour", selection: Binding(
                        get: { custom },
                        set: { custom = $0; model.setLight(entity, rgb: LightSheet.rgb($0)) }
                    ), supportsOpacity: false)
                        .font(.subheadline.weight(.semibold))
                }
            }
        }
    }

    private func inRange(_ kelvin: Int, _ light: LightDetails) -> Bool {
        kelvin >= (light.minKelvin ?? 0) && kelvin <= (light.maxKelvin ?? 10_000)
    }

    private func section<Content: View>(_ title: String, symbol: String,
                                        @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: symbol).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 72), spacing: 12)], spacing: 12) { content() }
        }
    }

    private func swatch(_ name: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Circle().fill(color)
                    .frame(width: 44, height: 44)
                    .overlay(Circle().strokeBorder(.white.opacity(0.25)))
                Text(name).font(.caption).foregroundStyle(.secondary)
            }
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel(name)
    }
}
