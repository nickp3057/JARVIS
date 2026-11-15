import SwiftUI
import UIKit

/// The nightstand iPad mini's own screen (`PadRole.nightstand`), not the
/// door panel squeezed down. A dark bedside console, laid out for the mini
/// on its side (1133 × 744 pt) with nothing to scroll:
///
/// - Left: a big clock, the date and weather, the next alarm (tap to edit
///   the wake-up), what's next on the calendar and running timers, and a
///   small JARVIS (three taps: Barn Door, as everywhere).
/// - Right: Goodnight / Peace / Movie as three wide buttons, the room's
///   lights and switches as compact tiles (tap toggles, hold opens the big
///   brightness panel with colours), and a bottom row with the TV remote,
///   TV power and the menu.
/// - While JARVIS talks, the conversation takes the right side.
///
/// Its own palette, whatever the shared theme: near-black with warm
/// off-white text and soft amber for anything on. Nothing pulses. Upright,
/// the same pieces stack and scroll.
///
/// When every light is off, `RoomView` covers it with `NightstandDarkClock`:
/// the backlight at its lowest and a massive, very dim clock.
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
    let openLevel: (HomeEntity) -> Void
    let menuExtras: AnyView

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width > geo.size.height
            Group {
                if wide {
                    HStack(alignment: .top, spacing: 28) {
                        clockColumn(width: geo.size.width * 0.46, height: geo.size.height)
                            .frame(width: geo.size.width * 0.46)
                        controlsColumn
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    .padding(.horizontal, 32)
                    .padding(.vertical, 26)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 28) {
                            clockColumn(width: geo.size.width - 48, height: 520)
                            controlsColumn
                        }
                        .padding(24)
                    }
                }
            }
        }
        .background { Nightstand.background.ignoresSafeArea() }
        .foregroundStyle(Nightstand.text)
        .tint(Nightstand.amber)
        .environment(\.colorScheme, .dark)
    }

    // MARK: - Left: the time

    private func clockColumn(width: CGFloat, height: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 14) {
                BatteryBadge()
                Spacer(minLength: 0)
                weather
            }
            .font(.subheadline)
            .foregroundStyle(Nightstand.dim)

            Spacer(minLength: 8)

            TimelineView(.everyMinute) { context in
                VStack(alignment: .leading, spacing: 4) {
                    NightstandTime(date: context.date, size: min(width * 0.34, height * 0.30))
                        .frame(maxWidth: width, alignment: .leading)
                    Text(context.date, format: .dateTime.weekday(.wide).month(.wide).day())
                        .font(.title2.weight(.light))
                        .foregroundStyle(Nightstand.dim)
                }
            }

            Spacer(minLength: 16)

            Button(action: showWake) {
                HStack(spacing: 14) {
                    Image(systemName: alarmOn ? "alarm.fill" : "alarm")
                        .font(.title2)
                        .foregroundStyle(alarmOn ? Nightstand.amber : Nightstand.dim)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(alarmOn ? "Wake-up" : "No wake-up set")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Nightstand.dim)
                        if alarmOn {
                            Text(wakeText).font(.title3.weight(.medium)).monospacedDigit()
                        }
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(.footnote).foregroundStyle(Nightstand.faint)
                }
                .padding(.horizontal, 18)
                .frame(minHeight: 66)
                .background(Nightstand.card, in: .rect(cornerRadius: 20))
                .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(Nightstand.edge))
            }
            .buttonStyle(CockpitButtonStyle())
            .accessibilityLabel(alarmOn ? "Wake-up, \(wakeText)" : "No wake-up set")
            .accessibilityHint("Opens the wake-up schedule")

            VStack(alignment: .leading, spacing: 10) {
                if let nextEvent {
                    Label(nextEvent, systemImage: "calendar")
                        .lineLimit(1)
                }
                ForEach(timers.prefix(2)) { NightstandTimerRow(timer: $0) }
            }
            .font(.callout)
            .foregroundStyle(Nightstand.dim)
            .padding(.top, 14)

            Spacer(minLength: 12)

            HStack(spacing: 14) {
                JarvisCoreMark(size: 64, active: voiceActive)
                    .barnDoorTaps()
                    .accessibilityLabel("JARVIS. Triple-tap for Barn Door Protocol.")
                Text(voiceActive ? "Listening" : "Say \u{201C}Hey JARVIS\u{201D}")
                    .font(.footnote)
                    .foregroundStyle(Nightstand.faint)
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
    }

    @ViewBuilder private var weather: some View {
        if let weather = model.glance.value?.weather, weather.online != false, (weather.error ?? "").isEmpty,
           let temp = weather.temperatureF {
            HStack(spacing: 6) {
                Image(systemName: weather.symbol(night: model.sun == "below_horizon"))
                    .symbolRenderingMode(.hierarchical)
                Text("\(Int(temp.rounded()))°").monospacedDigit()
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(Int(temp.rounded())) degrees")
        }
    }

    // MARK: - Right: the room

    @ViewBuilder private var controlsColumn: some View {
        if talking {
            dialogue
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(Nightstand.card, in: .rect(cornerRadius: 26))
                .overlay(RoundedRectangle(cornerRadius: 26).strokeBorder(Nightstand.edge))
                .transition(.opacity)
        } else {
            VStack(spacing: 16) {
                bedtimeRow
                devices
                bottomRow
            }
        }
    }

    /// Goodnight, Peace and Movie: the three things done from bed.
    private var bedtimeRow: some View {
        HStack(spacing: 12) {
            ForEach(bedtimeScenes) { scene in
                NightstandSceneButton(model: model, scene: scene,
                                      lit: model.scenes.value?.current == scene.entityID)
            }
        }
    }

    private var bedtimeScenes: [HomeScene] {
        guard let scenes = model.scenes.value?.scenes else { return [] }
        func find(_ id: String, _ name: String) -> HomeScene? {
            scenes.first { $0.entityID == id }
                ?? scenes.first { $0.name.lowercased().replacingOccurrences(of: " ", with: "") == name }
        }
        return [find("scene.goodnight", "goodnight"), find("scene.peace", "peace"), find("scene.movie", "movie")]
            .compactMap { $0 }
    }

    @ViewBuilder private var devices: some View {
        if let entities = model.entities.value, !entities.isEmpty {
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 3), spacing: 12) {
                ForEach(entities) { entity in
                    NightstandDeviceTile(model: model, entity: entity, openLevel: openLevel)
                }
            }
            .frame(maxHeight: .infinity, alignment: .top)
        } else if let failure = model.entities.failureText {
            Text(failure).font(.subheadline).foregroundStyle(Nightstand.amber)
                .frame(maxHeight: .infinity, alignment: .top)
        } else {
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var bottomRow: some View {
        HStack(spacing: 12) {
            Button(action: showRemote) {
                Label("TV remote", systemImage: "appletvremote.gen4")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 56)
                    .background(Nightstand.card, in: .rect(cornerRadius: 18))
                    .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Nightstand.edge))
            }
            .buttonStyle(CockpitButtonStyle())
            TVPowerButton(size: 56)
            Menu {
                Button(action: showSettings) { Label("Room settings", systemImage: "gearshape") }
                Button(action: showWake) { Label("Wake-up · \(wakeText)", systemImage: "alarm") }
                Divider()
                menuExtras
            } label: {
                Image(systemName: "ellipsis")
                    .font(.title3.weight(.semibold))
                    .frame(width: 56, height: 56)
                    .background(Nightstand.card, in: .rect(cornerRadius: 18))
                    .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Nightstand.edge))
            }
            .buttonStyle(CockpitButtonStyle())
            .accessibilityLabel("Menu")
        }
    }
}

/// The nightstand's palette: its own, whatever the shared theme is.
enum Nightstand {
    static let background = LinearGradient(colors: [Color(red: 0.035, green: 0.04, blue: 0.065),
                                                     Color(red: 0.015, green: 0.017, blue: 0.03)],
                                           startPoint: .top, endPoint: .bottom)
    static let text = Color(red: 0.93, green: 0.90, blue: 0.85)
    static let dim = Color(red: 0.93, green: 0.90, blue: 0.85).opacity(0.55)
    static let faint = Color(red: 0.93, green: 0.90, blue: 0.85).opacity(0.32)
    static let amber = Color(red: 1.0, green: 0.72, blue: 0.38)
    static let card = Color.white.opacity(0.045)
    static let edge = Color.white.opacity(0.08)
}

/// "10:42" big and thin, with a small AM/PM after it so the digits get the
/// room. One `Text`, so the whole line shrinks together to fit its space.
struct NightstandTime: View {
    let date: Date
    let size: CGFloat
    var color: Color = Nightstand.text

    var body: some View {
        let digits = Text(date, format: .dateTime.hour(.defaultDigits(amPM: .omitted)).minute())
            .font(.system(size: size, weight: .ultraLight, design: .rounded))
            .monospacedDigit()
        let meridiem = Self.meridiem(date)
        return (meridiem.isEmpty ? digits
                : digits + Text(" " + meridiem).font(.system(size: size * 0.2, weight: .light, design: .rounded)))
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
                                   color: Color(white: level))
                        .frame(maxWidth: geo.size.width)
                    if let alarm {
                        Label(alarm, systemImage: "alarm")
                            .font(.system(size: min(30, geo.size.height * 0.045), weight: .light, design: .rounded))
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

private struct NightstandSceneButton: View {
    @ObservedObject var model: HubViewModel
    let scene: HomeScene
    let lit: Bool

    var body: some View {
        Button { model.runScene(scene) } label: {
            HStack(spacing: 10) {
                if model.runningScene == scene.entityID {
                    ProgressView().tint(Nightstand.amber)
                } else {
                    Image(systemName: symbol).font(.title3)
                }
                Text(scene.name).font(.headline).lineLimit(1).minimumScaleFactor(0.8)
            }
            .foregroundStyle(lit ? Nightstand.amber : Nightstand.text)
            .frame(maxWidth: .infinity, minHeight: 64)
            .background(lit ? Nightstand.amber.opacity(0.13) : Nightstand.card, in: .rect(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20)
                .strokeBorder(lit ? Nightstand.amber.opacity(0.45) : Nightstand.edge))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(model.runningScene != nil)
        .accessibilityLabel(scene.name)
    }

    private var symbol: String {
        switch scene.entityID {
        case "scene.goodnight": return "moon.zzz.fill"
        case "scene.peace": return "hand.wave.fill"
        case "scene.movie": return "film.fill"
        default: return scene.symbol
        }
    }
}

/// One light or switch: icon, name and state, compact. Tap toggles; hold
/// opens the big brightness panel (with colours) like the door panel's tiles.
private struct NightstandDeviceTile: View {
    @ObservedObject var model: HubViewModel
    let entity: HomeEntity
    let openLevel: (HomeEntity) -> Void

    var body: some View {
        let pending = model.pendingEntityStates[entity.entityID]
        let on = pending ?? entity.isOn
        Button { model.toggle(entity, to: !on) } label: {
            HStack(spacing: 12) {
                Image(systemName: RoomView.symbol(for: entity))
                    .font(.title3)
                    .foregroundStyle(on ? Nightstand.amber : Nightstand.faint)
                    .frame(width: 30)
                VStack(alignment: .leading, spacing: 2) {
                    Text(entity.friendlyName)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(2)
                        .minimumScaleFactor(0.85)
                        .multilineTextAlignment(.leading)
                    Text(state(on: on))
                        .font(.caption)
                        .foregroundStyle(on ? Nightstand.amber.opacity(0.8) : Nightstand.faint)
                        .monospacedDigit()
                }
                Spacer(minLength: 0)
                if pending != nil { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
            .background(on ? Nightstand.amber.opacity(0.11) : Nightstand.card, in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18)
                .strokeBorder(on ? Nightstand.amber.opacity(0.4) : Nightstand.edge))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(!entity.isToggleable || pending != nil)
        .simultaneousGesture(LongPressGesture(minimumDuration: 0.35).onEnded { _ in
            if LevelDragger.adjustable(entity) { openLevel(entity) }
        })
        .sensoryFeedback(.impact(weight: .light), trigger: on)
        .accessibilityLabel(entity.friendlyName)
        .accessibilityValue(on ? "On" : "Off")
        .accessibilityHint(LevelDragger.adjustable(entity) ? "Hold for brightness" : "")
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
                .foregroundStyle(done ? Nightstand.amber : Nightstand.dim)
            Text(timer.label.isEmpty ? "Timer" : timer.label.capitalized)
            if done {
                Text("Time's up").foregroundStyle(Nightstand.amber)
            } else {
                Text(timerInterval: Date.now...end, countsDown: true).monospacedDigit()
            }
        }
        .accessibilityElement(children: .combine)
    }
}
