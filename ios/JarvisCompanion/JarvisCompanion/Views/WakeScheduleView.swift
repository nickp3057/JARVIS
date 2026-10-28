import SwiftUI

/// The TV wake-up schedule: each day gets its own time (or none), plus what
/// plays and how loud. Saved to JARVIS Core (`PUT /api/wake`); the TV turns
/// on and plays it, with I'm up / Snooze on screen. Talking to JARVIS edits
/// the same schedule ("wake me up at 9 on Saturday").
struct WakeScheduleView: View {
    @EnvironmentObject private var settings: AppSettings
    @State private var loaded: WakeSchedule?
    @State private var days: [Day] = WakeScheduleView.defaultDays
    @State private var music = ""
    @State private var volume = 30.0
    @State private var enabled = true
    @State private var loading = true
    @State private var saving = false
    @State private var error: String?
    @State private var savedTick = 0

    struct Day: Equatable {
        var on: Bool
        var time: Date
    }

    private static let names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    private static let defaultDays = (0..<7).map { _ in Day(on: false, time: clock("07:00")) }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                CockpitHeader(eyebrow: "Wake-up", title: "Rise and shine.",
                              subtitle: nextText)
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                CockpitPanel("Schedule", symbol: "calendar") {
                    Toggle("Wake me up", isOn: $enabled).tint(JarvisTheme.cyan)
                    ForEach(0..<7, id: \.self) { index in
                        dayRow(index)
                    }
                    .disabled(!enabled)
                    .opacity(enabled ? 1 : 0.45)
                    if let first = days.first(where: \.on) {
                        Button("Use \(first.time.formatted(date: .omitted, time: .shortened)) on every weekday") {
                            for index in 0..<5 { days[index] = Day(on: true, time: first.time) }
                        }
                        .font(.caption.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                        .disabled(!enabled)
                    }
                }
                CockpitPanel("Music", symbol: "music.note") {
                    TextField("Song, playlist or Spotify link", text: $music)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .padding(13).background(.black.opacity(0.2), in: .rect(cornerRadius: 12))
                    Text("Leave it empty to pick up whatever Spotify last played.")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Image(systemName: "speaker.wave.1").foregroundStyle(.secondary)
                        Slider(value: $volume, in: 5...100, step: 5).tint(JarvisTheme.cyan)
                        Image(systemName: "speaker.wave.3").foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Volume")
                    .accessibilityValue("\(Int(volume)) percent")
                    Text("Plays at \(Int(volume))% right away, no fade. Snooze on the TV gives you 9 more minutes.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                ForEach(loaded?.overrides ?? [], id: \.self) { change in
                    Label(Self.overrideText(change), systemImage: change.time == nil ? "moon.zzz" : "clock.arrow.circlepath")
                        .font(.footnote).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if let once = loaded?.onceAt, let date = Self.iso(once) {
                    Label("One-off at \(date.formatted(date: .abbreviated, time: .shortened)), set by JARVIS.",
                          systemImage: "clock.badge")
                        .font(.footnote).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .frame(maxWidth: 680).padding(20).frame(maxWidth: .infinity)
        }
        .background { JarvisBackdrop() }
        .navigationTitle("Wake-up").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if saving { ProgressView() }
                else { Button("Save", action: save).disabled(loading || !changed) }
            }
        }
        .overlay { if loading { ProgressView() } }
        .sensoryFeedback(.success, trigger: savedTick)
        .task { await load() }
    }

    private func dayRow(_ index: Int) -> some View {
        HStack {
            Toggle(Self.names[index], isOn: $days[index].on).tint(JarvisTheme.cyan)
                .fixedSize()
            Spacer()
            if days[index].on {
                DatePicker(Self.names[index], selection: $days[index].time, displayedComponents: .hourAndMinute)
                    .labelsHidden()
            } else {
                Text("Off").font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .frame(minHeight: 44)
    }

    private var nextText: String {
        if loading { return "Checking the schedule…" }
        guard let next = loaded?.nextAt else { return "Nothing set. Pick your days below." }
        let date = Date(timeIntervalSince1970: next)
        return "Next: \(date.formatted(.dateTime.weekday(.wide).hour().minute()))"
    }

    private var draft: WakeScheduleDraft {
        let typed = music.trimmingCharacters(in: .whitespacesAndNewlines)
        let unchanged = typed == Self.display(loaded?.music ?? "")
        return WakeScheduleDraft(
            times: days.map { $0.on ? Self.hhmm($0.time) : nil },
            music: unchanged ? (loaded?.music ?? "") : typed,
            label: unchanged ? (loaded?.label ?? "") : "",
            volume: Int(volume), enabled: enabled)
    }

    private var changed: Bool {
        guard let loaded else { return true }
        let d = draft
        return d.times != loaded.times || d.music != loaded.music || d.volume != loaded.volume
            || d.enabled != loaded.enabled
    }

    private func load() async {
        defer { loading = false }
        do {
            apply(try await JarvisAPIClient.make(from: settings).wakeSchedule())
        } catch {
            self.error = "Couldn't load the schedule: \(error.localizedDescription)"
        }
    }

    private func save() {
        saving = true
        error = nil
        let body = draft
        Task {
            defer { saving = false }
            do {
                apply(try await JarvisAPIClient.make(from: settings).saveWakeSchedule(body))
                savedTick += 1
            } catch {
                self.error = "Not saved: \(error.localizedDescription)"
            }
        }
    }

    private func apply(_ schedule: WakeSchedule) {
        loaded = schedule
        days = (0..<7).map { index in
            let time = schedule.times.indices.contains(index) ? schedule.times[index] : nil
            return Day(on: time != nil, time: Self.clock(time ?? "07:00"))
        }
        music = Self.display(schedule.music)
        volume = Double(schedule.volume)
        enabled = schedule.enabled
    }

    /// Core stores a typed name as "search:<words>"; show just the words.
    private static func display(_ music: String) -> String {
        music.hasPrefix("search:") ? String(music.dropFirst(7)) : music
    }

    private static func clock(_ hhmm: String) -> Date {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        return Calendar.current.date(bySettingHour: parts.first ?? 7, minute: parts.count > 1 ? parts[1] : 0,
                                     second: 0, of: Date()) ?? Date()
    }

    private static func hhmm(_ date: Date) -> String {
        let c = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", c.hour ?? 7, c.minute ?? 0)
    }

    /// "Wednesday: 8:30 AM instead, just this once" / "Thursday: skipped, just this once".
    private static func overrideText(_ change: WakeSchedule.Override) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        let day = f.date(from: change.date).map { $0.formatted(.dateTime.weekday(.wide).month(.abbreviated).day()) }
            ?? change.date
        guard let time = change.time else { return "\(day): skipped, just this once" }
        return "\(day): \(clock(time).formatted(date: .omitted, time: .shortened)) instead, just this once"
    }

    private static func iso(_ text: String) -> Date? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return f.date(from: text)
    }
}
