import SwiftUI
import UIKit

/// The nightstand iPad mini's own screen (`PadRole.nightstand`), laid out for
/// the mini on its side (1133 × 744 pt) with nothing to scroll, on the same
/// theme and backdrop as the phone and the door iPad:
///
/// - Left: JARVIS, big (three taps: Barn Door, as everywhere). He grows a
///   little and his glow lifts while he's talking.
/// - Right: the clock, the date and weather, the next wake-up (tap to edit),
///   what's next on the calendar and running timers. While JARVIS talks, the
///   conversation takes this side.
/// - Bottom: a big Goodnight, then Room (every light, switch and scene, on
///   its own page: `NightstandRoomSheet`), Movie, the TV remote, TV power
///   and the menu.
///
/// Upright, the same pieces stack and scroll. When every light is off,
/// `RoomView` covers it with `NightstandDarkClock`.
struct NightstandPanel: View {
    @ObservedObject var model: HubViewModel
    let wakeText: String
    let alarmOn: Bool
    let nextEvent: String?
    let timers: [NowTimer]
    let talking: Bool
    let voiceActive: Bool
    let dialogue: AnyView
    let showRemote: () -> Void
    let showWake: () -> Void
    let showSettings: () -> Void
    /// Set while the Movie scene runs but Nick went back to the panel: the
    /// bar's Movie button becomes "Movie controls" and brings the remote back.
    let backToMovie: (() -> Void)?
    let menuExtras: AnyView
    @State private var showingRoom = false

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width > geo.size.height
            if wide {
                VStack(spacing: 18) {
                    HStack(spacing: 24) {
                        // JARVIS gave the clock a little of his column (Nick, 2026-11-19: bigger clock).
                        jarvis(size: min(geo.size.height * 0.54, geo.size.width * 0.32))
                            .frame(width: geo.size.width * 0.36)
                        Group {
                            if talking {
                                dialoguePanel
                            } else {
                                // Down a size from 6.78 and nudged left a little (Nick, 2026-11-19).
                                clock(width: geo.size.width * 0.54, height: geo.size.height)
                                    .padding(.trailing, 36)
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    .frame(maxHeight: .infinity)
                    bottomBar
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 22)
            } else {
                ScrollView {
                    VStack(spacing: 26) {
                        jarvis(size: min(geo.size.width * 0.5, 320))
                        if talking { dialoguePanel.frame(minHeight: 320) } else {
                            clock(width: geo.size.width - 48, height: 560)
                        }
                        bottomBar
                    }
                    .padding(24)
                }
            }
        }
        .sheet(isPresented: $showingRoom) { NightstandRoomSheet(model: model).idleDismiss() }
    }

    // MARK: - JARVIS

    private func jarvis(size: CGFloat) -> some View {
        VStack(spacing: 14) {
            HStack {
                BatteryBadge()
                Spacer(minLength: 0)
            }
            Spacer(minLength: 0)
            JarvisCoreMark(size: size, active: voiceActive)
                .background {
                    Circle()
                        .fill(RadialGradient(colors: [JarvisTheme.reactor.opacity(0.35), .clear],
                                             center: .center, startRadius: 0, endRadius: size * 0.62))
                        .frame(width: size * 1.25, height: size * 1.25)
                        .opacity(talking ? 1 : 0)
                        .allowsHitTesting(false)
                }
                .scaleEffect(talking ? 1.08 : 1)
                .animation(.easeInOut(duration: 0.5), value: talking)
                .barnDoorTaps()
                .accessibilityLabel("JARVIS. Triple-tap for Barn Door Protocol.")
            Text(voiceActive ? "Listening" : "Say \u{201C}Hey JARVIS\u{201D}")
                .font(.footnote)
                .foregroundStyle(.tertiary)
            Spacer(minLength: 0)
        }
    }

    // MARK: - The time

    private func clock(width: CGFloat, height: CGFloat) -> some View {
        VStack(spacing: 18) {
            Spacer(minLength: 0)
            TimelineView(.everyMinute) { context in
                VStack(spacing: 6) {
                    NightstandTime(date: context.date, size: min(width * 0.34, height * 0.30), color: .primary)
                        .frame(maxWidth: width)
                    HStack(spacing: 12) {
                        Text(context.date, format: .dateTime.weekday(.wide).month(.wide).day())
                        weather
                    }
                    .font(.title3)
                    .foregroundStyle(.secondary)
                }
            }
            Button(action: showWake) {
                HStack(spacing: 12) {
                    Image(systemName: alarmOn ? "alarm.fill" : "alarm")
                        .font(.title3)
                        .foregroundStyle(alarmOn ? JarvisTheme.amber : Color.secondary)
                    Text(alarmOn ? wakeText : "No wake-up set")
                        .font(.title3.weight(.medium))
                        .monospacedDigit()
                    Image(systemName: "chevron.right").font(.footnote).foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 22)
                .frame(minHeight: 54)
                .jarvisSurface(in: .capsule)
                .overlay(Capsule().strokeBorder(JarvisTheme.border))
            }
            .buttonStyle(CockpitButtonStyle())
            .accessibilityLabel(alarmOn ? "Wake-up, \(wakeText)" : "No wake-up set")
            .accessibilityHint("Opens the wake-up schedule")
            if nextEvent != nil || !timers.isEmpty {
                VStack(spacing: 8) {
                    if let nextEvent {
                        Label(nextEvent, systemImage: "calendar").lineLimit(1)
                    }
                    ForEach(timers.prefix(2)) { NightstandTimerRow(timer: $0) }
                }
                .font(.callout)
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder private var weather: some View {
        if let weather = model.glance.value?.weather, weather.online != false, (weather.error ?? "").isEmpty,
           let temp = weather.temperatureF {
            HStack(spacing: 5) {
                Image(systemName: weather.symbol(night: model.sun == "below_horizon"))
                    .symbolRenderingMode(.multicolor)
                Text("\(Int(temp.rounded()))°").monospacedDigit()
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(Int(temp.rounded())) degrees")
        }
    }

    private var dialoguePanel: some View {
        dialogue
            .padding(20)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .jarvisSurface(in: .rect(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).strokeBorder(JarvisTheme.border))
            .transition(.opacity)
    }

    // MARK: - Bottom bar

    private var bottomBar: some View {
        HStack(spacing: 12) {
            if let goodnight = scene("scene.goodnight", "goodnight") {
                GoodnightButton(model: model, scene: goodnight)
            }
            barButton("Room", symbol: "square.grid.2x2.fill") { showingRoom = true }
            if let backToMovie {
                barButton("Movie controls", symbol: "play.rectangle.fill", highlighted: true, action: backToMovie)
            } else if let movie = scene("scene.movie", "movie") {
                barButton("Movie", symbol: "film.fill", busy: model.runningScene == movie.entityID) {
                    model.runScene(movie)
                }
                .disabled(model.runningScene != nil)
            }
            barButton("Remote", symbol: "appletvremote.gen4", action: showRemote)
            TVPowerButton(size: 72)
            Menu {
                Button(action: showSettings) { Label("Room settings", systemImage: "gearshape") }
                Button(action: showWake) { Label("Wake-up · \(wakeText)", systemImage: "alarm") }
                Divider()
                menuExtras
            } label: {
                Image(systemName: "ellipsis")
                    .font(.title3.weight(.semibold))
                    .frame(width: 72, height: 72)
                    .jarvisSurface(in: .rect(cornerRadius: 20))
                    .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(JarvisTheme.border))
            }
            .buttonStyle(CockpitButtonStyle())
            .accessibilityLabel("Menu")
        }
        .frame(height: 72)
    }

    private func barButton(_ title: String, symbol: String, busy: Bool = false, highlighted: Bool = false,
                           action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 5) {
                if busy { ProgressView() } else { Image(systemName: symbol).font(.title3) }
                Text(title).font(.caption.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.75)
            }
            .foregroundStyle(highlighted ? JarvisTheme.amber : JarvisTheme.cyan)
            .frame(width: highlighted ? 120 : 92, height: 72)
            .background(highlighted ? JarvisTheme.amber.opacity(0.14) : .clear, in: .rect(cornerRadius: 20))
            .jarvisSurface(in: .rect(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20)
                .strokeBorder(highlighted ? JarvisTheme.amber.opacity(0.5) : JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel(title)
    }

    private func scene(_ id: String, _ name: String) -> HomeScene? {
        guard let scenes = model.scenes.value?.scenes else { return nil }
        return scenes.first { $0.entityID == id }
            ?? scenes.first { $0.name.lowercased().replacingOccurrences(of: " ", with: "") == name }
    }
}

/// Goodnight, big: the one thing done from bed every night.
private struct GoodnightButton: View {
    @ObservedObject var model: HubViewModel
    let scene: HomeScene

    var body: some View {
        Button { model.runScene(scene) } label: {
            HStack(spacing: 14) {
                if model.runningScene == scene.entityID {
                    ProgressView()
                } else {
                    Image(systemName: "moon.zzz.fill").font(.title)
                }
                Text("Goodnight").font(.title2.weight(.semibold))
            }
            .foregroundStyle(JarvisTheme.cyan)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(JarvisTheme.cyan.opacity(0.16), in: .rect(cornerRadius: 22))
            .overlay(RoundedRectangle(cornerRadius: 22).strokeBorder(JarvisTheme.cyan.opacity(0.45)))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(model.runningScene != nil)
        .accessibilityLabel("Goodnight")
        .accessibilityHint("Runs the Goodnight scene")
    }
}

/// Every light, switch and scene, on a page of its own behind the Room
/// button: tap a tile to toggle it, hold a light for brightness and colour.
struct NightstandRoomSheet: View {
    @ObservedObject var model: HubViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var lightSheet: LightSheetID?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if let scenes = model.scenes.value?.scenes, !scenes.isEmpty {
                        section("Scenes", symbol: "sparkles")
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), spacing: 12)], spacing: 12) {
                            ForEach(scenes) { scene in
                                SceneTile(model: model, scene: scene, lit: scene.entityID == model.scenes.value?.current)
                            }
                        }
                    }
                    section("Lights & switches", symbol: "lightbulb.2")
                    if let entities = model.entities.value, !entities.isEmpty {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), spacing: 12)], spacing: 12) {
                            ForEach(entities) { entity in
                                NightstandDeviceTile(model: model, entity: entity) {
                                    lightSheet = LightSheetID(id: entity.entityID)
                                }
                            }
                        }
                    } else if let failure = model.entities.failureText {
                        Text(failure).font(.subheadline).foregroundStyle(JarvisTheme.amber)
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                    RoomVolumeControl()
                        .padding(18)
                        .jarvisSurface(in: .rect(cornerRadius: 20))
                        .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(JarvisTheme.border))
                }
                .padding(20)
            }
            .background { JarvisBackdrop().ignoresSafeArea() }
            .navigationTitle("Room")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .sheet(item: $lightSheet) { LightSheet(model: model, entityID: $0.id).idleDismiss() }
        }
        .tint(JarvisTheme.cyan)
    }

    private func section(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(JarvisTheme.cyan)
    }
}

/// "10:42" big, with a small AM/PM after it so the digits get the room. One
/// `Text`, so the whole line shrinks together to fit its space. Nick wanted
/// both clocks heavier (2026-11-15): semibold on the panel, heavy on the dark
/// screen so it reads from bed at a low grey.
struct NightstandTime: View {
    let date: Date
    let size: CGFloat
    var color: Color = .primary
    var weight: Font.Weight = .semibold

    var body: some View {
        let digits = Text(date, format: .dateTime.hour(.defaultDigits(amPM: .omitted)).minute())
            .font(.system(size: size, weight: weight, design: .rounded))
            .monospacedDigit()
        let meridiem = Self.meridiem(date)
        return (meridiem.isEmpty ? digits
                : digits + Text(" " + meridiem).font(.system(size: size * 0.2, weight: weight, design: .rounded)))
            .foregroundStyle(color)
            .lineLimit(1)
            .minimumScaleFactor(0.3)
            .accessibilityLabel(date.formatted(date: .omitted, time: .shortened))
    }

    /// "AM" / "PM" in the user's locale ("" on a 24-hour clock).
    static func meridiem(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = .current
        formatter.setLocalizedDateFormatFromTemplate("j")
        guard formatter.dateFormat.contains("a") else { return "" }
        formatter.dateFormat = "a"
        return formatter.string(from: date)
    }
}

/// The dark screen on the nightstand when every light is off: nothing but a
/// massive clock, very dim, with the next alarm under it. The backlight is
/// at its lowest (`RoomView.setDimmed`). No seconds, nothing moves.
struct NightstandDarkClock: View {
    let alarm: String?
    /// Grey level of the digits (Room settings › Dark clock).
    let level: Double

    var body: some View {
        GeometryReader { geo in
            TimelineView(.everyMinute) { context in
                VStack(spacing: geo.size.height * 0.03) {
                    NightstandTime(date: context.date,
                                   size: min(geo.size.width * 0.32, geo.size.height * 0.55),
                                   color: Color(white: level), weight: .heavy)
                        .frame(maxWidth: geo.size.width)
                    if let alarm {
                        Label(alarm, systemImage: "alarm")
                            .font(.system(size: min(30, geo.size.height * 0.045), weight: .semibold, design: .rounded))
                            .foregroundStyle(Color(white: level * 0.7))
                            .monospacedDigit()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// The Dark clock choices, from barely there to readable across the room.
    static let levels: [(name: String, value: Double)] = [
        ("Faintest", 0.10), ("Very dark", 0.17), ("Dark", 0.26), ("Dim", 0.38),
    ]
    static let defaultLevel = 0.17
}

/// One light or switch on the Room page: tap toggles, the slider under a
/// light or the fan sets its level; hold for its sheet (colour, Rainbow).
private struct NightstandDeviceTile: View {
    @ObservedObject var model: HubViewModel
    let entity: HomeEntity
    let openSheet: () -> Void
    @State private var holdBuzz = 0

    var body: some View {
        let pending = model.pendingEntityStates[entity.entityID]
        let on = pending ?? entity.isOn
        VStack(spacing: 10) {
            Button { model.toggle(entity, to: !on) } label: {
                HStack(spacing: 12) {
                    Image(systemName: RoomView.symbol(for: entity))
                        .font(.title2)
                        .foregroundStyle(on ? JarvisTheme.amber : Color.secondary)
                        .frame(width: 32)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entity.friendlyName)
                            .font(.headline)
                            .lineLimit(2)
                            .minimumScaleFactor(0.85)
                            .multilineTextAlignment(.leading)
                        Text(state(on: on))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                    Spacer(minLength: 0)
                    if pending != nil { ProgressView() }
                }
                .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(CockpitButtonStyle())
            .disabled(!entity.isToggleable || pending != nil)
            .simultaneousGesture(LongPressGesture(minimumDuration: 0.35).onEnded { _ in
                if LevelDragger.adjustable(entity) { holdBuzz += 1; openSheet() }
            })
            .accessibilityLabel(entity.friendlyName)
            .accessibilityValue(on ? "On" : "Off")
            .accessibilityHint(LevelDragger.adjustable(entity) ? "Hold for colour" : "")
            // Brightness or fan speed right on the tile (hold still opens colours).
            if LevelDragger.adjustable(entity) {
                LevelSlider(model: model, entity: entity)
            }
        }
        .padding(16)
        // Every tile in a row the same height, slider or not.
        .frame(maxWidth: .infinity, minHeight: 80, maxHeight: .infinity, alignment: .topLeading)
        .background(on ? JarvisTheme.amber.opacity(0.16) : JarvisTheme.cyan.opacity(0.05), in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18)
            .strokeBorder(on ? JarvisTheme.amber.opacity(0.45) : JarvisTheme.border))
        .sensoryFeedback(.impact(weight: .light), trigger: on)
        .sensoryFeedback(.impact(weight: .medium), trigger: holdBuzz)
    }

    private func state(on: Bool) -> String {
        guard entity.isToggleable else { return entity.state.capitalized }
        guard on else { return "Off" }
        if let pct = entity.light?.brightnessPct ?? entity.fan?.percentage { return "On · \(pct)%" }
        return "On"
    }
}

private struct NightstandTimerRow: View {
    let timer: NowTimer

    var body: some View {
        let end = Date(timeIntervalSince1970: timer.expiresAt)
        let done = timer.state == "alarming" || end <= .now
        HStack(spacing: 8) {
            Image(systemName: done ? "alarm.waves.left.and.right.fill" : "timer")
                .foregroundStyle(done ? JarvisTheme.amber : Color.secondary)
            Text(timer.label.isEmpty ? "Timer" : timer.label.capitalized)
            if done {
                Text("Time's up").foregroundStyle(JarvisTheme.amber)
            } else {
                Text(timerInterval: Date.now...end, countsDown: true).monospacedDigit()
            }
        }
        .accessibilityElement(children: .combine)
    }
}
