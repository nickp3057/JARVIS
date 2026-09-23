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
    }

    var body: some Scene {
        WindowGroup {
            RootView(chat: chat, hub: hub)
                .environmentObject(settings)
                .onChange(of: scenePhase) { _, phase in
                    switch phase {
                    case .active:
                        chat.appBecameActive()
                        if settings.isConfigured { Task { await hub.refreshNow() } }
                        Task { await PushRegistrar.shared.refresh() }
                    case .inactive, .background:
                        chat.appBecameInactive()
                    @unknown default:
                        break
                    }
                }
                .onChange(of: settings.connectionIdentity) {
                    PushRegistrar.shared.connectionChanged()
                    Task { await PushRegistrar.shared.refresh() }
                }
        }
    }
}
