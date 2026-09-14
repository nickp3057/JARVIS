import Foundation
import UserNotifications

@MainActor
protocol AcademicNotificationScheduling {
    func sync(events: [AcademicEvent], enabled: Bool, hour: Int) async
}

/// One study brief per day. Reconcile changed requests without tearing down
/// the queue on every Hub poll. Failed calendar fetches never call sync.
@MainActor
final class AcademicNotificationScheduler: AcademicNotificationScheduling {
    private let center: UNUserNotificationCenter
    private let calendar: Calendar
    private static let prefix = "jarvis.academic."
    private var syncing = false
    private var pendingSync: (events: [AcademicEvent], enabled: Bool, hour: Int)?

    struct Brief: Equatable {
        let id: String
        let date: Date
        let body: String
    }

    init(center: UNUserNotificationCenter = .current(), calendar: Calendar = .autoupdatingCurrent) {
        self.center = center
        self.calendar = calendar
    }

    func sync(events: [AcademicEvent], enabled: Bool = true, hour: Int = 9) async {
        pendingSync = (events, enabled, hour)
        guard !syncing else { return }
        syncing = true
        defer { syncing = false }
        // Permission prompts and notification writes suspend. Keep the latest
        // preference change so disabling alerts during either is not lost.
        while let request = pendingSync {
            pendingSync = nil
            await reconcile(events: request.events, enabled: request.enabled, hour: request.hour)
        }
    }

    private func reconcile(events: [AcademicEvent], enabled: Bool, hour: Int) async {
        let briefs = enabled ? Self.briefs(events: events, now: Date(), calendar: calendar, hour: hour) : []
        if !briefs.isEmpty {
            var settings = await center.notificationSettings()
            if settings.authorizationStatus == .notDetermined {
                _ = try? await center.requestAuthorization(options: [.alert, .sound])
                settings = await center.notificationSettings()
            }
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
        }
        let pending = await center.pendingNotificationRequests()
        guard !Task.isCancelled else { return }
        let desiredIDs = Set(briefs.map(\.id))
        center.removePendingNotificationRequests(withIdentifiers: pending
            .map(\.identifier).filter { $0.hasPrefix(Self.prefix) && !desiredIDs.contains($0) })
        for brief in briefs {
            let components = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: brief.date)
            if let existing = pending.first(where: { $0.identifier == brief.id }),
               existing.content.body == brief.body,
               let trigger = existing.trigger as? UNCalendarNotificationTrigger,
               trigger.dateComponents == components { continue }
            guard !Task.isCancelled else { return }
            let content = UNMutableNotificationContent()
            content.title = "Your study brief"
            content.body = brief.body
            content.sound = .default
            content.threadIdentifier = "jarvis.study"
            try? await center.add(UNNotificationRequest(
                identifier: brief.id, content: content,
                trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
            ))
        }
    }

    /// Groups assessments into a single notification for each morning.
    static func briefs(events: [AcademicEvent], now: Date, calendar: Calendar, hour: Int) -> [Brief] {
        let today = calendar.startOfDay(for: now)
        let upcoming = events.compactMap { event -> (AcademicEvent, Date)? in
            guard let timestamp = event.startAt else { return nil }
            let date = Date(timeIntervalSince1970: timestamp)
            guard event.allDay ? calendar.startOfDay(for: date) >= today : date > now else { return nil }
            return (event, date)
        }.sorted { $0.1 < $1.1 }
        return (0..<48).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: offset, to: today),
                  let fire = calendar.date(bySettingHour: min(23, max(0, hour)), minute: 0, second: 0, of: day),
                  fire > now else { return nil }
            let lines = upcoming.compactMap { event, date -> String? in
                let remaining = calendar.dateComponents([.day], from: day, to: calendar.startOfDay(for: date)).day ?? -1
                guard (0...14).contains(remaining), event.allDay || date >= fire else { return nil }
                return body(title: event.title, daysLeft: remaining)
            }
            guard !lines.isEmpty else { return nil }
            let suffix = lines.count > 4 ? "\n+\(lines.count - 4) more in your Hub." : ""
            return Brief(id: Self.prefix + String(Int(day.timeIntervalSince1970)), date: fire,
                         body: lines.prefix(4).joined(separator: "\n") + suffix)
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
