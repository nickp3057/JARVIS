import SwiftUI

/// A brightness (light) or speed (fan) slider that lives right on a tile:
/// the nightstand's Room page and the phone's Home card (Nick, 2026-11-19:
/// "I need the sliders to control percentages"). Drag to 0 turns it off;
/// dragging up from off turns it on at that level. The level is sent when
/// the finger lifts (`HubViewModel.setLevel`), so one drag is one command.
struct LevelSlider: View {
    @ObservedObject var model: HubViewModel
    let entity: HomeEntity
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
