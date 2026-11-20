import SwiftUI

/// A brightness (light) or speed (fan) slider that lives right on a tile:
/// the nightstand's Room page and the phone's Home card (Nick, 2026-11-19:
/// "I need the sliders to control percentages"). Drag to 0 turns it off;
/// dragging up from off turns it on at that level. The level is sent when
/// the finger lifts (`HubViewModel.setLevel`), so one drag is one command.
struct LevelSlider: View {
    @ObservedObject var model: HubViewModel
    let entity: HomeEntity
    /// Hides the percentage (kept for a narrow tile).
    var compact = false
    @State private var value = 0.0
    @State private var editing = false

    /// What Core says the level is now (0 when off).
    private var current: Double {
        let on = model.pendingEntityStates[entity.entityID] ?? entity.isOn
        return Double(LevelDragger.level(of: entity, on: on))
    }

    var body: some View {
        HStack(spacing: 10) {
            if !compact {
                Image(systemName: entity.domain == "fan" ? "fan" : "sun.min")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Slider(value: $value, in: 0...100, step: 1) { isEditing in
                editing = isEditing
                if !isEditing { model.setLevel(entity, Int(value), final: true) }
            }
            .tint(JarvisTheme.amber)
            if !compact {  // the phone's small tile shows its level in its corner instead
                Text("\(Int(value))%")
                    .font(.subheadline.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .frame(minWidth: 42, alignment: .trailing)
            }
        }
        .onAppear { value = current }
        .onChange(of: current) { _, level in if !editing { value = level } }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(entity.domain == "fan" ? "\(entity.friendlyName) speed" : "\(entity.friendlyName) brightness")
        .accessibilityValue("\(Int(value)) percent")
        .accessibilityAdjustableAction { direction in
            value = min(100, max(0, value + (direction == .increment ? 10 : -10)))
            model.setLevel(entity, Int(value), final: true)
        }
    }
}

/// The phone's tile: a thin bar showing the level, drawn inside the tile's
/// own button (no separate tap target: Nick, 2026-11-19). Hold the tile for
/// the big slider (`LevelSheet`).
struct LevelFill: View {
    let level: Int

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.18))
                Capsule().fill(JarvisTheme.amber.opacity(0.85))
                    .frame(width: max(level > 0 ? 6 : 0, geo.size.width * CGFloat(level) / 100))
            }
        }
        .frame(height: 6)
        .accessibilityHidden(true)
    }
}

/// A big level control for one light or the fan: the percentage large, a
/// full-width slider you can grab anywhere, and 1% nudges either side.
struct LevelSheet: View {
    @ObservedObject var model: HubViewModel
    let entity: HomeEntity
    @State private var value = 0.0
    @State private var dragging = false
    @State private var tick = 0
    @State private var send: Task<Void, Never>?
    @State private var showingColours = false

    /// Taller when there's a Colours button.
    static func height(_ entity: HomeEntity) -> CGFloat { hasColours(entity) ? 370 : 300 }

    static func hasColours(_ entity: HomeEntity) -> Bool {
        guard let light = entity.light else { return false }
        return light.color || light.temperature || light.rainbow == true
    }

    var body: some View {
        VStack(spacing: 22) {
            HStack {
                Label(entity.friendlyName, systemImage: RoomView.symbol(for: entity))
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                Text(value <= 0 ? "Off" : "\(Int(value))%")
                    .font(.system(size: 40, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .contentTransition(.numericText())
            }
            track
            HStack(spacing: 12) {
                nudge("minus", -1)
                ForEach([25, 50, 75, 100], id: \.self) { preset in
                    Button("\(preset)") { set(Double(preset)) }
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .background(JarvisTheme.cyan.opacity(0.1), in: .rect(cornerRadius: 12))
                        .buttonStyle(CockpitButtonStyle())
                }
                nudge("plus", 1)
            }
            if Self.hasColours(entity) {
                Button { showingColours = true } label: {
                    Label("Colour & Rainbow", systemImage: "paintpalette")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .background(JarvisTheme.cyan.opacity(0.1), in: .rect(cornerRadius: 14))
                }
                .buttonStyle(CockpitButtonStyle())
            }
        }
        .padding(24)
        .sheet(isPresented: $showingColours) { LightSheet(model: model, entityID: entity.entityID) }
        .sensoryFeedback(.selection, trigger: tick)
        .onAppear {
            let on = model.pendingEntityStates[entity.entityID] ?? entity.isOn
            value = Double(LevelDragger.level(of: entity, on: on))
        }
    }

    /// 64 pt tall: grab anywhere, the level jumps to the finger and follows it.
    private var track: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 18).fill(Color.secondary.opacity(0.16))
                RoundedRectangle(cornerRadius: 18).fill(JarvisTheme.amber.opacity(0.8))
                    .frame(width: geo.size.width * CGFloat(value) / 100)
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { drag in
                    dragging = true
                    let new = (min(max(drag.location.x / geo.size.width, 0), 1) * 100).rounded()
                    if new != value { value = new; tick += 1 }
                }
                .onEnded { _ in
                    dragging = false
                    model.setLevel(entity, Int(value), final: true)
                })
        }
        .frame(height: 64)
        .accessibilityElement()
        .accessibilityLabel("Level")
        .accessibilityValue("\(Int(value)) percent")
        .accessibilityAdjustableAction { set(value + ($0 == .increment ? 5 : -5)) }
    }

    private func nudge(_ symbol: String, _ step: Double) -> some View {
        Button { set(value + step) } label: {
            Image(systemName: symbol)
                .font(.headline)
                .frame(width: 44, height: 44)
                .background(JarvisTheme.cyan.opacity(0.1), in: .circle)
        }
        .buttonStyle(CockpitButtonStyle())
        .buttonRepeatBehavior(.enabled)
        .accessibilityLabel(step > 0 ? "Up 1 percent" : "Down 1 percent")
    }

    /// Held − / + repeat fast: the light gets the level once they settle.
    private func set(_ level: Double) {
        value = min(100, max(0, level))
        tick += 1
        send?.cancel()
        send = Task {
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            model.setLevel(entity, Int(value), final: true)
        }
    }
}
