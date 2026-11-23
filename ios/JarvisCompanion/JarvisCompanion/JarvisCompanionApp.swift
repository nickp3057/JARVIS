import SwiftUI

@main
struct JarvisCompanionApp: App {
    @UIApplicationDelegateAdaptor(PushAppDelegate.self) private var pushDelegate
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var settings: AppSettings
    @StateObject private var chat: ChatViewModel
    @StateObject private var hub: HubViewModel

    init() {
        let settings = AppSettings()
        _settings = StateObject(wrappedValue: settings)
        _chat = StateObject(wrappedValue: ChatViewModel(settings: settings))
        _hub = StateObject(wrappedValue: HubViewModel(
            settings: settings,
            academicNotifications: AcademicNotificationScheduler()
        ))
        PushRegistrar.shared.attach(settings)
        ServerRoute.shared.start(settings)  // home or away, picked on its own
    }

    var body: some Scene {
        WindowGroup {
            RootView(chat: chat, hub: hub)
                .environmentObject(settings)
                // onChange(of: scenePhase) misses the first .active at launch.
                .task { VoicePresence.shared.start(settings) }
                .onChange(of: scenePhase) { _, phase in
                    switch phase {
                    case .active:
                        ServerRoute.shared.reevaluate()
                        chat.appBecameActive()
                        if settings.isConfigured { Task { await hub.refreshNow() } }
                        Task { await PushRegistrar.shared.refresh() }
                        VoicePresence.shared.start(settings)  // iPad: "Hey JARVIS" overlay
                    case .inactive, .background:
                        chat.appBecameInactive()
                        if phase == .background { VoicePresence.shared.stop() }
                    @unknown default:
                        break
                    }
                }
                .onChange(of: settings.connectionIdentity) {
                    VoicePresence.shared.stop()
                    VoicePresence.shared.start(settings)
                    PushRegistrar.shared.connectionChanged()
                    Task { await PushRegistrar.shared.refresh() }
                }
        }
        .commands { RootTabCommands() }
    }
}
