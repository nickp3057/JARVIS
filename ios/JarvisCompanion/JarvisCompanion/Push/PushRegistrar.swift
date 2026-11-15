import SwiftUI
import UIKit
import UserNotifications

/// Registers this phone for JARVIS push alerts (APNs) and tells JARVIS Core
/// the device token via `POST /api/push/register`.
///
/// Registration happens on every launch/foreground (Apple's guidance: tokens
/// can change), and the token is re-sent whenever it or the server identity
/// changes. Notification permission is requested once a server is
/// configured, because push alerts are the reason to allow it.
@MainActor
final class PushRegistrar: ObservableObject {
    static let shared = PushRegistrar()

    @Published private(set) var status = "Push alerts not registered yet"
    private weak var settings: AppSettings?
    private var deviceToken: String?
    private var uploadedFingerprint: String?

    func attach(_ settings: AppSettings) {
        self.settings = settings
    }

    /// Ask for permission if still undetermined, then (re)register with APNs.
    func refresh() async {
        guard let settings, settings.isConfigured else { return }
        let center = UNUserNotificationCenter.current()
        if await center.notificationSettings().authorizationStatus == .notDetermined {
            _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
        }
        // A token is issued even without alert permission; alerts then just
        // stay silent, so always register.
        UIApplication.shared.registerForRemoteNotifications()
    }

    func didRegister(tokenData: Data) {
        deviceToken = tokenData.map { String(format: "%02x", $0) }.joined()
        Task { await upload() }
    }

    func didFail(_ error: Error) {
        status = "Push unavailable: \(error.localizedDescription)"
    }

    /// The server identity changed (new address or token): send the token again.
    func connectionChanged() {
        uploadedFingerprint = nil
        Task { await upload() }
    }

    private func upload() async {
        guard let settings, settings.isConfigured, let token = deviceToken else { return }
        let fingerprint = "\(settings.connectionIdentity)|\(token)"
        guard fingerprint != uploadedFingerprint else { return }
        do {
            try await JarvisAPIClient.make(from: settings)
                .registerPushDevice(token: token, environment: Self.apsEnvironment(), name: UIDevice.current.name)
            uploadedFingerprint = fingerprint
            status = "Push alerts on"
        } catch {
            status = "Push registration failed: \(error.localizedDescription)"
        }
    }

    /// "sandbox" for development-signed builds, "production" otherwise, read
    /// from the embedded provisioning profile's aps-environment. JARVIS Core
    /// also self-corrects on a BadDeviceToken, so a wrong guess only costs
    /// one retried request.
    static func apsEnvironment() -> String {
        #if targetEnvironment(simulator)
        return "sandbox"
        #else
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let raw = try? Data(contentsOf: url),
              let text = String(data: raw, encoding: .isoLatin1),
              let start = text.range(of: "<?xml"),
              let end = text.range(of: "</plist>"),
              let xml = String(text[start.lowerBound..<end.upperBound]).data(using: .isoLatin1),
              let plist = try? PropertyListSerialization.propertyList(from: xml, format: nil) as? [String: Any],
              let entitlements = plist["Entitlements"] as? [String: Any],
              let aps = entitlements["aps-environment"] as? String
        else { return "production" }  // App Store / TestFlight builds carry no profile
        return aps == "development" ? "sandbox" : "production"
        #endif
    }
}

/// UIKit hooks SwiftUI doesn't expose: the APNs token callbacks, and showing
/// alerts while the app is in the foreground.
final class PushAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        // The wake-up push (category WAKE_UP) gets I'm up / Snooze / leave the lights buttons.
        let awake = UNNotificationAction(identifier: Self.wakeAwake, title: "I'm up", options: [])
        let snooze = UNNotificationAction(identifier: Self.wakeSnooze, title: "Snooze 9 min", options: [])
        let keep = UNNotificationAction(identifier: Self.wakeKeepLights, title: "I'm awake, leave the lights", options: [])
        UNUserNotificationCenter.current().setNotificationCategories([
            UNNotificationCategory(identifier: "WAKE_UP", actions: [awake, snooze, keep], intentIdentifiers: [])])
        return true
    }

    static let wakeAwake = "WAKE_AWAKE", wakeSnooze = "WAKE_SNOOZE", wakeKeepLights = "WAKE_KEEP_LIGHTS"

    /// A button on the wake-up push: tell JARVIS Core, which tells the TV.
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let action = response.actionIdentifier
        let content = response.notification.request.content
        if action == UNNotificationDefaultActionIdentifier, content.categoryIdentifier == "WAKE_UP" {
            // Tapped the push itself: open on the alarm screen.
            Task { @MainActor in
                WakeRinging.shared.detail = content.body
                WakeRinging.shared.open()
                completionHandler()
            }
            return
        }
        guard action == Self.wakeAwake || action == Self.wakeSnooze || action == Self.wakeKeepLights else {
            completionHandler()
            return
        }
        Task { @MainActor in
            if let client = try? JarvisAPIClient.make(from: AppSettings()) {
                if action == Self.wakeSnooze { try? await client.snoozeWakeUp() }
                else { try? await client.wakeUpAwake(lights: action != Self.wakeKeepLights) }
            }
            completionHandler()
        }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task { @MainActor in PushRegistrar.shared.didRegister(tokenData: deviceToken) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in PushRegistrar.shared.didFail(error) }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        let content = notification.request.content
        guard content.categoryIdentifier == "WAKE_UP" else {
            completionHandler([.banner, .list, .sound])
            return
        }
        // The alarm went off with the app open: straight to I'm up / Snooze.
        Task { @MainActor in
            WakeRinging.shared.detail = content.body
            WakeRinging.shared.open()
            completionHandler([.list, .sound])
        }
    }
}
