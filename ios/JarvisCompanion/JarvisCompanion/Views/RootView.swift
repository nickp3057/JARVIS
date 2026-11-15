import SwiftUI
import UIKit

/// Top-level shell: Media, Hub and Home (default, in the middle) are HubView tabs over one
/// shared HubViewModel, Chat keeps its whole NavigationStack, and Settings is
/// the only place settings live.
struct RootView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var chat: ChatViewModel
    @ObservedObject var hub: HubViewModel
    @State private var selection = RootView.isMac ? RootTab.desk : RootView.isPad ? RootTab.room : RootTab.home
    /// Horizontal offset of the visible tab while swiping, and while the new
    /// tab slides in. The tab bar itself never moves.
    @State private var swipeOffset: CGFloat = 0
    /// Re-identifies the tab content so every palette colour re-resolves.
    @AppStorage(JarvisPalette.storageKey) private var palette = JarvisPalette.arc.rawValue
    /// The palette the tabs are built with; follows `palette` inside an animation.
    @State private var shownPalette = JarvisPalette.current.rawValue
    /// True while a theme crossfade runs: a solid backdrop sits under it.
    @State private var fading = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @ObservedObject private var wakeRinging = WakeRinging.shared
    /// The iPad as the room camera: runs the lens while Nick's phone watches.
    @StateObject private var camera = RoomCameraController()
    @StateObject private var phoneVoice = PushToTalkController()
    /// The iPad as JARVIS's ears and voice in the room (SatelliteVoice).
    @AppStorage(SatelliteVoice.settingKey) private var satelliteSetting = ""
    @AppStorage(PadRole.storageKey) private var padRoleRaw = ""
    /// The phone's camera viewer is presented here, above `.id(palette)`, so a
    /// theme flip (Barn Door engaging) doesn't tear down the live feed.
    @ObservedObject private var cameraViewer = RoomCameraPresenter.shared
    /// Donna mode covers everything, tab bar included, until he leaves.
    @ObservedObject private var donna = DonnaPresenter.shared
    /// iPad: slide back to Room after a few idle minutes on another tab.
    @AppStorage(RoomView.autoReturnKey) private var autoReturn = true
    /// Bumped by any touch, so the trip back to Room waits for a quiet spell.
    @State private var touches = 0
    /// Room's full screen: the iPad stays on Room, no swiping to other tabs.
    @AppStorage("room.fullScreen") private var roomFullScreen = false

    /// No swipe to another tab: in Room's full screen, or while a control
    /// (the volume slider, a tile drag) has the finger (`SwipeLock`).
    private var swipeBlocked: Bool {
        (Self.isPad && !Self.isMac && selection == .room && roomFullScreen) || SwipeLock.active
    }

    /// Tab-bar (and swipe) order: Home sits in the middle. The iPad adds Room
    /// first (its default): the tablet is mainly a panel for the room. The
    /// Mac swaps Room and Home for Desk, everything on one screen.
    enum RootTab: Hashable, CaseIterable { case room, desk, media, hub, home, chat, knowledge, settings }

    /// True on the Mac too (Mac Catalyst runs the iPad layout, Room tab included).
    static let isPad = UIDevice.current.userInterfaceIdiom == .pad
    /// The laptop app: no night screen, no wall-panel habits.
    static let isMac = ProcessInfo.processInfo.isMacCatalystApp
    static let tabsInOrder = tabs
    private static let tabs = RootTab.allCases.filter { tab in
        switch tab {
        case .room: return isPad && !isMac
        case .desk: return isMac
        case .home: return !isMac
        case .knowledge: return isPad // the phone opens it from Settings
        default: return true
        }
    }

    var body: some View {
        ZStack {
        if fading {
            // Under the crossfade, so the two screens never let black through
            // (that was the dim dip mid-change). Only there while it runs.
            JarvisBackdrop()
        }
        if !Self.isPad && !Self.isMac {
            PhoneTabs(selection: $selection, pages: phonePages, reduceMotion: reduceMotion)
                // UIKit owns safe areas and keyboard avoidance inside the
                // selected page; the tab shell must not shrink with it.
                .ignoresSafeArea()
                .id(shownPalette)
                .transition(.opacity)
        } else {
        TabView(selection: $selection) {
            if Self.isMac {
                HubView(model: hub, onAsk: ask, onNavigate: navigate, onSend: sendNow, focus: .desk)
                    .swipePage(swipeOffset)
                    .tabItem { Label("Desk", systemImage: "rectangle.3.group") }
                    .tag(RootTab.desk)
            }

            if Self.isPad && !Self.isMac {
                RoomView(model: hub)
                    .swipePage(swipeOffset)
                    .tabItem { Label("Room", systemImage: "lightbulb.2") }
                    .tag(RootTab.room)
            }

            HubView(model: hub, onAsk: ask, focus: .media)
                .swipePage(swipeOffset)
                .tabItem { Label("Media", systemImage: "play.rectangle.on.rectangle") }
                .tag(RootTab.media)

            HubView(model: hub, onAsk: ask, onNavigate: navigate, focus: .hub)
                .swipePage(swipeOffset)
                .tabItem { Label("Control", systemImage: "switch.2") }
                .tag(RootTab.hub)

            if !Self.isMac {
                HubView(model: hub, onAsk: ask, onNavigate: navigate, focus: .home)
                    .swipePage(swipeOffset)
                    .tabItem { Label("Home", systemImage: "house") }
                    .tag(RootTab.home)
            }

            ChatView(model: chat)
                .swipePage(swipeOffset)
                .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
                .tag(RootTab.chat)

            if Self.isPad {
                KnowledgeView()
                    .swipePage(swipeOffset)
                    .tabItem { Label("Memory", systemImage: "brain") }
                    .tag(RootTab.knowledge)
            }

            SettingsView(embedded: true, studyBrief: hub.studyBrief.value)
                .swipePage(swipeOffset)
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(RootTab.settings)
        }
        .id(shownPalette)
        // A theme change (Barn Door engaging or standing down, a pick on another
        // device) crossfades the old colours into the new instead of cutting.
        .transition(.opacity)
        }
        }
        .onChange(of: palette) { _, new in
            fading = !reduceMotion
            withAnimation(reduceMotion ? nil : .easeInOut(duration: 1.4)) {
                shownPalette = new
            } completion: {
                fading = false
            }
        }
        .tint(JarvisTheme.reactor)
        .overlay(alignment: .bottom) {
            if !Self.isPad && !Self.isMac && phoneVoice.showing {
                PushToTalkPanel(controller: phoneVoice)
            }
        }
        .overlay {
            // Answer screens ("what's the weather?") over whatever tab is up;
            // under Donna, who covers everything.
            AnswerCardOverlay()
        }
        .task(id: "\(scenePhase)-\(settings.connectionIdentity)") {
            // Follow JARVIS's answer cards while the app is open.
            guard scenePhase == .active, settings.isConfigured else { return }
            await AnswerCardPresenter.shared.follow(settings)
        }
        .overlay {
            if donna.showing {
                DonnaView(settings: settings)
                    .environmentObject(settings)
                    .transition(.opacity)
                    .zIndex(10)
            }
        }
        // iPad/Mac only: the tab swipe and the idle timer. On the phone these
        // ancestor gestures swallowed List row taps (Settings, Maintenance).
        .simultaneousGesture(DragGesture(minimumDistance: 30).onChanged(track).onEnded(swipe),
                             including: Self.isPad || Self.isMac ? .all : .subviews)
        .simultaneousGesture(TapGesture().onEnded { touches &+= 1 },
                             including: Self.isPad || Self.isMac ? .all : .subviews)
        .task(id: "\(selection)-\(touches)-\(autoReturn)") {
            // Chat is left alone: a half-typed message shouldn't be yanked away.
            guard Self.isPad, !Self.isMac, autoReturn, selection != .room, selection != .chat else { return }
            try? await Task.sleep(for: .seconds(180))
            if !Task.isCancelled, !wakeRinging.showing { selection = .room }
        }
        .sensoryFeedback(.selection, trigger: selection)
        .focusedSceneValue(\.rootTab, $selection)
        .onAppear(perform: sizeMacWindow)
        .onAppear { phoneVoice.configure(settings: settings, chat: chat) }
        .onChange(of: chat.conversationID) { _, id in phoneVoice.conversationChanged(id) }
        .fullScreenCover(isPresented: $wakeRinging.showing) { WakeRingingView().environmentObject(settings) }
        // While the iPad's lens is on, its reactors draw the green orb.
        .environment(\.cameraWatching, camera.watching)
        .fullScreenCover(isPresented: $cameraViewer.showing) { RoomCameraView().environmentObject(settings) }
        .task { camera.activate(settings: settings) }
        .onChange(of: hub.cameraWanted, initial: true) { _, wanted in camera.follow(wanted) }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { camera.activate(settings: settings) } else { camera.deactivate() }
            if phase != .active { phoneVoice.cancel() }
        }
        .task(id: scenePhase) {
            // Follow the shared theme while the app is open (a pick on the TV,
            // Barn Door engaging or standing down), not just on launch.
            guard scenePhase == .active else { return }
            // While the home stream is up it brings theme changes at once
            // (HubViewModel), so this only runs when it's down.
            await ThemeSync.pull(settings)
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(10))
                if !hub.homeStreamLive { await ThemeSync.pull(settings) }
            }
        }
        .task(id: "\(scenePhase)-\(satelliteSetting)-\(padRoleRaw)-\(settings.connectionIdentity)") {
            // An iPad lends Core's room voice its mic and speaker while the
            // app is open (on by default on the nightstand, off by the door).
            guard Self.isPad, !Self.isMac else { return }
            let role = PadRole.resolve(padRoleRaw)
            SatelliteVoice.shared.stop()
            if scenePhase == .active, SatelliteVoice.enabled(stored: satelliteSetting, role: role) {
                SatelliteVoice.shared.start(settings: settings, name: role.lens)
            }
        }
        .task(id: scenePhase) {
            // An alarm going off (or snoozed) opens the alarm screen whenever the
            // app is open, push or no push; answered anywhere, it closes.
            guard scenePhase == .active, let client = try? JarvisAPIClient.make(from: settings) else { return }
            while !Task.isCancelled {
                if let wake = try? await client.wakeSchedule() { wakeRinging.follow(wake.ringing) }
                try? await Task.sleep(for: .seconds(wakeRinging.showing ? 5 : 15))
            }
        }
        .onChange(of: settings.studyAlertsEnabled) { Task { await hub.updateNotificationPreferences() } }
        .onChange(of: selection) { _, tab in
            if tab == .chat { chat.refreshConnectionStatus() }
            if tab != .home && ["connecting", "listening", "finalizing"].contains(phoneVoice.phase) { phoneVoice.cancel() }
        }
    }

    private var phonePages: [AnyView] {
        // Same order as `PhoneTabs.tabs`.
        [AnyView(HubView(model: hub, onAsk: ask, focus: .media).environmentObject(settings)),
         AnyView(HubView(model: hub, onAsk: ask, onNavigate: navigate, focus: .hub).environmentObject(settings)),
         AnyView(HubView(model: hub, onAsk: ask, onNavigate: navigate, focus: .home, phoneVoice: phoneVoice).environmentObject(settings)),
         AnyView(ChatView(model: chat).environmentObject(settings)),
         AnyView(SettingsView(embedded: true, studyBrief: hub.studyBrief.value).environmentObject(settings))]
    }

    /// Mac: keep the window big enough for the Room tab's two columns.
    private func sizeMacWindow() {
        guard Self.isMac else { return }
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
            scene.sizeRestrictions?.minimumSize = CGSize(width: 900, height: 640)
        }
    }

    private func neighbour(towards dx: CGFloat) -> RootTab? {
        let tabs = Self.tabs
        guard let index = tabs.firstIndex(of: selection) else { return nil }
        let next = dx < 0 ? index + 1 : index - 1
        return tabs.indices.contains(next) ? tabs[next] : nil
    }

    /// The page follows the finger (rubber-banded at the first/last tab) only
    /// while the drag is clearly horizontal, so vertical scrolling is untouched.
    private func track(_ drag: DragGesture.Value) {
        guard Self.isPad || Self.isMac else { return }
        guard !reduceMotion, drag.startLocation.x > 24, !swipeBlocked else {
            if swipeOffset != 0 { withAnimation(.spring(duration: 0.25)) { swipeOffset = 0 } }
            return
        }
        let dx = drag.translation.width, dy = drag.translation.height
        guard abs(dx) > 2.5 * abs(dy) else {
            if swipeOffset != 0 { withAnimation(.spring(duration: 0.25)) { swipeOffset = 0 } }
            return
        }
        swipeOffset = neighbour(towards: dx) == nil ? dx * 0.15 : dx * 0.5
    }

    /// Horizontal swipe moves to the neighbouring tab, which slides in from
    /// the side the finger came from. It must be clearly horizontal and must
    /// not start at the left edge (that's the system back gesture inside
    /// pushed screens).
    private func swipe(_ drag: DragGesture.Value) {
        touches &+= 1
        guard Self.isPad || Self.isMac else { return }
        let dx = drag.translation.width, dy = drag.translation.height
        guard !swipeBlocked, drag.startLocation.x > 24, abs(dx) > 60, abs(dx) > 2.5 * abs(dy),
              let next = neighbour(towards: dx) else {
            withAnimation(.spring(duration: 0.3)) { swipeOffset = 0 }
            return
        }
        selection = next
        guard !reduceMotion else { return }
        swipeOffset = dx < 0 ? 140 : -140
        withAnimation(.spring(duration: 0.35, bounce: 0.12)) { swipeOffset = 0 }
    }

    /// Home's chips: slide over to Hub or Media (which scrolls to the card).
    private func navigate(_ focus: HubView.HubFocus) {
        let target: RootTab = focus == .media ? .media : focus == .hub ? .hub : focus == .desk ? .desk : .home
        guard target != selection, let from = Self.tabs.firstIndex(of: selection),
              let to = Self.tabs.firstIndex(of: target) else { return }
        selection = target
        guard (Self.isPad || Self.isMac), !reduceMotion else { return }
        swipeOffset = to > from ? 140 : -140
        withAnimation(.spring(duration: 0.35, bounce: 0.12)) { swipeOffset = 0 }
    }

    /// Desk's ask bar: straight into Chat and sent.
    private func sendNow(_ prompt: String) {
        chat.draft = prompt
        chat.draftChanged()
        selection = .chat
        chat.send()
    }

    /// Hub "ask" shortcuts drop the prompt into Chat's composer and switch there.
    /// (Programmatic switches don't slide; only a swipe does.)
    private func ask(_ prompt: String) {
        if chat.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            chat.draft = prompt
        } else {
            chat.draft += "\n" + prompt
        }
        chat.draftChanged()
        selection = .chat
    }
}

private extension View {
    /// Offset and gentle fade for the tab being swiped.
    func swipePage(_ offset: CGFloat) -> some View {
        self.offset(x: offset)
            .opacity(1 - min(abs(offset) / 500, 0.3))
    }
}

extension RootView.RootTab {
    var title: String {
        switch self {
        case .room: return "Room"
        case .desk: return "Desk"
        case .media: return "Media"
        case .hub: return "Control"
        case .home: return "Home"
        case .chat: return "Chat"
        case .knowledge: return "Memory"
        case .settings: return "Settings"
        }
    }
}

private struct RootTabKey: FocusedValueKey {
    typealias Value = Binding<RootView.RootTab>
}

extension FocusedValues {
    /// The front window's tab, for the menu bar's Go menu.
    var rootTab: Binding<RootView.RootTab>? {
        get { self[RootTabKey.self] }
        set { self[RootTabKey.self] = newValue }
    }
}

/// Mac (and iPad keyboard) menus: ⌘1… for the tabs, ⌘, for Settings.
struct RootTabCommands: Commands {
    @FocusedBinding(\.rootTab) private var tab

    var body: some Commands {
        CommandGroup(replacing: .appSettings) {
            Button("Settings…") { tab = .settings }
                .keyboardShortcut(",")
                .disabled(tab == nil)
        }
        CommandMenu("Go") {
            ForEach(Array(RootView.tabsInOrder.enumerated()), id: \.element) { index, item in
                Button(item.title) { tab = item }
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))))
                    .disabled(tab == nil)
            }
        }
    }
}
