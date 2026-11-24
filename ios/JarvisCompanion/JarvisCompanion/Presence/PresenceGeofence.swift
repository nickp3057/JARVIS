import CoreLocation
import UIKit

/// Tells JARVIS Core when this iPhone leaves or reaches home, with nothing
/// for Nick to do (2026-11-24: "I don't even want to think about it").
///
/// iOS region monitoring watches a circle around home (Home Assistant's
/// zone.home from `GET /api/presence/zone`, at least 150 m so GPS at the
/// edge doesn't flap) and relaunches the app in the background on every
/// crossing, even when it was never opened or was swiped away. Each crossing
/// is posted to `POST /api/presence/phone`, which keeps the room quiet while
/// he's out. Needs location "Always" (asked once). iPhone only: the iPads and
/// the Mac stay home.
///
/// A crossing that can't be delivered (no signal at the edge) is kept and
/// sent on the next launch, crossing or foregrounding.
@MainActor
final class PresenceGeofence: NSObject, CLLocationManagerDelegate {
    static let shared = PresenceGeofence()

    nonisolated private static let regionID = "jarvis.home"
    private static let zoneKey = "jarvis.presence.zone"
    private static let pendingKey = "jarvis.presence.pending"
    private static let minimumRadius: CLLocationDistance = 150

    private let manager = CLLocationManager()
    private weak var settings: AppSettings?
    private var attached = false

    private var isPhone: Bool {
        !ProcessInfo.processInfo.isMacCatalystApp && UIDevice.current.userInterfaceIdiom == .phone
    }

    /// Called from the App's init, so a background relaunch for a crossing
    /// has its delegate in place before iOS hands the event over.
    func attach(_ settings: AppSettings) {
        self.settings = settings
        guard isPhone, !attached else { return }
        attached = true
        manager.delegate = self
    }

    /// Launch and every foregrounding: ask for permission once, refresh the
    /// home circle, and send anything that didn't get through.
    func refresh() async {
        guard isPhone, let settings, settings.isConfigured else { return }
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestAlwaysAuthorization()  // the answer comes back through the delegate
            return
        case .authorizedWhenInUse:
            manager.requestAlwaysAuthorization()  // iOS offers the upgrade to Always once
        default:
            break
        }
        await sendPending()
        if let zone = try? await JarvisAPIClient.make(from: settings).homeZone() {
            if let data = try? JSONEncoder().encode(zone) { UserDefaults.standard.set(data, forKey: Self.zoneKey) }
        }
        startMonitoring()
    }

    private var savedZone: JarvisAPIClient.HomeZone? {
        guard let data = UserDefaults.standard.data(forKey: Self.zoneKey) else { return nil }
        return try? JSONDecoder().decode(JarvisAPIClient.HomeZone.self, from: data)
    }

    private func startMonitoring() {
        guard manager.authorizationStatus == .authorizedAlways,
              CLLocationManager.isMonitoringAvailable(for: CLCircularRegion.self),
              let zone = savedZone else { return }
        let radius = min(max(zone.radius, Self.minimumRadius), manager.maximumRegionMonitoringDistance)
        let center = CLLocationCoordinate2D(latitude: zone.latitude, longitude: zone.longitude)
        if let existing = manager.monitoredRegions.first(where: { $0.identifier == Self.regionID }) as? CLCircularRegion,
           existing.radius == radius, existing.center.latitude == center.latitude,
           existing.center.longitude == center.longitude {
            manager.requestState(for: existing)  // say where we are now, in case a crossing was missed
            return
        }
        let region = CLCircularRegion(center: center, radius: radius, identifier: Self.regionID)
        region.notifyOnEntry = true
        region.notifyOnExit = true
        manager.startMonitoring(for: region)  // didStartMonitoring asks for the current state
    }

    // MARK: Delivering a crossing

    private func crossed(_ place: String) {
        UserDefaults.standard.set(place, forKey: Self.pendingKey)
        // Woken in the background: ask for time to finish the request.
        var task = UIBackgroundTaskIdentifier.invalid
        task = UIApplication.shared.beginBackgroundTask(withName: "jarvis.presence") {
            UIApplication.shared.endBackgroundTask(task)
            task = .invalid
        }
        Task {
            for attempt in 0..<3 {
                if await self.sendPending() { break }
                // Leaving home, Wi-Fi drops mid-request; give cellular a moment.
                try? await Task.sleep(for: .seconds(attempt == 0 ? 3 : 8))
            }
            if task != .invalid { UIApplication.shared.endBackgroundTask(task) }
        }
    }

    /// True when nothing is left to send.
    @discardableResult
    private func sendPending() async -> Bool {
        guard let place = UserDefaults.standard.string(forKey: Self.pendingKey) else { return true }
        guard let settings, let client = try? JarvisAPIClient.make(from: settings) else { return false }
        do {
            try await client.reportPresence(place)
            if UserDefaults.standard.string(forKey: Self.pendingKey) == place {
                UserDefaults.standard.removeObject(forKey: Self.pendingKey)
            }
            return true
        } catch {
            ServerRoute.shared.reevaluate()  // the network just changed under us
            return false
        }
    }

    // MARK: CLLocationManagerDelegate (iOS calls these on the main thread: the manager was made there)

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            if manager.authorizationStatus == .authorizedWhenInUse { manager.requestAlwaysAuthorization() }
            await self.refresh()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didStartMonitoringFor region: CLRegion) {
        Task { @MainActor in manager.requestState(for: region) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didDetermineState state: CLRegionState,
                                     for region: CLRegion) {
        guard region.identifier == Self.regionID, state != .unknown else { return }
        Task { @MainActor in self.crossed(state == .inside ? "home" : "away") }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, monitoringDidFailFor region: CLRegion?,
                                     withError error: Error) {}

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {}
}
