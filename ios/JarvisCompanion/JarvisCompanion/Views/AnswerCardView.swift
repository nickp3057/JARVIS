import SwiftUI

/// Answer screens: when Nick asks JARVIS something with a visual shape
/// ("what's the weather?", "what's my day look like?", "what timers have I
/// got?"), Core puts a card on every screen (`brain/answer_cards.py`). This
/// follows Core's card stream while the app is open and shows the newest one
/// over whatever tab is up (the iPad's Room tab, the phone); it clears itself
/// after the card's time (a minute) or on a tap. One renderer per card type.
@MainActor
final class AnswerCardPresenter: ObservableObject {
    static let shared = AnswerCardPresenter()
    @Published private(set) var current: AnswerCard?
    /// Card ids already shown (a reconnect replays cards still on screen).
    private var seen: Set<String> = []
    private var expiry: Task<Void, Never>?

    func receive(_ card: AnswerCard) {
        guard card.payload != .unknown, seen.insert("\(card.id)@\(card.createdAt)").inserted else { return }
        if seen.count > 200 { seen.removeAll() }
        // Time left by Core's clock, clamped so a skewed clock still shows it a while.
        let left = min(card.ttl, max(8, card.ttl - (Date().timeIntervalSince1970 - card.createdAt)))
        current = card
        expiry?.cancel()
        expiry = Task { [weak self] in
            try? await Task.sleep(for: .seconds(left))
            guard !Task.isCancelled, self?.current?.id == card.id else { return }
            self?.dismiss()
        }
    }

    func dismiss() {
        expiry?.cancel()
        current = nil
    }

    /// Follows the stream until cancelled, reconnecting every few seconds.
    func follow(_ settings: AppSettings) async {
        while !Task.isCancelled {
            guard let client = try? JarvisAPIClient.make(from: settings) else { return }
            try? await client.streamCards { [weak self] card in self?.receive(card) }
            guard !Task.isCancelled else { return }
            try? await Task.sleep(for: .seconds(5))
        }
    }
}

/// The card over the app: a calm dim behind, the readout centred. Tap
/// anywhere to clear it.
struct AnswerCardOverlay: View {
    @ObservedObject var presenter = AnswerCardPresenter.shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            if let card = presenter.current {
                Color.black.opacity(0.35)
                    .ignoresSafeArea()
                    .transition(.opacity)
                AnswerCardView(card: card)
                    .padding(16)
                    .frame(maxWidth: 640)
                    .id(card.id)
                    .transition(reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.97)))
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.35), value: presenter.current?.id)
        .contentShape(Rectangle())
        .allowsHitTesting(presenter.current != nil)
        .onTapGesture { presenter.dismiss() }
    }
}

/// One card, in the app's panel style (follows the theme like `CockpitPanel`).
struct AnswerCardView: View {
    let card: AnswerCard

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 9) {
                Image(systemName: symbol)
                    .foregroundStyle(JarvisTheme.cyan)
                    .frame(width: 22)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text("JARVIS")
                        .font(.caption2.weight(.semibold))
                        .tracking(2.5)
                        .foregroundStyle(JarvisTheme.cyan)
                    Text(card.title)
                        .font(.headline)
                        .accessibilityAddTraits(.isHeader)
                }
                Spacer()
                Image(systemName: "xmark")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Close")
            }
            switch card.payload {
            case .weather(let weather): WeatherTodayCard(weather: weather)
            case .calendar(let day): CalendarDayCard(day: day)
            case .timers(let list): TimersCard(list: list, sentAt: Date(timeIntervalSince1970: card.createdAt))
            case .unknown: EmptyView()
            }
        }
        .padding(20)
        .jarvisSurface(in: .rect(cornerRadius: 24))
        .overlay {
            RoundedRectangle(cornerRadius: 24)
                .strokeBorder(LinearGradient(
                    colors: [JarvisTheme.cyan.opacity(0.35), JarvisTheme.border.opacity(0.35)],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
        }
        .shadow(color: .black.opacity(0.3), radius: 24, y: 8)
        .accessibilityElement(children: .contain)
    }

    private var symbol: String {
        switch card.payload {
        case .weather: return "cloud.sun"
        case .calendar: return "calendar"
        case .timers: return "timer"
        case .unknown: return "sparkles"
        }
    }
}

private func clock(_ epoch: Double, hourOnly: Bool = false) -> String {
    let date = Date(timeIntervalSince1970: epoch)
    return hourOnly ? date.formatted(.dateTime.hour()) : date.formatted(date: .omitted, time: .shortened)
}

/// Today's weather: now, high/low, the rest of the day hour by hour
/// (temperature and rain chance), sunrise/sunset, wind and humidity.
private struct WeatherTodayCard: View {
    let weather: AnswerCard.WeatherToday

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 16) {
                Image(systemName: AnswerCard.weatherSymbol(weather.condition, day: weather.isDay ?? true))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(JarvisTheme.cyan)
                    .font(.system(size: 44))
                    .frame(width: 56)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(weather.tempF.map { "\($0)°" } ?? "--")
                        .font(.system(size: 56, weight: .thin, design: .rounded))
                        .monospacedDigit()
                    Text((weather.description ?? "").capitalized)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    if let high = weather.highF { Text("H \(high)°").font(.title3.weight(.medium)) }
                    if let low = weather.lowF { Text("L \(low)°").font(.title3).foregroundStyle(.secondary) }
                }
                .monospacedDigit()
            }
            if !weather.hours.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 0) {
                        ForEach(Array(weather.hours.enumerated()), id: \.element.id) { index, hour in
                            HourColumn(hour: hour, now: index == 0)
                        }
                    }
                }
            }
            HStack(spacing: 10) {
                if let rise = weather.sunrise { Tile(symbol: "sunrise", label: "Sunrise", value: clock(rise)) }
                if let set = weather.sunset { Tile(symbol: "sunset", label: "Sunset", value: clock(set)) }
                if let wind = weather.windMph {
                    let gust = weather.gustMph.flatMap { $0 >= wind + 10 ? " · \($0)" : nil } ?? ""
                    Tile(symbol: "wind", label: "Wind", value: "\(wind)\(gust) mph")
                }
                if let rain = weather.rainChance { Tile(symbol: "umbrella", label: "Rain", value: "\(rain)%") }
            }
        }
    }

    private struct HourColumn: View {
        let hour: AnswerCard.WeatherToday.Hour
        let now: Bool

        var body: some View {
            let rain = hour.rainChance ?? 0
            VStack(spacing: 8) {
                Text(now ? "Now" : clock(hour.at, hourOnly: true))
                    .font(.caption.weight(now ? .semibold : .regular))
                    .foregroundStyle(now ? JarvisTheme.cyan : .secondary)
                Image(systemName: AnswerCard.weatherSymbol(hour.condition, day: hour.isDay ?? true))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(JarvisTheme.cyan)
                    .font(.title3)
                    .frame(height: 24)
                Text(hour.tempF.map { "\($0)°" } ?? "--")
                    .font(.subheadline.weight(.medium))
                    .monospacedDigit()
                // Rain chance as a quiet bar, so the wet hours read at a glance.
                ZStack(alignment: .bottom) {
                    Capsule().fill(JarvisTheme.border).frame(width: 6, height: 28)
                    Capsule().fill(JarvisTheme.blue.opacity(rain >= 30 ? 0.9 : 0.45))
                        .frame(width: 6, height: max(3, 28 * CGFloat(rain) / 100))
                }
                Text("\(rain)%")
                    .font(.caption2)
                    .monospacedDigit()
                    .foregroundStyle(rain >= 30 ? JarvisTheme.blue : .secondary)
            }
            .frame(width: 54)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(now ? "Now" : clock(hour.at, hourOnly: true)), \(hour.tempF ?? 0) degrees, \(rain) percent rain")
        }
    }
}

/// A small aligned stat tile (sunrise, wind, ...); tiles share the row equally.
private struct Tile: View {
    let symbol: String
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(label, systemImage: symbol)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
                .labelStyle(.titleAndIcon)
                .lineLimit(1)
            Text(value)
                .font(.subheadline.weight(.medium))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(JarvisTheme.panelRaised.opacity(0.6), in: .rect(cornerRadius: 12))
    }
}

/// The rest of today's calendar, times in one aligned column.
private struct CalendarDayCard: View {
    let day: AnswerCard.CalendarDay

    var body: some View {
        if day.events.isEmpty {
            Text("Clear for the rest of today.")
                .font(.title3)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(day.events.prefix(8)) { event in
                    HStack(alignment: .firstTextBaseline, spacing: 14) {
                        Text(event.allDay ? "All day" : clock(event.startAt))
                            .font(.subheadline.weight(.semibold))
                            .monospacedDigit()
                            .foregroundStyle(JarvisTheme.cyan)
                            .frame(width: 82, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(event.title).font(.headline).lineLimit(2)
                            let detail = [event.location, event.calendar].compactMap { $0 }.filter { !$0.isEmpty }
                            if !detail.isEmpty {
                                Text(detail.joined(separator: " · "))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                }
                if day.events.count > 8 {
                    Text("and \(day.events.count - 8) more")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

/// Running timers counting down (once a second, no animation).
private struct TimersCard: View {
    let list: AnswerCard.TimerList
    /// When Core measured `remaining` (the card's time).
    let sentAt: Date

    var body: some View {
        TimelineView(.periodic(from: sentAt, by: 1)) { context in
            VStack(alignment: .leading, spacing: 12) {
                ForEach(list.timers) { timer in
                    HStack(alignment: .firstTextBaseline) {
                        Text(timer.label).font(.headline).lineLimit(1)
                        Spacer()
                        Text(status(timer, at: context.date))
                            .font(.title3.weight(.medium))
                            .monospacedDigit()
                            .foregroundStyle(timer.alarming ? JarvisTheme.amber : timer.paused ? .secondary : .primary)
                    }
                }
            }
        }
    }

    private func status(_ timer: AnswerCard.TimerList.Row, at now: Date) -> String {
        if timer.alarming { return "Time's up" }
        let left = timer.paused ? timer.remaining : max(0, timer.remaining - now.timeIntervalSince(sentAt))
        let seconds = Int(left.rounded(.up))
        let text = seconds >= 3600
            ? String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
            : String(format: "%d:%02d", seconds / 60, seconds % 60)
        return timer.paused ? "\(text) paused" : text
    }
}
