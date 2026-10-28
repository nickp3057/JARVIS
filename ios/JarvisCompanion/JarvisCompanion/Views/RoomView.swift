import SwiftUI
import UIKit

/// The iPad's Room tab: a wall-panel for Nick's room. A clock and what's
/// next on one side; big light/switch tiles and the Home Assistant scenes
/// (picked like Focus modes) on the other. Side by side in landscape,
/// stacked in portrait (iPad mini). Same Core calls as the phone's Home tab.
/// Long-press a light for brightness and colour (`LightSheet`).
///
/// Night screen: with Keep screen on, after dark (HA's `sun.sun`) and every
/// light (and lamp) off for 45 s, the screen goes black at its lowest
/// brightness, so the iPad isn't a night light. Any light coming on, sunrise,
/// or a tap brings it back; a tap with the room still dark sleeps again 45 s
/// later. iPads don't let apps read their light sensor, so the room's own
/// lights stand in for it.
struct RoomView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: HubViewModel
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("room.keepAwake") private var keepAwake = false
    @State private var wake: WakeSchedule?
    @State private var showingRemote = false
    @State private var showingWake = false
    @State private var holdsPolling = false
    @State private var lightSheet: String?
    @AppStorage("room.nightScreen") private var nightScreen = true
    @State private var asleep = false
    /// The screen's brightness before the night screen, to put back.
    @State private var savedBrightness: CGFloat?
    @ObservedObject private var voice = VoicePresence.shared

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width > geo.size.height
            ScrollView {
                let layout = wide
                    ? AnyLayout(HStackLayout(alignment: .top, spacing: 20))
                    : AnyLayout(VStackLayout(spacing: 20))
                layout {
                    clockPanel
                        .frame(width: wide ? min(360, geo.size.width * 0.36) : nil)
                    VStack(spacing: 20) {
                        devicesPanel
                        scenesPanel
                    }
                }
                .padding(24)
            }
        }
        .background { JarvisBackdrop().ignoresSafeArea() }
        .refreshable { await refresh() }
        .sheet(isPresented: $showingRemote) { TVRemoteView() }
        .sheet(isPresented: $showingWake) {
            NavigationStack { WakeScheduleView() }
        }
        .sheet(item: Binding(get: { lightSheet.map(SheetID.init) }, set: { lightSheet = $0?.id })) { item in
            LightSheet(model: model, entityID: item.id)
        }
        .fullScreenCover(isPresented: $asleep) {
            // Black; a tap is the only thing on it.
            Color.black.ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { asleep = false }
                .accessibilityLabel("Night screen. Tap to wake.")
        }
        .transaction { $0.disablesAnimations = asleep }
        .task(id: "\(roomIsDark)-\(asleep)") {
            if roomIsDark && !asleep {
                try? await Task.sleep(for: .seconds(45))
                if !Task.isCancelled, roomIsDark { asleep = true }
            } else if !roomIsDark && asleep {
                asleep = false
            }
        }
        .onChange(of: asleep) { _, sleeping in setDimmed(sleeping) }
        // "Hey JARVIS" lights the screen back up (the overlay shows above it).
        .onChange(of: voice.active) { _, active in if active { asleep = false } }
        .task {
            if !holdsPolling {
                holdsPolling = true
                model.retainPolling(start: scenePhase == .active)
            }
            await refresh()
        }
        .task(id: scenePhase) {
            // The next alarm changes rarely; once a minute is plenty.
            while scenePhase == .active, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                await loadWake()
            }
        }
        .onDisappear {
            if holdsPolling {
                holdsPolling = false
                model.releasePolling()
            }
            UIApplication.shared.isIdleTimerDisabled = false
            asleep = false
            setDimmed(false)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { asleep = false; setDimmed(false) }
        }
        .onAppear { UIApplication.shared.isIdleTimerDisabled = keepAwake }
        .onChange(of: keepAwake) { _, on in UIApplication.shared.isIdleTimerDisabled = on }
    }

    private struct SheetID: Identifiable { let id: String }

    /// After dark with every light and lamp off (and Keep screen on).
    private var roomIsDark: Bool {
        guard keepAwake, nightScreen, scenePhase == .active, !voice.active, model.sun == "below_horizon",
              let entities = model.entities.value, model.entities.failureText == nil else { return false }
        let sources = entities.filter { $0.domain == "light" || $0.friendlyName.lowercased().contains("lamp") }
        return !sources.isEmpty && sources.allSatisfy { !(model.pendingEntityStates[$0.entityID] ?? $0.isOn) }
    }

    /// The app may set the screen's brightness while it's in front; put it
    /// back whenever the night screen ends or the app leaves.
    private func setDimmed(_ dim: Bool) {
        guard let screen = (UIApplication.shared.connectedScenes.first as? UIWindowScene)?.screen else { return }
        if dim {
            if savedBrightness == nil { savedBrightness = screen.brightness }
            screen.brightness = 0
        } else if let saved = savedBrightness {
            screen.brightness = saved
            savedBrightness = nil
        }
    }

    private func refresh() async {
        async let entities: Void = model.refreshEntities()
        async let scenes: Void = model.refreshScenes()
        async let now: Void = model.refreshNow()
        async let wake: Void = loadWake()
        _ = await (entities, scenes, now, wake)
    }

    private func loadWake() async {
        wake = try? await JarvisAPIClient.make(from: settings).wakeSchedule()
    }

    // MARK: - Clock and what's next

    private var clockPanel: some View {
        VStack(alignment: .leading, spacing: 18) {
            TimelineView(.everyMinute) { context in
                VStack(alignment: .leading, spacing: 2) {
                    Text(context.date, format: .dateTime.hour().minute())
                        .font(.system(size: 72, weight: .thin, design: .rounded))
                        .monospacedDigit()
                    Text(context.date, format: .dateTime.weekday(.wide).month(.wide).day())
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
            }
            Divider().overlay(JarvisTheme.border)
            infoRow("alarm", title: "Wake-up", value: wakeText) { showingWake = true }
            if let event = model.now.value?.nextEvent {
                infoRow("calendar", title: "Next up", value: eventText(event))
            }
            Divider().overlay(JarvisTheme.border)
            if let peace = model.scenes.value?.scenes.first(where: { $0.entityID == "scene.peace" }) {
                // Heading out: everything off, plugs too (HA's Peace scene).
                Button { model.runScene(peace) } label: {
                    HStack {
                        if model.runningScene == peace.entityID { ProgressView() }
                        Label("Peace", systemImage: "hand.wave")
                    }
                    .font(.title3.weight(.semibold))
                    .frame(maxWidth: .infinity, minHeight: 60)
                    .background(JarvisTheme.amber.opacity(0.16), in: .rect(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.amber.opacity(0.5)))
                }
                .buttonStyle(CockpitButtonStyle())
                .disabled(model.runningScene != nil)
                .accessibilityHint("Turns everything off for when you leave")
            }
            Button { showingRemote = true } label: {
                Label("TV remote", systemImage: "appletvremote.gen4")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .background(JarvisTheme.cyan.opacity(0.14), in: .rect(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.cyan.opacity(0.4)))
            }
            .buttonStyle(CockpitButtonStyle())
            Toggle("Keep screen on", isOn: $keepAwake)
                .tint(JarvisTheme.cyan)
                .font(.subheadline)
            Toggle("Dark at night when the lights are off", isOn: $nightScreen)
                .tint(JarvisTheme.cyan)
                .font(.subheadline)
                .disabled(!keepAwake)
        }
        .roomPanel()
    }

    private var wakeText: String {
        guard let wake, wake.enabled, let next = wake.nextAt else { return "Off" }
        let date = Date(timeIntervalSince1970: next)
        let day = Calendar.current.isDateInToday(date) ? "Today"
            : Calendar.current.isDateInTomorrow(date) ? "Tomorrow"
            : date.formatted(.dateTime.weekday(.wide))
        return "\(day) \(date.formatted(.dateTime.hour().minute()))"
    }

    private func eventText(_ event: NowEvent) -> String {
        guard let start = event.startAt else { return event.title }
        let date = Date(timeIntervalSince1970: start)
        let when = event.allDay ? date.formatted(.dateTime.weekday(.abbreviated))
            : date.formatted(.dateTime.weekday(.abbreviated).hour().minute())
        return "\(event.title) · \(when)"
    }

    private func infoRow(_ symbol: String, title: String, value: String,
                         action: (() -> Void)? = nil) -> some View {
        Button { action?() } label: {
            HStack(spacing: 12) {
                Image(systemName: symbol)
                    .font(.title3)
                    .foregroundStyle(JarvisTheme.cyan)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Text(value).font(.headline).lineLimit(2).multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
                if action != nil {
                    Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(action == nil)
    }

    // MARK: - Lights and switches

    private var devicesPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            heading("Lights & switches", symbol: "lightbulb.2")
            if let entities = model.entities.value, !entities.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 12)], spacing: 12) {
                    ForEach(entities) { deviceTile($0) }
                }
            } else if let failure = model.entities.failureText {
                Text(failure).font(.subheadline).foregroundStyle(JarvisTheme.amber)
            } else if model.entities.isLoading {
                ProgressView().frame(maxWidth: .infinity)
            } else {
                Text("No devices on the dashboard list (HOME_DASHBOARD_ENTITIES).")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .roomPanel()
    }

    private func deviceTile(_ entity: HomeEntity) -> some View {
        let pending = model.pendingEntityStates[entity.entityID]
        let on = pending ?? entity.isOn
        return Button { model.toggle(entity, to: !on) } label: {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Image(systemName: Self.symbol(for: entity))
                        .font(.title2)
                        .foregroundStyle(on ? JarvisTheme.amber : Color.secondary)
                    Spacer(minLength: 0)
                    if pending != nil { ProgressView() }
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(entity.friendlyName)
                        .font(.headline)
                        .lineLimit(2, reservesSpace: true)
                        .multilineTextAlignment(.leading)
                    Text(statusText(entity, on: on))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, minHeight: 120, alignment: .topLeading)
            .background(on ? JarvisTheme.amber.opacity(0.16) : JarvisTheme.cyan.opacity(0.05),
                        in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18)
                .strokeBorder(on ? JarvisTheme.amber.opacity(0.45) : JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(!entity.isToggleable || pending != nil)
        .simultaneousGesture(LongPressGesture(minimumDuration: 0.45).onEnded { _ in
            if entity.light?.dimmable == true { lightSheet = entity.entityID }
        })
        .sensoryFeedback(.impact(weight: .light), trigger: on)
        .accessibilityLabel(entity.friendlyName)
        .accessibilityValue(on ? "On" : "Off")
    }

    private func statusText(_ entity: HomeEntity, on: Bool) -> String {
        guard entity.isToggleable else { return entity.state.capitalized }
        guard on else { return "Off" }
        if let pct = entity.light?.brightnessPct { return "On · \(pct)%" }
        return "On"
    }

    private static func symbol(for entity: HomeEntity) -> String {
        let name = entity.friendlyName.lowercased()
        if name.contains("lamp") { return "lamp.table.fill" }
        if name.contains("strip") { return "light.strip.2.fill" }
        if name.contains("desk") { return "lamp.desk.fill" }
        if name.contains("wii") || name.contains("game") { return "gamecontroller.fill" }
        if name.contains("scent") { return "flame.fill" }
        if name.contains("tower") { return "building.2.fill" }
        switch entity.domain {
        case "light": return "lightbulb.fill"
        case "fan": return "fan.fill"
        case "switch": return "powerplug.fill"
        default: return "power"
        }
    }

    // MARK: - Scenes

    private var scenesPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            heading("Scenes", symbol: "sparkles")
            if let scenes = model.scenes.value, !scenes.scenes.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: 12)], spacing: 12) {
                    ForEach(scenes.scenes) { sceneTile($0, lit: $0.entityID == scenes.last) }
                }
            } else {
                Text("No scenes yet. Make one in Home Assistant (Settings › Automations & Scenes › Scenes) and it shows up here.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .roomPanel()
    }

    private func sceneTile(_ scene: HomeScene, lit: Bool) -> some View {
        Button { model.runScene(scene) } label: {
            HStack(spacing: 14) {
                ZStack {
                    if model.runningScene == scene.entityID {
                        ProgressView()
                    } else {
                        Image(systemName: scene.symbol).font(.title3.weight(.semibold))
                    }
                }
                .frame(width: 46, height: 46)
                .background(lit ? JarvisTheme.cyan.opacity(0.9) : JarvisTheme.panelRaised, in: Circle())
                .foregroundStyle(lit ? JarvisTheme.backgroundDeep : JarvisTheme.cyan)
                Text(scene.name)
                    .font(.headline)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 76, alignment: .leading)
            .background(lit ? JarvisTheme.cyan.opacity(0.14) : Color.clear, in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18)
                .strokeBorder(lit ? JarvisTheme.cyan.opacity(0.7) : JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(model.runningScene != nil)
        .accessibilityLabel(scene.name + (lit ? ", last run" : ""))
    }

    private func heading(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(JarvisTheme.cyan)
    }
}

private extension View {
    func roomPanel() -> some View {
        padding(20)
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .background(JarvisTheme.panelGradient, in: .rect(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).strokeBorder(JarvisTheme.border))
    }
}
