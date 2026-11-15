import SwiftUI
import UIKit

/// The iPad's Room tab: a wall-panel for Nick's room, all on one screen (no
/// scrolling in landscape). Left: the clock and battery, the reactor (three
/// quick taps: Barn Door Protocol), what's next, the TV remote and the
/// panel's settings (a sheet, to keep the toggles out of sight). The battery
/// sits above the clock (top left). Right: the light/switch tiles with a
/// Peace square in the top corner, and Nick's favourite scenes (Game Time,
/// Movie, Study) in one row; the rest are voice-only. Portrait (iPad mini)
/// stacks and scrolls. Drag a light or the fan up or down (in portrait, hold
/// first, since the panel scrolls) and the big level panel (`LevelPanel`)
/// opens while brightness or speed follows the finger (`LevelDragger`); hold
/// and let go and the same panel stays open to use, with colours, Rainbow
/// and the fan's modes. Neither toggles the tile. The clock panel has
/// JARVIS's own volume (the room speaker, `RoomVolumeControl`). Core pushes every Home
/// Assistant change (`/api/home/stream`), so a switch flipped on the phone or
/// the wall shows here within about half a second.
///
/// Night screen: with Keep screen on, after dark (HA's `sun.sun`) and every
/// light (and lamp) off for 45 s, the screen goes black at its lowest
/// brightness, so the iPad isn't a night light. The lights going out from
/// anywhere (the phone, voice, Peace, Goodnight, a wall switch) does the
/// same 3 s later, day or night. Any light coming on, sunrise or a timer going off
/// brings it back. A tap anywhere wakes it and turns on the tap-to-wake light
/// (Bedroom Lights unless changed); with the room still dark it sleeps again
/// 45 s later (after dark). The wake-up alarm's screen keeps it away until
/// the alarm is answered or closed. iPads don't let apps read their light sensor, so
/// the room's own lights stand in for it.
///
/// Full screen hides the tab bar, status bar and home indicator so the iPad
/// is just the panel; a swipe still reaches the other tabs.
///
/// On the Mac (Mac Catalyst) the same tab is a dashboard: no night screen,
/// keep-awake, full screen or tap-to-wake, so those toggles are hidden.
struct RoomView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: HubViewModel
    @Environment(\.scenePhase) fileprivate var scenePhase
    @AppStorage("room.keepAwake") fileprivate var keepAwake = false
    @State private var wake: WakeSchedule?
    @State private var showingRemote = false
    @State private var showingWake = false
    @State private var holdsPolling = false
    @State private var lightSheet: LightSheetID?
    @AppStorage("room.nightScreen") private var nightScreen = true
    @State private var asleep = false
    /// The dark screen was tapped away (the nightstand waits longer to go dark again).
    @State private var tappedAwake = false
    /// Peace or Goodnight just ran: go dark once the lights are out, even by day.
    @State private var lightsOut = false
    @AppStorage("room.fullScreen") private var fullScreen = false
    /// Light a tap on the night screen turns on; "" = just the screen.
    @AppStorage("room.wakeLight") private var wakeLight = "light.bedroom_lights"
    @AppStorage(RoomView.autoReturnKey) private var autoReturn = true
    /// The screen's brightness before the night screen, to put back.
    @State private var savedBrightness: CGFloat?
    /// The screen's brightness before movie mode dimmed it.
    @State private var movieBrightness: CGFloat?
    /// Bumped by each tap in movie mode: full brightness, then back to dim.
    @State fileprivate var movieTouches = 0
    @Environment(\.cameraWatching) fileprivate var cameraWatching
    @ObservedObject private var voice = VoicePresence.shared
    @ObservedObject private var cards = AnswerCardPresenter.shared
    @ObservedObject private var wakeRinging = WakeRinging.shared
    @StateObject private var holds = HoldSuppressor()
    @StateObject private var dragger = LevelDragger()
    /// The tile a hold is in progress on; resets when the finger lifts or the hold is cancelled.
    @GestureState private var holding: String?
    /// This touch dragged a level (so lifting after a hold doesn't leave the panel open).
    @State private var touchDragged = false
    @State private var holdSeen = false
    /// The first movement of this touch was sideways: not a level drag.
    @State private var touchSideways = false
    /// Landscape: no scrolling, so a plain drag on a tile sets its level.
    @State private var wideLayout = true
    @State private var showingSettings = false
    @State private var showingImport = false
    @State private var roomCover: RoomCover?
    /// A note opened from the Memory map.
    @State private var openNote: String?

    enum RoomCover: String, Identifiable {
        case memoryMap, linkMap, agentMap
        var id: String { rawValue }
    }
    @AppStorage(RoomView.favoritesKey) private var favoriteScenes = RoomView.defaultFavorites
    /// "Back to the panel" during a movie; cleared when the scene changes.
    @State private var movieDismissed = false
    /// Nightstand or Door (`PadRole`); "" = by screen size.
    @AppStorage(PadRole.storageKey) private var padRoleRaw = ""
    fileprivate var padRole: PadRole { PadRole.resolve(padRoleRaw) }
    /// The nightstand's night screen shows a dim clock and the next alarm.
    @AppStorage(RoomView.nightClockKey) private var nightClock = true
    static let nightClockKey = "room.nightClock"
    /// How bright the nightstand's dark-screen clock is (grey level).
    @AppStorage(RoomView.darkClockKey) private var darkClockLevel = NightstandDarkClock.defaultLevel
    static let darkClockKey = "nightstand.darkClockLevel"
    /// The nightstand's own tap-to-wake light: none by default, so a tap in
    /// the night shows the panel without lighting the room.
    @AppStorage(RoomView.nightstandWakeLightKey) private var nightstandWakeLight = ""
    static let nightstandWakeLightKey = "nightstand.wakeLight"

    /// The light a tap on the dark screen turns on, for this iPad's role.
    private var tapLight: String { padRole == .nightstand ? nightstandWakeLight : wakeLight }

    private var nightstandPanel: some View {
        NightstandPanel(
            model: model, wakeText: wakeText, alarmOn: wake?.enabled == true && wake?.nextAt != nil,
            nextEvent: model.now.value?.nextEvent.map(eventText), timers: timers,
            talking: talking, voiceActive: voice.active, dialogue: AnyView(RoomDialogue(presence: voice)),
            showRemote: { showingRemote = true }, showWake: { showingWake = true },
            showSettings: { showingSettings = true },
            menuExtras: AnyView(Group {
                Button { roomCover = .memoryMap } label: { Label("Memory map", systemImage: "point.3.connected.trianglepath.dotted") }
                Button { roomCover = .linkMap } label: { Label("Link map", systemImage: "dot.radiowaves.left.and.right") }
                Button { roomCover = .agentMap } label: { Label("Agents", systemImage: "person.3.sequence") }
                Button { showingImport = true } label: { Label("Import memory", systemImage: "square.and.arrow.down") }
            }))
        .transition(.opacity)
    }

    /// The Movie scene is on.
    private var movieScene: Bool {
        !RootView.isMac && model.scenes.value?.current == "scene.movie"
    }

    /// Movie is on: the nightstand iPad becomes a dark, quiet movie remote.
    /// The door iPad stays the panel (`movieDims` still dims it).
    fileprivate var movieMode: Bool {
        movieScene && !movieDismissed && padRole.hostsMovieMode
    }

    /// Movie's dim backlight: the movie remote, and the door panel for the
    /// whole film, so neither glows while the film is on.
    fileprivate var movieDims: Bool {
        movieMode || (movieScene && !padRole.hostsMovieMode)
    }

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width > geo.size.height
            if movieMode {
                MovieModeView(model: model, showRemote: { showingRemote = true },
                              backToPanel: { withAnimation(.easeInOut(duration: 0.4)) { movieDismissed = true } })
                    .simultaneousGesture(TapGesture().onEnded { movieTouches &+= 1 })
                    .transition(.opacity)
            } else if padRole == .nightstand && !RootView.isMac {
                nightstandPanel
            } else if wide {
                HStack(alignment: .top, spacing: 18) {
                    clockPanel(fill: true)
                        .frame(width: min(400, geo.size.width * 0.34))
                    // The lights card stretches so the column ends flush with the
                    // clock's, no gap under Scenes.
                    VStack(spacing: 18) {
                        if talking {
                            dialoguePanel
                        } else {
                            devicesPanel
                            scenesPanel
                        }
                    }
                    .frame(maxHeight: .infinity)
                }
                .padding(20)
            } else {
                ScrollView {
                    VStack(spacing: 18) {
                        clockPanel(fill: false)
                        if talking {
                            dialoguePanel.frame(minHeight: 360)
                        } else {
                            devicesPanel
                            scenesPanel
                        }
                    }
                    .padding(20)
                }
            }
        }
        // The door panel during a movie: any touch lights it back up for 20 s.
        .simultaneousGesture(TapGesture().onEnded { movieTouches &+= 1 },
                             including: movieDims && !movieMode ? .all : .subviews)
        // Full screen really is full: down past the home indicator's strip.
        .animation(.easeInOut(duration: 0.35), value: talking)
        .modifier(RoomVoiceHost(voice: voice, covered: covered))
        .ignoresSafeArea(.container, edges: fullScreen && !RootView.isMac ? .all : [])
        .onGeometryChange(for: Bool.self) { $0.size.width > $0.size.height } action: { wideLayout = $0 }
        .modifier(RoomMemoryCovers(cover: $roomCover, showingImport: $showingImport, openNote: $openNote))
        .background { JarvisBackdrop().ignoresSafeArea() }
        .toolbar(fullScreen && !RootView.isMac ? .hidden : .automatic, for: .tabBar)
        .statusBarHidden(fullScreen && !RootView.isMac)
        .persistentSystemOverlays(fullScreen && !RootView.isMac ? .hidden : .automatic)
        .refreshable { await refresh() }
        .sheet(isPresented: $showingRemote) { TVRemoteView() }
        .sheet(isPresented: $showingSettings) {
            RoomSettingsView(model: model, wakeLightChoices: wakeLightChoices)
        }
        .sheet(isPresented: $showingWake) {
            NavigationStack { WakeScheduleView() }
        }
        .sheet(item: $lightSheet) { item in
            LightSheet(model: model, entityID: item.id)
        }
        .onChange(of: holding) { was, now in
            // The finger lifted after a hold: no drag means the panel stays open to use.
            guard let was, now == nil else { return }
            if holdSeen, !touchDragged { dragger.openPanel(was) }
            holdSeen = false
        }
        .overlay {
            if let id = dragger.panelID ?? dragger.activeID,
               let entity = model.entities.value?.first(where: { $0.entityID == id }) {
                LevelPanel(model: model, dragger: dragger, entity: entity, symbol: Self.symbol(for: entity),
                           interactive: dragger.panelID == id)
                    .transition(.opacity.combined(with: .scale(scale: 0.96)))
            }
        }
        .animation(.easeOut(duration: 0.18), value: dragger.panelID ?? dragger.activeID)
        .fullScreenCover(isPresented: $asleep) {
            // Black; a tap is the only thing on it. The nightstand iPad keeps
            // a dim grey clock (the Hub's old job), the backlight at its lowest.
            ZStack {
                Color.black.ignoresSafeArea()
                if padRole == .nightstand && nightClock {
                    NightstandDarkClock(alarm: wake?.enabled == true && wake?.nextAt != nil ? wakeText : nil,
                                       level: darkClockLevel)
                        .padding(24)
                        .ignoresSafeArea()
                }
                VStack(spacing: 18) {
                    Spacer()
                    // Lens on while the room sleeps: a faint grey line, readable
                    // from bed, so a watcher can't go unnoticed.
                    if cameraWatching {
                        Label("JARVIS is watching", systemImage: "eye")
                            .font(.title2.weight(.medium))
                            .foregroundStyle(Color(white: 0.6))
                            .padding(.bottom, padRole == .nightstand ? 28 : 0)
                    }
                    if padRole != .nightstand { Spacer() }
                }
            }
            .contentShape(Rectangle())
            .statusBarHidden(true)
            .persistentSystemOverlays(.hidden)
            .onTapGesture { wakeFromNight() }
            .accessibilityLabel(cameraWatching ? "Night screen. JARVIS is watching. Tap to wake." : "Night screen. Tap to wake.")
        }
        .transaction { $0.disablesAnimations = asleep }
        .task(id: "\(roomIsDark)-\(asleep)-\(lightsOut)") {
            if roomIsDark && !asleep {
                // The nightstand goes dark soon after the last light (and 20 s
                // after a tap shows the panel); the door keeps its 45 s.
                let wait: Double = lightsOut ? 3 : padRole == .nightstand ? (tappedAwake ? 20 : 6) : 45
                try? await Task.sleep(for: .seconds(wait))
                if !Task.isCancelled, roomIsDark { asleep = true }
            } else if !roomIsDark && asleep {
                asleep = false
            }
        }
        .onChange(of: asleep) { _, sleeping in
            setDimmed(sleeping)
            wakeRinging.nightScreenUp = sleeping
        }
        .onChange(of: model.runningScene) { ran, running in
            if running == nil, let ran, Self.lightsOutScenes.contains(ran) { lightsOut = true }
        }
        // Goodnight or Peace from anywhere else (voice, phone, HA) shows up
        // as the scenes' "last" within a poll or three.
        .onChange(of: model.scenes.value?.last) { _, last in
            if let last, Self.lightsOutScenes.contains(last), model.runningScene == nil { lightsOut = true }
        }
        // The room going dark from anywhere (the phone, voice, a wall switch)
        // darkens the panel too, day or night, a few seconds later.
        .onChange(of: model.scenes.value?.current) { _, _ in movieDismissed = false }
        .animation(.easeInOut(duration: 0.4), value: movieMode)
        .onChange(of: lightsAllOff) { was, off in
            if off == false { lightsOut = false; tappedAwake = false } else if off == true, was == false { lightsOut = true }
        }
        // "Hey JARVIS" lights the screen back up (the conversation shows in place).
        .onChange(of: voice.active) { _, active in if active { asleep = false } }
        .task {
            if !holdsPolling {
                holdsPolling = true
                model.retainPolling(start: scenePhase == .active, details: false)
            }
            await refresh()
        }
        .task(id: scenePhase) {
            // Core pushes every change (HubViewModel's home stream); this
            // poll is only the fallback for a dropped stream.
            while scenePhase == .active, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                if !model.homeStreamLive { await model.refreshEntities() }
            }
        }
        .task(id: scenePhase) {
            // The house's live traffic: every real signal fires one of the
            // neural core's thoughts (TrafficFeed). Open while the Room is up.
            guard scenePhase == .active else { return }
            TrafficFeed.shared.hold(settings: settings)
            defer { TrafficFeed.shared.release() }
            while !Task.isCancelled { try? await Task.sleep(for: .seconds(3600)) }
        }
        .task(id: scenePhase) {
            // The weather beside the date; Core refreshes it every few minutes.
            guard scenePhase == .active else { return }
            model.wantsGlance = true
            while !Task.isCancelled {
                await model.refreshGlance()
                try? await Task.sleep(for: .seconds(600))
            }
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
                model.releasePolling(details: false)
            }
            UIApplication.shared.isIdleTimerDisabled = false
            asleep = false
            wakeRinging.nightScreenUp = false
            setDimmed(false)
            setMovieDim(nil)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { asleep = false; setDimmed(false) }
            // The Hub tab stops the shared polling (and home stream) when the
            // app leaves the foreground but only restarts it if it's the one
            // showing, so the Room restarts it itself: without this the iPad
            // came back with no scenes, no stream and only the light fallback.
            if phase == .active, holdsPolling {
                model.startPolling()
                Task { await refresh() }
            }
        }
        // The nightstand always stays awake: its dark screen is the bedside clock.
        .onAppear { if !RootView.isMac { UIApplication.shared.isIdleTimerDisabled = keepAwake || movieDims || padRole == .nightstand } }
        .onChange(of: keepAwake) { _, on in if !RootView.isMac { UIApplication.shared.isIdleTimerDisabled = on || movieDims || padRole == .nightstand } }
        .onChange(of: padRoleRaw) { if !RootView.isMac { UIApplication.shared.isIdleTimerDisabled = keepAwake || movieDims || padRole == .nightstand } }
        .modifier(movieModeEffects)
    }

    /// Movie mode stays awake for the whole film (even with Keep screen on
    /// off) and drops the backlight, the iPad's biggest power draw.
    private var movieModeEffects: MovieModeEffects { MovieModeEffects(room: self) }

    /// "Hey JARVIS" (or an announcement) is on: the conversation takes the
    /// lights' place and the reactor grows a little.
    /// The reply panel is up while JARVIS talks, and while an answer screen
    /// ("what's the weather?") sits inside it.
    private var talking: Bool { (voice.showing || cards.current != nil) && !RootView.isMac }

    /// Something's over the panel, so the voice uses its own overlay window.
    private var covered: Bool {
        movieMode || showingRemote || showingSettings || showingWake || lightSheet != nil
            || roomCover != nil || showingImport || openNote != nil
    }

    private var dialoguePanel: some View {
        RoomDialogue(presence: voice)
            .roomPanel(fill: true)
            .transition(.opacity)
    }

    private static let lightsOutScenes: Set<String> = ["scene.peace", "scene.goodnight"]
    static let favoritesKey = "room.favoriteScenes"
    static let defaultFavorites = "scene.game_time,scene.movie,scene.study"
    /// Shared with `RootView`: slide back to Room after a while on another tab.
    static let autoReturnKey = "room.autoReturn"

    /// A tap on the night screen: screen back on, and the tap-to-wake light.
    private func wakeFromNight() {
        lightsOut = false
        asleep = false
        tappedAwake = true
        if let light = model.entities.value?.first(where: { $0.entityID == tapLight }), !light.isOn {
            model.toggle(light, to: true)
        }
    }

    /// Lights Nick can pick for tap-to-wake. Never the fan light (it's only
    /// ever turned off).
    private var wakeLightChoices: [HomeEntity] {
        (model.entities.value ?? []).filter { $0.domain == "light" && $0.entityID != "light.nicks_fan" }
    }

    private var timers: [NowTimer] {
        (model.now.value?.timers ?? []).filter { $0.state == "running" || $0.state == "alarming" }
    }

    /// Every light and lamp off; nil while the device list isn't known.
    private var lightsAllOff: Bool? {
        guard let entities = model.entities.value, model.entities.failureText == nil else { return nil }
        let sources = entities.filter { $0.domain == "light" || $0.friendlyName.lowercased().contains("lamp") }
        guard !sources.isEmpty else { return nil }
        return sources.allSatisfy { !(model.pendingEntityStates[$0.entityID] ?? $0.isOn) }
    }

    /// Lights out, and either after dark with Keep screen on or just after Peace/Goodnight.
    private var roomIsDark: Bool {
        // The wake-up alarm's screen beats the night screen (WakeRinging).
        guard !RootView.isMac, nightScreen, scenePhase == .active, !voice.active, !timers.contains(where: { $0.state == "alarming" }),
              !wakeRinging.showing, !wakeRinging.opening, !movieDims,
              // The nightstand goes dark whenever every light is off, day or night.
              padRole == .nightstand || lightsOut || (keepAwake && model.sun == "below_horizon") else { return false }
        return lightsAllOff == true
    }

    /// Movie mode's backlight (nil = movie over); the old level goes back
    /// when it ends.
    fileprivate func setMovieDim(_ level: CGFloat?) {
        guard !RootView.isMac, let screen = (UIApplication.shared.connectedScenes.first as? UIWindowScene)?.screen else { return }
        if let level {
            if movieBrightness == nil { movieBrightness = screen.brightness }
            screen.brightness = level
        } else if let saved = movieBrightness {
            screen.brightness = saved
            movieBrightness = nil
        }
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

    /// The weather beside the date: icon and temperature (Core's glance,
    /// the same snapshot as the Mac's Desk and the TV). Nothing while it's
    /// unknown, rather than a placeholder.
    @ViewBuilder private var weatherBadge: some View {
        if let weather = model.glance.value?.weather, weather.online != false, (weather.error ?? "").isEmpty,
           let temp = weather.temperatureF {
            HStack(spacing: 5) {
                Image(systemName: weather.symbol(night: model.sun == "below_horizon"))
                    .symbolRenderingMode(.multicolor)
                Text("\(Int(temp.rounded()))°").monospacedDigit()
            }
            .fixedSize()
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(Int(temp.rounded())) degrees, \(weather.description ?? "")")
        }
    }

    private func loadWake() async {
        wake = try? await JarvisAPIClient.make(from: settings).wakeSchedule()
    }

    // MARK: - Clock and what's next

    private func clockPanel(fill: Bool) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                if !RootView.isMac { BatteryBadge() }
                TimelineView(.everyMinute) { context in
                    VStack(alignment: .leading, spacing: 2) {
                        // One line: "11:47 PM" used to drop the PM below.
                        Text(context.date, format: .dateTime.hour().minute())
                            .font(.system(size: 64, weight: .thin, design: .rounded))
                            .monospacedDigit()
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                        HStack(spacing: 12) {
                            Text(context.date, format: .dateTime.weekday(.wide).month(.wide).day())
                                .lineLimit(1)
                                .minimumScaleFactor(0.8)
                            weatherBadge
                        }
                        .font(.title3)
                        .foregroundStyle(.secondary)
                    }
                }
            }
            // The reactor: its glow lifts while "Hey JARVIS" is listening;
            // three quick taps engage (or stand down) Barn Door Protocol.
            // Landscape: as big as the card's free space allows.
            if fill {
                GeometryReader { box in
                    // He may spill into the card's side padding: his rings rarely reach the edge.
                    core(size: max(170, min(box.size.width + 36, box.size.height)))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .frame(minHeight: 170)
            } else {
                core(size: 170)
            }
            Divider().overlay(JarvisTheme.border)
            if let event = model.now.value?.nextEvent {
                infoRow("calendar", title: "Next up", value: eventText(event))
            }
            ForEach(timers.prefix(2)) { timerRow($0) }
            RoomVolumeControl()
            HStack(spacing: 10) {
                Button { showingRemote = true } label: {
                    Label("TV remote", systemImage: "appletvremote.gen4")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 52)
                        .background(JarvisTheme.cyan.opacity(0.14), in: .rect(cornerRadius: 14))
                        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.cyan.opacity(0.4)))
                }
                .buttonStyle(CockpitButtonStyle())
                TVPowerButton()  // the TV on or off in one tap
            }
            // One menu for the rarely used things: settings, and JARVIS's memory.
            Menu {
                Button { showingSettings = true } label: { Label("Room settings", systemImage: "gearshape") }
                // The wake-up row lives here now (more room for JARVIS), its time in the label.
                Button { showingWake = true } label: { Label("Wake-up · \(wakeText)", systemImage: "alarm") }
                Divider()
                Button { roomCover = .memoryMap } label: { Label("Memory map", systemImage: "point.3.connected.trianglepath.dotted") }
                Button { roomCover = .linkMap } label: { Label("Link map", systemImage: "dot.radiowaves.left.and.right") }
                Button { roomCover = .agentMap } label: { Label("Agents", systemImage: "person.3.sequence") }
                Button { showingImport = true } label: { Label("Import memory", systemImage: "square.and.arrow.down") }
            } label: {
                Label("Menu", systemImage: "line.3.horizontal")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 40)
            }
            .buttonStyle(CockpitButtonStyle())
        }
        .roomPanel(fill: fill)
    }

    private func core(size: CGFloat) -> some View {
        JarvisCoreMark(size: size, active: voice.active)
            // Talking: he grows and his light lifts, steady (no pulsing).
            .background {
                Circle()
                    .fill(RadialGradient(colors: [JarvisTheme.reactor.opacity(0.35), .clear],
                                         center: .center, startRadius: 0, endRadius: size * 0.62))
                    .frame(width: size * 1.25, height: size * 1.25)
                    .opacity(talking ? 1 : 0)
                    .allowsHitTesting(false)
            }
            .scaleEffect(talking ? 1.12 : 1)
            .animation(.easeInOut(duration: 0.5), value: talking)
            .barnDoorTaps()
            .frame(maxWidth: .infinity)
            .accessibilityLabel("JARVIS. Triple-tap for Barn Door Protocol.")
            // Tell the plasma where he is, so it keeps its pool of red under him.
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { frame in
                PlasmaAnchor.center = CGPoint(x: frame.midX, y: frame.midY)
                PlasmaAnchor.radius = min(size, frame.height) / 2
            }
            .onDisappear { PlasmaAnchor.center = nil }
    }

    /// Heading out: everything off, plugs too (HA's Peace scene), then the
    /// night screen. A small square in the top-right corner.
    @ViewBuilder private var peaceSquare: some View {
        if let peace = model.scenes.value?.scenes.first(where: { $0.entityID == "scene.peace" }) {
            Button { model.runScene(peace) } label: {
                ZStack {
                    if model.runningScene == peace.entityID {
                        ProgressView()
                    } else {
                        Image(systemName: "hand.wave.fill").font(.title)
                    }
                }
                .foregroundStyle(JarvisTheme.amber)
                .frame(width: 58, height: 58)
                .background(JarvisTheme.amber.opacity(0.14), in: .rect(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.amber.opacity(0.5)))
            }
            .buttonStyle(CockpitButtonStyle())
            .disabled(model.runningScene != nil)
            .accessibilityLabel("Peace")
            .accessibilityHint("Turns everything off for when you leave")
        }
    }

    /// A running timer counts down live; one that's done says so.
    private func timerRow(_ timer: NowTimer) -> some View {
        let alarming = timer.state == "alarming"
        let end = Date(timeIntervalSince1970: timer.expiresAt)
        return HStack(spacing: 12) {
            Image(systemName: alarming ? "alarm.waves.left.and.right.fill" : "timer")
                .font(.title3)
                .foregroundStyle(alarming ? JarvisTheme.amber : JarvisTheme.cyan)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 1) {
                Text(timer.label.isEmpty ? "Timer" : timer.label.capitalized)
                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                if alarming || end <= .now {
                    Text("Time's up").font(.headline).foregroundStyle(JarvisTheme.amber)
                } else {
                    Text(timerInterval: Date.now...end, countsDown: true)
                        .font(.headline).monospacedDigit()
                }
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
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
            HStack {
                heading("Lights & switches", symbol: "lightbulb.2")
                Spacer()
                peaceSquare
            }
            if let entities = model.entities.value, !entities.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 12)], spacing: 12) {
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
        .roomPanel(fill: wideLayout)
    }

    private func deviceTile(_ entity: HomeEntity) -> some View {
        let pending = model.pendingEntityStates[entity.entityID]
        let live = dragger.live[entity.entityID]
        let on = live.map { $0 > 0 } ?? pending ?? entity.isOn
        return Button {
            guard !holds.swallows(entity.entityID) else { return }
            model.toggle(entity, to: !on)
        } label: {
            VStack(alignment: .leading, spacing: 12) {
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
                    Text(live.map { $0 > 0 ? "On · \($0)%" : "Off" } ?? statusText(entity, on: on))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, minHeight: 108, alignment: .topLeading)
            // While dragged, the tile fills from the bottom to the new level.
            .background(alignment: .bottom) {
                if let live {
                    GeometryReader { geo in
                        JarvisTheme.amber.opacity(0.28)
                            .frame(height: geo.size.height * CGFloat(live) / 100)
                            .frame(maxHeight: .infinity, alignment: .bottom)
                    }
                    .clipShape(.rect(cornerRadius: 18))
                }
            }
            .background(on ? JarvisTheme.amber.opacity(0.16) : JarvisTheme.cyan.opacity(0.05),
                        in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18)
                .strokeBorder(on ? JarvisTheme.amber.opacity(0.45) : JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(!entity.isToggleable || pending != nil)
        .simultaneousGesture(levelDrag(entity, on: on))
        .simultaneousGesture(holdToOpen(entity))
        .sensoryFeedback(.impact(weight: .light), trigger: on)
        .sensoryFeedback(.selection, trigger: live.map { $0 / 10 })
        .accessibilityLabel(entity.friendlyName)
        .accessibilityValue(on ? "On" : "Off")
    }

    private func statusText(_ entity: HomeEntity, on: Bool) -> String {
        guard entity.isToggleable else { return entity.state.capitalized }
        guard on else { return "Off" }
        if let pct = entity.light?.brightnessPct ?? entity.fan?.percentage { return "On · \(pct)%" }
        return "On"
    }

    /// Up or down on a light or fan tile: the level follows the finger and
    /// the big panel shows it. Landscape takes it straight away; portrait
    /// (the panel scrolls) only after a hold. Sideways is left to the page.
    private func levelDrag(_ entity: HomeEntity, on: Bool) -> some Gesture {
        DragGesture(minimumDistance: 10, coordinateSpace: .global)
            .onChanged { drag in
                guard LevelDragger.adjustable(entity), !touchSideways else { return }
                let id = entity.entityID
                if dragger.activeID != id {
                    let dx = drag.translation.width, dy = drag.translation.height
                    guard abs(dy) > abs(dx) else { touchSideways = true; return }
                    guard wideLayout || holding == id else { return }
                    holds.hold(id)  // the release mustn't toggle it
                    touchDragged = true
                    dragger.begin(entity, on: on, baseline: dy)
                }
                dragger.update(entity, translation: drag.translation.height, model: model)
            }
            .onEnded { _ in
                touchSideways = false
                dragger.end(entity, model: model)
            }
    }

    /// Hold (0.35 s): lifting without a drag leaves the big panel open
    /// (`onChange(of: holding)`).
    private func holdToOpen(_ entity: HomeEntity) -> some Gesture {
        LongPressGesture(minimumDuration: 0.35)
            .sequenced(before: DragGesture(minimumDistance: 0, coordinateSpace: .global))
            .updating($holding) { value, state, _ in
                if case .second(true, _) = value, LevelDragger.adjustable(entity) { state = entity.entityID }
            }
            .onChanged { value in
                guard case .second(true, _) = value, LevelDragger.adjustable(entity) else { return }
                if !holdSeen {  // a new hold: nothing dragged yet
                    holdSeen = true
                    touchDragged = false
                }
                holds.hold(entity.entityID)  // the release mustn't toggle it
            }
    }

    static func symbol(for entity: HomeEntity) -> String {
        let name = entity.friendlyName.lowercased()
        if entity.domain == "light" && (name.contains("fan") || entity.entityID == "light.nicks_fan") {
            return "fan.and.light.ceiling.fill"  // the fan's RGB ring
        }
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

    private var favoriteIDs: [String] {
        favoriteScenes.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    /// Nick's favourites (Game Time, Movie, Study) in one row, and nothing
    /// else: the other scenes are for "Hey JARVIS". With no favourite found
    /// (renamed in HA) the menu stands in so the panel isn't empty.
    /// The room's scene when it isn't one of the tiles, so something always
    /// says where the room is.
    private var otherCurrentScene: HomeScene? {
        guard let scenes = model.scenes.value, let current = scenes.current,
              !favoriteIDs.contains(current) else { return nil }
        return scenes.scenes.first { $0.entityID == current }
    }

    private var scenesPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                heading("Scenes", symbol: "sparkles")
                if let other = otherCurrentScene {
                    Text("Current scene: \(other.name)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            if let scenes = model.scenes.value, !scenes.scenes.isEmpty {
                let favorites = favoriteIDs.compactMap { id in scenes.scenes.first { $0.entityID == id } }
                if favorites.isEmpty {
                    SceneFocusMenu(model: model, scenes: scenes, title: "Scene").frame(minHeight: 76)
                } else {
                    HStack(spacing: 12) {
                        ForEach(favorites) { SceneTile(model: model, scene: $0, lit: $0.entityID == scenes.current, large: true) }
                    }
                }
            } else {
                Text("No scenes yet. Make one in Home Assistant (Settings › Automations & Scenes › Scenes) and it shows up here.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .roomPanel(fill: false)
    }

    private func heading(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(JarvisTheme.cyan)
    }
}

private extension View {
    func roomPanel(fill: Bool = false) -> some View {
        padding(20)
            .frame(maxWidth: .infinity, maxHeight: fill ? .infinity : nil, alignment: .topLeading)
            .jarvisSurface(in: .rect(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).strokeBorder(JarvisTheme.border))
    }
}

/// Movie scene: the panel steps back. A big dim clock, the TV's playback and
/// volume, the bedroom light for a snack run, the full remote, and a way
/// back to the normal panel (the scene keeps running). Dark and still: no
/// pulsing, nothing bright.
private struct MovieModeView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: HubViewModel
    let showRemote: () -> Void
    let backToPanel: () -> Void
    @State private var tick = 0

    private var bedroom: HomeEntity? {
        model.entities.value?.first { $0.entityID == "light.bedroom_lights" }
    }

    var body: some View {
        VStack(spacing: 28) {
            HStack {
                Label("Movie night", systemImage: "film")
                    .font(.headline)
                    .foregroundStyle(JarvisTheme.amber.opacity(0.8))
                Spacer()
                Button(action: backToPanel) {
                    Label("Back to panel", systemImage: "square.grid.2x2")
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 16).padding(.vertical, 10)
                        .background(Color.white.opacity(0.06), in: .capsule)
                }
                .buttonStyle(CockpitButtonStyle())
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            TimelineView(.everyMinute) { context in
                Text(context.date, format: .dateTime.hour().minute())
                    .font(.system(size: 120, weight: .ultraLight, design: .rounded))
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.5)
                    .foregroundStyle(.white.opacity(0.55))
            }
            HStack(spacing: 22) {
                control("gobackward", "Rewind", key: "rewind")
                control("playpause.fill", "Play or pause", key: "play_pause", big: true)
                control("goforward", "Fast forward", key: "fast_forward")
            }
            HStack(spacing: 22) {
                control("speaker.minus", "Volume down", key: "volume_down", repeats: true)
                control("speaker.slash", "Mute", key: "mute")
                control("speaker.plus", "Volume up", key: "volume_up", repeats: true)
            }
            Spacer(minLength: 0)
            if let state = model.lightSync.value, let calibration = state.calibrating {
                calibrationPanel(state, calibration)
            }
            HStack(spacing: 14) {
                if let bedroom {
                    let on = model.pendingEntityStates[bedroom.entityID] ?? bedroom.isOn
                    wide(on ? "Bedroom light off" : "Bedroom light on", symbol: on ? "lightbulb.slash" : "lightbulb") {
                        model.toggle(bedroom, to: !on)
                    }
                }
                lightSyncSwitch
                lightSyncMenu
                wide("TV remote", symbol: "appletvremote.gen4", action: showRemote)
            }
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black.opacity(0.82).ignoresSafeArea())
        .sensoryFeedback(.impact(weight: .light), trigger: tick)
        .onAppear { model.wantsLightSync = true }
        .onDisappear { model.wantsLightSync = false }
        // The home stream says when light sync changes; this is the fallback.
        .task {
            while !Task.isCancelled {
                await model.refreshLightSync()
                try? await Task.sleep(for: .seconds(model.homeStreamLive ? 30 : 5))
            }
        }
    }

    /// Calibrating with the flash test clip: nudge the global timing by eye
    /// (±25 ms a press), see where it stands, Done. Still and dim, like the
    /// rest of the movie screen.
    private func calibrationPanel(_ state: LightSyncState, _ calibration: LightSyncState.Calibration) -> some View {
        let step = state.offset?.stepMs ?? 25
        let building = calibration.phase == "building"
        return VStack(spacing: 14) {
            HStack {
                Label(building ? "Calibrating · building the test track…" : "Calibrating · \(calibration.title)",
                      systemImage: "slider.horizontal.below.rectangle")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(JarvisTheme.cyan.opacity(0.8))
                Spacer()
                Text("Global \(LightSyncState.milliseconds(state.offset?.globalMs ?? 0))")
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.75))
                    .contentTransition(.numericText())
                    .accessibilityLabel("Global timing \(state.offset?.globalMs ?? 0) milliseconds")
            }
            HStack(spacing: 14) {
                wide("Lights earlier", symbol: "backward.frame") { nudge(step) }
                wide("Lights later", symbol: "forward.frame") { nudge(-step) }
                wide("Done", symbol: "checkmark") { model.setLightSync(calibrate: false) }
            }
        }
        .padding(18)
        .background(Color.white.opacity(0.04), in: .rect(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(JarvisTheme.cyan.opacity(0.2)))
    }

    private func nudge(_ ms: Int) {
        tick += 1
        model.setLightSync(nudgeMs: ms)
    }

    /// Light sync's extras beside the switch: intensity, timing, a track
    /// for this film, the build queue, calibration.
    private var lightSyncMenu: some View {
        let state = model.lightSync.value
        let running = state?.running == true
        return Menu {
            if let state {
                if state.calibrating != nil {
                    Button { model.setLightSync(calibrate: false) } label: {
                        Label("Done calibrating", systemImage: "checkmark")
                    }
                }
                if running, state.calibrating == nil, let offset = state.offset {
                    let step = offset.stepMs
                    Section("Timing · \(offset.filmTitle ?? "this film") \(LightSyncState.milliseconds(offset.filmMs))") {
                        Button { model.setLightSync(nudgeMs: step) } label: {
                            Label("Lights earlier (\(step) ms)", systemImage: "backward.frame")
                        }
                        Button { model.setLightSync(nudgeMs: -step) } label: {
                            Label("Lights later (\(step) ms)", systemImage: "forward.frame")
                        }
                        Button { model.setLightSync(resetOffset: true) } label: {
                            Label("Reset timing", systemImage: "arrow.counterclockwise")
                        }
                        .disabled(offset.filmMs == 0)
                    }
                }
                Section("Intensity") {
                    ForEach(LightSyncState.intensities, id: \.self) { level in
                        Button { model.setLightSync(intensity: level) } label: {
                            if state.intensity == level {
                                Label(level.capitalized, systemImage: "checkmark")
                            } else {
                                Text(level.capitalized)
                            }
                        }
                    }
                }
                let queued = state.queue ?? []
                if let build = state.building {
                    Button {} label: { Text("Building \(build.title)… \(build.percent)%") }.disabled(true)
                }
                ForEach(queued) { film in
                    Button {} label: { Text("Next: \(film.title)") }.disabled(true)
                }
                if let film = state.film, !state.hasTrack, let key = film.ratingKey,
                   state.building?.title != film.title, !queued.contains(where: { $0.ratingKey == key }) {
                    Button { model.buildLightSync(ratingKey: key) } label: {
                        Label("Make a light sync for \(film.title)", systemImage: "wand.and.stars")
                    }
                }
                if state.calibrating == nil {
                    Button { model.setLightSync(calibrate: true) } label: {
                        Label("Calibrate…", systemImage: "slider.horizontal.below.rectangle")
                    }
                }
            } else {
                Button {} label: { Text("Light sync isn't answering") }.disabled(true)
            }
        } label: {
            Image(systemName: "ellipsis")
                .font(.headline)
                .frame(width: 56, height: 56)
                .foregroundStyle(running ? JarvisTheme.cyan.opacity(0.8) : .white.opacity(0.7))
                .background(Color.white.opacity(0.06), in: .rect(cornerRadius: 16))
        }
        .menuStyle(.button)
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel("Light sync options")
    }

    /// Light sync on/off. On: sync films that have a light track (this one
    /// now, if it has one). Off: stop and leave the Movie scene's lights.
    /// The ellipsis beside it holds intensity, timing, making a track and
    /// calibration.
    private var lightSyncSwitch: some View {
        let state = model.lightSync.value
        let running = state?.running == true
        let on = running || state?.auto == true
        let detail: String = {
            guard let state else { return "Not answering" }
            if state.calibrating != nil { return "Calibrating" }
            if running { return state.film?.title ?? "Syncing" }
            if !on { return "Off" }
            if let film = state.film { return state.hasTrack ? film.title : "None for this film" }
            return "Waiting for a film"
        }()
        return Toggle(isOn: Binding(get: { on }, set: { want in
            tick += 1
            if want {
                model.setLightSync(action: state?.hasTrack == true && !running ? "start" : nil, auto: true)
            } else {
                model.setLightSync(action: running ? "stop" : nil, auto: false)
            }
        })) {
            VStack(alignment: .leading, spacing: 2) {
                Label("Light sync", systemImage: "sparkles")
                    .font(.headline)
                    .foregroundStyle(on ? JarvisTheme.cyan.opacity(0.8) : .white.opacity(0.7))
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .tint(JarvisTheme.cyan.opacity(0.7))
        .disabled(state == nil || state?.calibrating != nil)
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, minHeight: 56)
        .background(Color.white.opacity(0.06), in: .rect(cornerRadius: 16))
        .accessibilityLabel("Light sync")
        .accessibilityValue(on ? "On, \(detail)" : "Off")
    }

    private func control(_ symbol: String, _ label: String, key: String,
                         big: Bool = false, repeats: Bool = false) -> some View {
        Button { press(key) } label: {
            Image(systemName: symbol)
                .font(big ? .largeTitle : .title2)
                .frame(width: big ? 104 : 80, height: big ? 104 : 80)
                .foregroundStyle(JarvisTheme.cyan.opacity(0.8))
                .background(Color.white.opacity(0.05), in: .circle)
                .overlay(Circle().strokeBorder(JarvisTheme.cyan.opacity(0.25)))
        }
        .buttonStyle(CockpitButtonStyle())
        .buttonRepeatBehavior(repeats ? .enabled : .disabled)
        .accessibilityLabel(label)
    }

    private func wide(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .font(.headline)
                .frame(maxWidth: .infinity, minHeight: 56)
                .foregroundStyle(.white.opacity(0.7))
                .background(Color.white.opacity(0.06), in: .rect(cornerRadius: 16))
        }
        .buttonStyle(CockpitButtonStyle())
    }

    private func press(_ key: String) {
        tick += 1
        Task {
            do {
                try await JarvisAPIClient.make(from: settings).pressTVRemote(key)
            } catch {
                model.actionError = "The TV didn't get that: \(error.localizedDescription)"
            }
        }
    }
}

/// The iPad's battery, since full screen hides the status bar.
struct BatteryBadge: View {
    @State private var level: Float = -1  // -1 when unknown
    @State private var charging = false

    var body: some View {
        Group {
            if level >= 0 {
                HStack(spacing: 4) {
                    Image(systemName: charging ? "battery.100.bolt" : Self.symbol(level))
                    Text("\(Int((level * 100).rounded()))%").monospacedDigit()
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(level < 0.2 && !charging ? JarvisTheme.amber : .secondary)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Battery \(Int((level * 100).rounded())) percent\(charging ? ", charging" : "")")
            }
        }
        // iOS posts these the moment the level (in its 5% steps) or the
        // charger changes, so no polling and no lag.
        .onAppear { UIDevice.current.isBatteryMonitoringEnabled = true; read() }
        .onReceive(NotificationCenter.default.publisher(for: UIDevice.batteryLevelDidChangeNotification)) { _ in read() }
        .onReceive(NotificationCenter.default.publisher(for: UIDevice.batteryStateDidChangeNotification)) { _ in read() }
    }

    private func read() {
        let device = UIDevice.current
        level = device.batteryLevel
        charging = device.batteryState == .charging || device.batteryState == .full
    }

    private static func symbol(_ level: Float) -> String {
        switch level {
        case 0.88...: return "battery.100"
        case 0.62...: return "battery.75"
        case 0.37...: return "battery.50"
        case 0.12...: return "battery.25"
        default: return "battery.0"
        }
    }
}

/// The Room tab's settings, out of the way behind "Room settings".
private struct RoomSettingsView: View {
    @ObservedObject var model: HubViewModel
    let wakeLightChoices: [HomeEntity]
    @Environment(\.dismiss) private var dismiss
    @AppStorage("room.keepAwake") private var keepAwake = false
    @AppStorage("room.nightScreen") private var nightScreen = true
    @AppStorage("room.fullScreen") private var fullScreen = false
    @AppStorage("room.wakeLight") private var wakeLight = "light.bedroom_lights"
    @AppStorage(RoomView.autoReturnKey) private var autoReturn = true
    @AppStorage(RoomView.favoritesKey) private var favoriteScenes = RoomView.defaultFavorites
    @AppStorage(PadRole.storageKey) private var padRoleRaw = ""
    @AppStorage(RoomView.nightClockKey) private var nightClock = true
    @AppStorage(RoomView.darkClockKey) private var darkClockLevel = NightstandDarkClock.defaultLevel
    @AppStorage(RoomView.nightstandWakeLightKey) private var nightstandWakeLight = ""

    private var nightstand: Bool { PadRole.resolve(padRoleRaw) == .nightstand }
    @AppStorage(SatelliteVoice.settingKey) private var satelliteSetting = ""
    @AppStorage(SatelliteVoice.trimKey) private var satelliteTrim = 1.0
    @ObservedObject private var satellite = SatelliteVoice.shared

    private var favorites: [String] {
        favoriteScenes.split(separator: ",").map(String.init)
    }

    var body: some View {
        NavigationStack {
            Form {
                if !RootView.isMac {
                    Section {
                        Picker("This iPad", selection: $padRoleRaw) {
                            Text("Automatic (\(PadRole.automatic.title))").tag("")
                            ForEach(PadRole.allCases) { Text($0.title).tag($0.rawValue) }
                        }
                    } header: {
                        Text("Where it lives")
                    } footer: {
                        Text("Nightstand: its own dark bedside screen, a big dim clock whenever every light is off, movie mode and the camera's Bed angle. Door: the room panel and the camera's Door angle; during a movie it stays the panel, dimmed.")
                    }
                    Section {
                        Toggle("JARVIS listens here", isOn: Binding(
                            get: { SatelliteVoice.enabled(stored: satelliteSetting, role: PadRole.resolve(padRoleRaw)) },
                            set: { on in
                                let byRole = SatelliteVoice.enabled(stored: "", role: PadRole.resolve(padRoleRaw))
                                satelliteSetting = on == byRole ? "" : (on ? "on" : "off")
                            }))
                        if SatelliteVoice.enabled(stored: satelliteSetting, role: PadRole.resolve(padRoleRaw)) {
                            LabeledContent("Match the desk") {
                                Slider(value: $satelliteTrim, in: 0.5...2, step: 0.05)
                                    .frame(maxWidth: 260)
                            }
                        }
                    } header: {
                        Text("Mic and speaker")
                    } footer: {
                        Text("\(satellite.status.text). This iPad hears \u{201C}Hey JARVIS\u{201D} for the room, and plays everything JARVIS says together with the desk speaker, at the same loudness. Keep this iPad's volume up and use Match the desk if it sounds louder or quieter than the desk. Works while the app is open.")
                    }
                }
                Section("Screen") {
                    Toggle("Full screen", isOn: $fullScreen)
                    Toggle("Keep screen on", isOn: $keepAwake)
                    if nightstand {
                        Toggle("Dark when every light is off", isOn: $nightScreen)
                        Toggle("Big clock on the dark screen", isOn: $nightClock)
                        if nightClock {
                            Picker("Dark clock", selection: $darkClockLevel) {
                                ForEach(NightstandDarkClock.levels, id: \.value) { Text($0.name).tag($0.value) }
                            }
                        }
                    } else {
                        Toggle("Dark at night when the lights are off", isOn: $nightScreen)
                    }
                    if !wakeLightChoices.isEmpty {
                        Picker("Tap the dark screen to turn on", selection: nightstand ? $nightstandWakeLight : $wakeLight) {
                            Text("Just the screen").tag("")
                            ForEach(wakeLightChoices) { Text($0.friendlyName).tag($0.entityID) }
                        }
                    }
                    Toggle("Back to Room after 3 min on another tab", isOn: $autoReturn)
                }
                NightVolumePanel(inForm: true)
                Section {
                    ThemePicker()
                } header: {
                    Text("Theme")
                } footer: {
                    Text("Shared with your phone and the TV, the same as Settings › Theme, without leaving full screen.")
                }
                if let scenes = model.scenes.value?.scenes, !scenes.isEmpty {
                    Section {
                        ForEach(scenes.filter { $0.entityID != "scene.peace" }) { scene in
                            Button { toggleFavorite(scene.entityID) } label: {
                                HStack {
                                    Label(scene.name, systemImage: scene.symbol).foregroundStyle(.primary)
                                    Spacer()
                                    if favorites.contains(scene.entityID) {
                                        Image(systemName: "checkmark").foregroundStyle(JarvisTheme.cyan)
                                    }
                                }
                            }
                        }
                    } header: {
                        Text("Scene tiles")
                    } footer: {
                        Text("Up to three get a tile on the panel; the rest are for \"Hey JARVIS\". Peace has its own button.")
                    }
                }
            }
            .tint(JarvisTheme.cyan)
            .navigationTitle("Room settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }

    private func toggleFavorite(_ id: String) {
        var list = favorites
        if let index = list.firstIndex(of: id) {
            list.remove(at: index)
        } else if list.count < 3 {
            list.append(id)
        } else {
            list[list.count - 1] = id
        }
        favoriteScenes = list.joined(separator: ",")
    }
}

/// RoomView's movie-mode wake/brightness rules, split out so the main view's
/// modifier chain stays type-checkable.
private struct MovieModeEffects: ViewModifier {
    let room: RoomView

    func body(content: Content) -> some View {
        content
            .onChange(of: room.movieDims) { _, on in
                UIApplication.shared.isIdleTimerDisabled = room.keepAwake || on || room.padRole == .nightstand
                if !on { room.setMovieDim(nil) }
            }
            .onChange(of: room.scenePhase) { _, phase in if phase != .active { room.setMovieDim(nil) } }
            // The lens coming on mid-movie lights the screen like a tap would.
            .onChange(of: room.cameraWatching) { _, on in if on && room.movieDims { room.movieTouches &+= 1 } }
            // Movie (the remote on the nightstand, the panel by the door): a
            // tap lights the screen fully, 20 s idle drops it to 15%.
            .task(id: "\(room.movieDims)-\(room.movieTouches)-\(room.scenePhase == .active)") {
                guard room.movieDims, room.scenePhase == .active else { return }
                if room.movieTouches > 0 { room.setMovieDim(1.0) }
                try? await Task.sleep(for: .seconds(20))
                if !Task.isCancelled, room.movieDims { room.setMovieDim(0.15) }
            }
    }
}

private struct NotePath: Identifiable {
    let id: String
}

/// The Room menu's memory screens: the maps full screen, Import and an
/// opened note as sheets.
private struct RoomMemoryCovers: ViewModifier {
    @Binding var cover: RoomView.RoomCover?
    @Binding var showingImport: Bool
    @Binding var openNote: String?

    func body(content: Content) -> some View {
        content
            .fullScreenCover(item: $cover) { cover in
                switch cover {
                case .memoryMap:
                    // After the map has finished closing: a sheet can't open mid-dismiss.
                    MemoryMapView { path in DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { openNote = path } }
                case .linkMap: LinkMapView()
                case .agentMap: AgentMapView()
                }
            }
            .sheet(isPresented: $showingImport) { ImportMemoryView() }
            .sheet(item: Binding(get: { openNote.map(NotePath.init) }, set: { openNote = $0?.id })) { note in
                NavigationStack {
                    NoteDetail(path: note.id)
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { openNote = nil } } }
                }
            }
    }
}

/// Tells `VoicePresence` whether the Room shows the conversation itself (on
/// screen, nothing over it) so its overlay window stays away.
private struct RoomVoiceHost: ViewModifier {
    let voice: VoicePresence
    let covered: Bool
    @State private var onScreen = false
    @Environment(\.scenePhase) private var scenePhase

    func body(content: Content) -> some View {
        content
            .onAppear { onScreen = true }
            .onDisappear { onScreen = false }
            .onChange(of: onScreen && !covered && scenePhase == .active && !RootView.isMac, initial: true) { _, inline in
                voice.setInlineHost(inline)
                AnswerCardPresenter.shared.setInlineHost(inline)
            }
    }
}
