import Foundation
import UserNotifications

@MainActor
protocol AcademicNotificationScheduling {
    /// Ask iOS for permission right now. Used when the user turns the study
    /// brief on, so the prompt appears immediately instead of on the first
    /// morning that happens to have an assessment.
    func requestPermission() async -> Bool
    /// Reconcile the pending queue with the briefs JARVIS wants delivered.
    func sync(briefs: [StudyBriefEntry], enabled: Bool) async
}

/// One study brief per morning, written by JARVIS Core and registered here as
/// local notifications. Reconciles changed requests without tearing down the
/// queue on every Hub poll. A failed fetch never calls `sync`, so an outage
/// leaves the last good schedule in place.
@MainActor
final class AcademicNotificationScheduler: AcademicNotificationScheduling {
    private let center: UNUserNotificationCenter
    private static let prefix = "jarvis.academic."
    private var syncing = false
    private var pendingSync: (briefs: [StudyBriefEntry], enabled: Bool)?

    init(center: UNUserNotificationCenter = .current()) {
        self.center = center
    }

    func requestPermission() async -> Bool {
        var settings = await center.notificationSettings()
        if settings.authorizationStatus == .notDetermined {
            _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
            settings = await center.notificationSettings()
        }
        return Self.allowed(settings.authorizationStatus)
    }

    private static func allowed(_ status: UNAuthorizationStatus) -> Bool {
        status == .authorized || status == .provisional || status == .ephemeral
    }

    func sync(briefs: [StudyBriefEntry], enabled: Bool) async {
        pendingSync = (briefs, enabled)
        guard !syncing else { return }
        syncing = true
        defer { syncing = false }
        // Permission prompts and notification writes suspend. Keep the latest
        // preference change so disabling alerts during either is not lost.
        while let request = pendingSync {
            pendingSync = nil
            await reconcile(briefs: request.enabled ? request.briefs : [])
        }
    }

    private func reconcile(briefs: [StudyBriefEntry]) async {
        if !briefs.isEmpty {
            guard await requestPermission() else { return }
        }
        let pending = await center.pendingNotificationRequests()
        guard !Task.isCancelled else { return }
        let desired = Dictionary(briefs.map { (Self.prefix + $0.id, $0) }, uniquingKeysWith: { $1 })
        center.removePendingNotificationRequests(withIdentifiers: pending
            .map(\.identifier).filter { $0.hasPrefix(Self.prefix) && desired[$0] == nil })
        let calendar = Calendar.autoupdatingCurrent
        for (identifier, brief) in desired.sorted(by: { $0.value.fireAt < $1.value.fireAt }) {
            let components = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: brief.fireDate)
            if let existing = pending.first(where: { $0.identifier == identifier }),
               existing.content.body == brief.body,
               let trigger = existing.trigger as? UNCalendarNotificationTrigger,
               trigger.dateComponents == components { continue }
            guard !Task.isCancelled else { return }
            let content = UNMutableNotificationContent()
            content.title = "Study brief"
            content.body = brief.body
            content.sound = .default
            content.threadIdentifier = "jarvis.study"
            try? await center.add(UNNotificationRequest(
                identifier: identifier, content: content,
                trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
            ))
        }
    }

    // MARK: - Local fallback

    /// Plans briefs on the phone from the Hub's countdown list. Only used when
    /// JARVIS Core predates `/api/study/brief`; otherwise the server's
    /// composed briefs are authoritative.
    static func briefs(events: [AcademicEvent], now: Date, calendar: Calendar, hour: Int) -> [StudyBriefEntry] {
        let today = calendar.startOfDay(for: now)
        let upcoming = events.compactMap { event -> (AcademicEvent, Date)? in
            guard let timestamp = event.startAt else { return nil }
            let date = Date(timeIntervalSince1970: timestamp)
            guard event.allDay ? calendar.startOfDay(for: date) >= today : date > now else { return nil }
            return (event, date)
        }.sorted { $0.1 < $1.1 }
        return (0..<30).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: offset, to: today),
                  let fire = calendar.date(bySettingHour: min(23, max(0, hour)), minute: 0, second: 0, of: day),
                  fire > now else { return nil }
            let items = upcoming.compactMap { event, date -> StudyBriefItem? in
                let remaining = calendar.dateComponents([.day], from: day, to: calendar.startOfDay(for: date)).day ?? -1
                guard (0...14).contains(remaining), event.allDay || date >= fire else { return nil }
                return StudyBriefItem(title: event.title, daysLeft: remaining)
            }
            guard !items.isEmpty else { return nil }
            let lines = items.prefix(4).map { body(title: $0.title, daysLeft: $0.daysLeft) }
            let suffix = items.count > 4 ? "\n+\(items.count - 4) more in your Hub." : ""
            return StudyBriefEntry(id: String(Int(day.timeIntervalSince1970)), fireAt: fire.timeIntervalSince1970,
                                   body: lines.joined(separator: "\n") + suffix, items: items)
        }
    }

    static func body(title: String, daysLeft: Int) -> String {
        switch daysLeft {
        case ...0: return "\(title) is today."
        case 1: return "\(title) is tomorrow."
        default: return "\(title) is in \(daysLeft) days."
        }
    }
}
