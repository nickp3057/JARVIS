import SwiftUI

/// Long-press a light (iPad Room tab, phone Home tab): brightness, Rainbow
/// (Nick's own Govee rainbow, where Core has one set), whites and colours
/// for that one light (`POST /api/home/light`). Shows only what the light
/// supports; the bedroom light is brightness only.
struct LightSheet: View {
    @ObservedObject var model: HubViewModel
    let entityID: String
    @Environment(\.dismiss) private var dismiss
    @State private var brightness = 50.0
    @State private var custom = Color.white
    @State private var loaded = false

    private static let whites: [(String, Int)] = [("Candle", 2200), ("Warm", 2700), ("Neutral", 4000), ("Daylight", 6500)]
    private static let colours: [(String, [Int])] = [
        ("Red", [255, 0, 0]), ("Orange", [255, 110, 0]), ("Yellow", [255, 210, 0]),
        ("Green", [0, 220, 70]), ("Cyan", [0, 220, 255]), ("Blue", [20, 60, 255]),
        ("Purple", [140, 40, 255]), ("Pink", [255, 60, 180]),
    ]

    private var entity: HomeEntity? { model.entities.value?.first { $0.entityID == entityID } }

    var body: some View {
        NavigationStack {
            Group {
                if let entity, let light = entity.light {
                    content(entity, light)
                } else {
                    ContentUnavailableView("Light unavailable", systemImage: "lightbulb.slash")
                }
            }
            .navigationTitle(entity?.friendlyName ?? "Light")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .presentationDetents([.medium, .large])
        .onAppear {
            guard !loaded, let light = entity?.light else { return }
            loaded = true
            brightness = Double(light.brightnessPct ?? 100)
            if let rgb = light.rgb, rgb.count == 3 {
                custom = Color(red: Double(rgb[0]) / 255, green: Double(rgb[1]) / 255, blue: Double(rgb[2]) / 255)
            }
        }
    }

    private func content(_ entity: HomeEntity, _ light: LightDetails) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Button { model.toggle(entity, to: !entity.isOn) } label: {
                    Label(entity.isOn ? "Turn off" : "Turn on", systemImage: "power")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 50)
                        .background((entity.isOn ? JarvisTheme.amber : JarvisTheme.cyan).opacity(0.16),
                                    in: .rect(cornerRadius: 14))
                }
                .buttonStyle(CockpitButtonStyle())

                if light.dimmable {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Label("Brightness", systemImage: "sun.max").font(.subheadline.weight(.semibold))
                            Spacer()
                            Text("\(Int(brightness))%").monospacedDigit().foregroundStyle(.secondary)
                        }
                        // Sent when the finger lifts, not on every step.
                        Slider(value: $brightness, in: 1...100, step: 1) { editing in
                            if !editing { model.setLight(entity, brightness: Int(brightness)) }
                        }
                        .tint(JarvisTheme.amber)
                    }
                }

                if light.rainbow == true {
                    Button { model.setLight(entity, rainbow: true) } label: {
                        Label("Rainbow", systemImage: "rainbow")
                            .font(.headline)
                            .foregroundStyle(.white)
                            .frame(maxWidth: .infinity, minHeight: 54)
                            .background(LinearGradient(colors: [.red, .orange, .yellow, .green, .cyan, .blue, .purple],
                                                       startPoint: .leading, endPoint: .trailing)
                                .opacity(0.85), in: .rect(cornerRadius: 14))
                            .shadow(color: .black.opacity(0.35), radius: 1)
                    }
                    .buttonStyle(CockpitButtonStyle())
                }

                if light.temperature {
                    section("Whites", symbol: "thermometer.sun") {
                        ForEach(Self.whites.filter { inRange($0.1, light) }, id: \.1) { name, kelvin in
                            swatch(name, color: Self.kelvinColor(kelvin)) { model.setLight(entity, kelvin: kelvin) }
                        }
                    }
                }

                if light.color {
                    section("Colours", symbol: "paintpalette") {
                        ForEach(Self.colours, id: \.0) { name, rgb in
                            swatch(name, color: Color(red: Double(rgb[0]) / 255, green: Double(rgb[1]) / 255,
                                                      blue: Double(rgb[2]) / 255)) {
                                model.setLight(entity, rgb: rgb)
                            }
                        }
                    }
                    // Sends only on a pick, not when onAppear seeds the light's colour.
                    ColorPicker("Any colour", selection: Binding(
                        get: { custom },
                        set: { custom = $0; model.setLight(entity, rgb: Self.rgb($0)) }
                    ), supportsOpacity: false)
                        .font(.subheadline.weight(.semibold))
                }
            }
            .padding(24)
        }
    }

    private func inRange(_ kelvin: Int, _ light: LightDetails) -> Bool {
        kelvin >= (light.minKelvin ?? 0) && kelvin <= (light.maxKelvin ?? 10_000)
    }

    private func section<Content: View>(_ title: String, symbol: String,
                                        @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: symbol).font(.subheadline.weight(.semibold))
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 72), spacing: 12)], spacing: 12) { content() }
        }
    }

    private func swatch(_ name: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Circle().fill(color)
                    .frame(width: 44, height: 44)
                    .overlay(Circle().strokeBorder(.white.opacity(0.25)))
                Text(name).font(.caption).foregroundStyle(.secondary)
            }
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel(name)
    }

    private static func kelvinColor(_ kelvin: Int) -> Color {
        switch kelvin {
        case ..<2500: return Color(red: 1, green: 0.62, blue: 0.3)
        case ..<3500: return Color(red: 1, green: 0.78, blue: 0.5)
        case ..<5000: return Color(red: 1, green: 0.92, blue: 0.82)
        default: return Color(red: 0.86, green: 0.92, blue: 1)
        }
    }

    private static func rgb(_ colour: Color) -> [Int] {
        let ui = UIColor(colour)
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        ui.getRed(&r, green: &g, blue: &b, alpha: &a)
        return [r, g, b].map { Int((min(1, max(0, $0)) * 255).rounded()) }
    }
}
