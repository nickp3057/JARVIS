import SwiftUI

/// Answer screens: when Nick asks JARVIS something with a visual shape
/// ("what's the weather?", "what's my day look like?", "what timers have I
/// got?"), Core puts a card on every screen (`brain/answer_cards.py`). This
/// follows Core's card stream while the app is open and shows the newest one
/// over whatever tab is up (the iPad's Room tab, the phone); it clears itself
/// after the card's time (45 s) or on a tap. One renderer per card type.
@MainActor
final class AnswerCardPresenter: ObservableObject {
    static let shared = AnswerCardPresenter()
    @Published private(set) var current: AnswerCard?
    /// The iPad's Room tab is on screen and shows the card inside JARVIS's
    /// reply panel, so the floating overlay stays away (like `VoicePresence`).
    @Published private(set) var inlineHost = false
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

    func setInlineHost(_ inline: Bool) {
        if inlineHost != inline { inlineHost = inline }
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

/// The card over the app when no reply panel can hold it (phone, other
/// tabs): a calm dim behind, the readout centred. Tap anywhere to clear it.
struct AnswerCardOverlay: View {
    @ObservedObject var presenter = AnswerCardPresenter.shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.horizontalSizeClass) private var sizeClass

    private var card: AnswerCard? { presenter.inlineHost ? nil : presenter.current }

    var body: some View {
        ZStack {
            if let card {
                Color.black.opacity(0.35)
                    .ignoresSafeArea()
                    .transition(.opacity)
                AnswerCardView(card: card, k: sizeClass == .regular ? 1 : 0.72)
                    .padding(16)
                    .frame(maxWidth: sizeClass == .regular ? 820 : 640)
                    .id(card.id)
                    .transition(reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.97)))
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.35), value: card?.id)
        .contentShape(Rectangle())
        .allowsHitTesting(card != nil)
        .onTapGesture { presenter.dismiss() }
    }
}

/// One card as a floating panel, in the app's panel style.
struct AnswerCardView: View {
    let card: AnswerCard
    var k: CGFloat = 1

    var body: some View {
        VStack(alignment: .leading, spacing: 16 * k) {
            HStack(spacing: 9) {
                Image(systemName: answerCardSymbol(card))
                    .font(.system(size: 22 * k))
                    .foregroundStyle(JarvisTheme.cyan)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text("JARVIS")
                        .font(.system(size: 13 * k, weight: .semibold))
                        .tracking(2.5)
                        .foregroundStyle(JarvisTheme.cyan)
                    Text(card.title)
                        .font(.system(size: 22 * k, weight: .semibold))
                        .accessibilityAddTraits(.isHeader)
                }
                Spacer()
                Image(systemName: "xmark")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Close")
            }
            AnswerCardContent(card: card, k: k)
        }
        .padding(24 * k)
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
}

func answerCardSymbol(_ card: AnswerCard) -> String {
    switch card.payload {
    case .weather: return "cloud.sun"
    case .calendar: return "calendar"
    case .timers: return "timer"
    case .unknown: return "sparkles"
    }
}

/// The readout itself, no chrome: the floating panel above and the Room
/// tab's reply panel (`RoomDialogue`) both show it. `k` scales every size;
/// 1 is the iPad.
struct AnswerCardContent: View {
    let card: AnswerCard
    var k: CGFloat = 1

    var body: some View {
        switch card.payload {
        case .weather(let weather): WeatherTodayCard(weather: weather, k: k)
        case .calendar(let day): CalendarDayCard(day: day, k: k)
        case .timers(let list): TimersCard(list: list, sentAt: Date(timeIntervalSince1970: card.createdAt), k: k)
        case .unknown: EmptyView()
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
    let k: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 26 * k) {
            HStack(alignment: .center, spacing: 20 * k) {
                Image(systemName: AnswerCard.weatherSymbol(weather.condition, day: weather.isDay ?? true))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(JarvisTheme.cyan)
                    .font(.system(size: 84 * k))
                    .frame(width: 100 * k)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(weather.tempF.map { "\($0)°" } ?? "--")
                        .font(.system(size: 110 * k, weight: .thin, design: .rounded))
                        .monospacedDigit()
                    Text((weather.description ?? "").capitalized)
                        .font(.system(size: 24 * k))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 6 * k) {
                    if let high = weather.highF { Text("H \(high)°").font(.system(size: 40 * k, weight: .medium, design: .rounded)) }
                    if let low = weather.lowF { Text("L \(low)°").font(.system(size: 40 * k, design: .rounded)).foregroundStyle(.secondary) }
                }
                .monospacedDigit()
            }
            if !weather.hours.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 0) {
                        ForEach(Array(weather.hours.enumerated()), id: \.element.id) { index, hour in
                            HourColumn(hour: hour, now: index == 0, k: k)
                        }
                    }
                }
            }
            HStack(spacing: 12 * k) {
                if let rise = weather.sunrise { Tile(symbol: "sunrise", label: "Sunrise", value: clock(rise), k: k) }
                if let set = weather.sunset { Tile(symbol: "sunset", label: "Sunset", value: clock(set), k: k) }
                if let wind = weather.windMph {
                    let gust = weather.gustMph.flatMap { $0 >= wind + 10 ? " · \($0)" : nil } ?? ""
                    Tile(symbol: "wind", label: "Wind", value: "\(wind)\(gust) mph", k: k)
                }
                if let rain = weather.rainChance { Tile(symbol: "umbrella", label: "Rain", value: "\(rain)%", k: k) }
            }
        }
    }

    private struct HourColumn: View {
        let hour: AnswerCard.WeatherToday.Hour
        let now: Bool
        let k: CGFloat

        var body: some View {
            let rain = hour.rainChance ?? 0
            VStack(spacing: 10 * k) {
                Text(now ? "Now" : clock(hour.at, hourOnly: true))
                    .font(.system(size: 18 * k, weight: now ? .semibold : .regular))
                    .foregroundStyle(now ? JarvisTheme.cyan : .secondary)
                Image(systemName: AnswerCard.weatherSymbol(hour.condition, day: hour.isDay ?? true))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(JarvisTheme.cyan)
                    .font(.system(size: 30 * k))
                    .frame(height: 36 * k)
                Text(hour.tempF.map { "\($0)°" } ?? "--")
                    .font(.system(size: 28 * k, weight: .medium, design: .rounded))
                    .monospacedDigit()
                // Rain chance as a quiet bar, so the wet hours read at a glance.
                ZStack(alignment: .bottom) {
                    Capsule().fill(JarvisTheme.border).frame(width: 8 * k, height: 40 * k)
                    Capsule().fill(JarvisTheme.blue.opacity(rain >= 30 ? 0.9 : 0.45))
                        .frame(width: 8 * k, height: max(3, 40 * k * CGFloat(rain) / 100))
                }
                Text("\(rain)%")
                    .font(.system(size: 16 * k))
                    .monospacedDigit()
                    .foregroundStyle(rain >= 30 ? JarvisTheme.blue : .secondary)
            }
            .frame(width: 84 * k)
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
    let k: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 6 * k) {
            Label(label, systemImage: symbol)
                .font(.system(size: 15 * k, weight: .semibold))
                .foregroundStyle(.secondary)
                .labelStyle(.titleAndIcon)
                .lineLimit(1)
            Text(value)
                .font(.system(size: 30 * k, weight: .medium, design: .rounded))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14 * k)
        .background(JarvisTheme.panelRaised.opacity(0.6), in: .rect(cornerRadius: 12))
    }
}

/// The rest of today's calendar, times in one aligned column.
private struct CalendarDayCard: View {
    let day: AnswerCard.CalendarDay
    let k: CGFloat

    var body: some View {
        if day.events.isEmpty {
            Text("Clear for the rest of today.")
                .font(.system(size: 30 * k))
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            VStack(alignment: .leading, spacing: 18 * k) {
                ForEach(day.events.prefix(8)) { event in
                    HStack(alignment: .firstTextBaseline, spacing: 20 * k) {
                        Text(event.allDay ? "All day" : clock(event.startAt))
                            .font(.system(size: 26 * k, weight: .semibold, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(JarvisTheme.cyan)
                            .frame(width: 130 * k, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(event.title).font(.system(size: 28 * k, weight: .semibold)).lineLimit(2)
                            let detail = [event.location, event.calendar].compactMap { $0 }.filter { !$0.isEmpty }
                            if !detail.isEmpty {
                                Text(detail.joined(separator: " · "))
                                    .font(.system(size: 18 * k))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                }
                if day.events.count > 8 {
                    Text("and \(day.events.count - 8) more")
                        .font(.system(size: 18 * k))
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
    let k: CGFloat

    var body: some View {
        TimelineView(.periodic(from: sentAt, by: 1)) { context in
            VStack(alignment: .leading, spacing: 18 * k) {
                ForEach(list.timers) { timer in
                    HStack(alignment: .firstTextBaseline) {
                        Text(timer.label).font(.system(size: 30 * k, weight: .semibold)).lineLimit(1)
                        Spacer()
                        Text(status(timer, at: context.date))
                            .font(.system(size: 56 * k, weight: .light, design: .rounded))
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
