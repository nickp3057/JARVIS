import AVFoundation
import SwiftUI

/// The alarm screen's state. It opens when Nick taps the wake-up push, when
/// the push lands while the app is open, and whenever the app is open while
/// Core says an alarm is ringing (RootView polls `GET /api/wake`). Answered
/// anywhere (I'm up or Snooze, on any device), it closes; a snooze ringing
/// again is a new alarm and opens it again.
///
/// The Room tab's night screen is a full-screen cover too, and iOS shows only
/// one at a time: the night screen stays down while the alarm is up
/// (`RoomView.roomIsDark`), and opening the alarm over it drops it first.
@MainActor
final class WakeRinging: ObservableObject {
    static let shared = WakeRinging()
    @Published var showing = false
    @Published var detail = ""
    /// Waiting for the night screen to go before the alarm screen opens.
    @Published private(set) var opening = false
    /// The Room tab's night screen is up (RoomView keeps this current).
    var nightScreenUp = false
    /// The alarm on screen (Core's `since`) and the one Nick closed, so Close
    /// sticks for that alarm but the next one (a snooze ending) opens again.
    private var since: Double?
    private var closed: Double?

    func follow(_ ringing: WakeSchedule.Ringing?) {
        // The mini's own backup ring stays up until Nick answers it here.
        guard !localRinging else { return }
        // Snoozed counts as answered: the screen closes everywhere.
        guard let ringing, ringing.state == "ringing" else {
            showing = false
            return
        }
        since = ringing.since
        if !ringing.detail.isEmpty { detail = ringing.detail }
        if ringing.since != closed { open() }
    }

    /// Show the alarm screen, taking the night screen down first if it's up.
    func open() {
        guard !showing, !opening else { return }
        guard nightScreenUp else { showing = true; return }
        opening = true
        Task {
            try? await Task.sleep(for: .milliseconds(700))
            opening = false
            showing = true
        }
    }

    func close() {
        closed = since
        showing = false
    }

    // MARK: - The nightstand's backup alarm

    /// True while the nightstand mini is ringing on its own because Core
    /// couldn't be reached at alarm time. The alarm screen then shows Stop /
    /// Snooze that work without Core.
    @Published private(set) var localRinging = false
    private static let backupKey = "nightstand.backupAlarmAt"
    private var lastContact = Date.distantPast
    private var player: AVAudioPlayer?
    private var savedBrightness: CGFloat?

    /// When the mini should ring if Core is gone by then (epoch seconds).
    /// Kept across launches, so a relaunch with Core down still rings.
    var backupAt: Double? {
        get { UserDefaults.standard.object(forKey: Self.backupKey) as? Double }
        set { UserDefaults.standard.set(newValue, forKey: Self.backupKey) }
    }

    /// Core answered `GET /api/wake`: its next alarm (or the snooze's end)
    /// is what the mini would ring at if Core goes quiet.
    func coreAnswered(_ wake: WakeSchedule) {
        lastContact = .now
        if wake.ringing?.state == "snoozed", let until = wake.ringing?.until {
            backupAt = until
        } else {
            backupAt = wake.nextAt
        }
    }

    /// Called every poll on the nightstand: ring if the alarm time is a
    /// minute gone and Core hasn't answered since it came up. If Core was
    /// reachable after the alarm time, ringing was its job.
    func checkBackup(now: Date = .now) {
        guard !localRinging, let at = backupAt else { return }
        let due = Date(timeIntervalSince1970: at)
        guard Self.shouldRing(due: due, lastContact: lastContact, now: now) else { return }
        backupAt = nil
        ringLocally()
    }

    nonisolated static func shouldRing(due: Date, lastContact: Date, now: Date) -> Bool {
        // Older than 2 h: the iPad was off or asleep; don't wake anyone for it.
        now >= due.addingTimeInterval(60) && now < due.addingTimeInterval(7200) && lastContact < due
    }

    private func ringLocally() {
        localRinging = true
        detail = "JARVIS is offline, so I'm ringing myself."
        since = nil
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .default, options: [])
        try? session.setActive(true)
        if let player = try? AVAudioPlayer(data: Self.alarmTone()) {
            player.numberOfLoops = -1
            player.volume = 1
            player.play()
            self.player = player
        }
        savedBrightness = UIScreen.main.brightness
        UIScreen.main.brightness = max(UIScreen.main.brightness, 0.6)
        open()
    }

    /// Stop (or snooze) the backup ring. Core's next answer sets the next alarm.
    func stopLocal(snoozeMinutes: Int? = nil) {
        player?.stop()
        player = nil
        localRinging = false
        if let saved = savedBrightness { UIScreen.main.brightness = saved }
        savedBrightness = nil
        if let minutes = snoozeMinutes {
            backupAt = Date.now.addingTimeInterval(Double(minutes) * 60).timeIntervalSince1970
        }
        showing = false
    }

    /// Three quick beeps and a pause, as a WAV in memory (no bundled file).
    nonisolated static func alarmTone() -> Data {
        let rate = 22050
        var samples: [Int16] = []
        func tone(_ seconds: Double) {
            let n = Int(seconds * Double(rate))
            for i in 0..<n {
                let fade = min(1, Double(min(i, n - i)) / 200)
                samples.append(Int16(sin(2 * .pi * 880 * Double(i) / Double(rate)) * 26000 * fade))
            }
        }
        func gap(_ seconds: Double) { samples += [Int16](repeating: 0, count: Int(seconds * Double(rate))) }
        for _ in 0..<3 { tone(0.14); gap(0.09) }
        gap(0.55)
        var data = Data()
        func put<T: FixedWidthInteger>(_ v: T) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
        let bytes = samples.count * 2
        data.append(contentsOf: Array("RIFF".utf8)); put(UInt32(36 + bytes))
        data.append(contentsOf: Array("WAVEfmt ".utf8)); put(UInt32(16)); put(UInt16(1)); put(UInt16(1))
        put(UInt32(rate)); put(UInt32(rate * 2)); put(UInt16(2)); put(UInt16(16))
        data.append(contentsOf: Array("data".utf8)); put(UInt32(bytes))
        for s in samples { put(s) }
        return data
    }
}

/// Full-screen alarm: three big buttons, no animation. All tell JARVIS Core,
/// which tells the TV (the same calls as the notification's buttons). I'm up
/// stops the TV's music and runs the good-morning lights; "I'm awake, leave
/// the lights" stops the alarm and nothing else, so the room stays as it is.
struct WakeRingingView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var ringing = WakeRinging.shared
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        ZStack {
            JarvisBackdrop().ignoresSafeArea()
            VStack(spacing: 28) {
                Spacer()
                Text(Date.now, format: .dateTime.hour().minute())
                    .font(.system(size: 76, weight: .thin, design: .rounded))
                    .monospacedDigit()
                Text("Good morning, sir").font(.title2.weight(.semibold))
                if !ringing.detail.isEmpty {
                    Text(ringing.detail).font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                Spacer()
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                }
                if ringing.localRinging {
                    localButton("Snooze 9 min", symbol: "zzz", tint: JarvisTheme.blue) { ringing.stopLocal(snoozeMinutes: 9) }
                    localButton("I'm up", symbol: "sun.max", tint: JarvisTheme.cyan) { ringing.stopLocal() }
                } else {
                    coreButtons
                }
            }
            .padding(24)
        }
        .disabled(busy)
    }

    /// The backup ring's buttons: they only touch this iPad.
    private func localButton(_ title: String, symbol: String, tint: Color, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .font(.title3.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 64)
                .background(tint.opacity(0.22), in: RoundedRectangle(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(tint.opacity(0.6)))
        }
        .buttonStyle(CockpitButtonStyle())
    }

    @ViewBuilder private var coreButtons: some View {
                button("Snooze 9 min", symbol: "zzz", tint: JarvisTheme.blue) { try await $0.snoozeWakeUp() }
                button("I'm up", symbol: "sun.max", tint: JarvisTheme.cyan) { try await $0.wakeUpAwake() }
                button("I'm awake, leave the lights", symbol: "lightbulb.slash", tint: JarvisTheme.blue) {
                    try await $0.wakeUpAwake(lights: false)
                }
                Button("Close") { ringing.close() }
                    .font(.footnote).foregroundStyle(.secondary)
    }

    private func button(_ title: String, symbol: String, tint: Color,
                        _ action: @escaping (JarvisAPIClient) async throws -> Void) -> some View {
        Button {
            Task {
                busy = true
                defer { busy = false }
                do {
                    try await action(try JarvisAPIClient.make(from: settings))
                    ringing.close()
                } catch {
                    self.error = error.localizedDescription
                }
            }
        } label: {
            Label(title, systemImage: symbol)
                .font(.title3.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 64)
                .background(tint.opacity(0.22), in: RoundedRectangle(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(tint.opacity(0.6)))
        }
        .buttonStyle(CockpitButtonStyle())
    }
}
