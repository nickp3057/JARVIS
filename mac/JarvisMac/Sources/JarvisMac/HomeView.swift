import SwiftUI

/// Scenes and the room's devices (Core's HOME_DASHBOARD_ENTITIES allowlist).
struct HomeView: View {
    @ObservedObject var model: HomeModel

    private let columns = [GridItem(.adaptive(minimum: 200, maximum: 280), spacing: 12)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if let error = model.error { ErrorLine(text: error) }
                if !model.scenes.isEmpty {
                    section("Scenes") {
                        LazyVGrid(columns: columns, spacing: 12) {
                            ForEach(model.scenes) { scene in sceneTile(scene) }
                        }
                    }
                }
                ForEach(groups, id: \.title) { group in
                    section(group.title) {
                        LazyVGrid(columns: columns, spacing: 12) {
                            ForEach(group.entities) { entity in
                                EntityTile(entity: entity, busy: model.busy.contains(entity.entityID),
                                           toggle: { Task { await model.toggle(entity) } },
                                           dim: { pct in Task { await model.setBrightness(entity, pct) } })
                            }
                        }
                    }
                }
                if model.scenes.isEmpty && model.entities.isEmpty && model.error == nil {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 80)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(24)
        }
        .toolbar {
            Button { Task { await model.load() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                .keyboardShortcut("r")
                .help("Refresh (⌘R)")
        }
        .task {
            // Keep the tiles honest while the page is open.
            while !Task.isCancelled {
                await model.load()
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    private var groups: [(title: String, entities: [HomeEntity])] {
        let names = ["light": "Lights", "fan": "Fans", "switch": "Switches", "media_player": "Media"]
        let order = ["light", "fan", "switch", "media_player"]
        let grouped = Dictionary(grouping: model.entities, by: \.domain)
        return grouped.keys
            .sorted { (order.firstIndex(of: $0) ?? 99, $0) < (order.firstIndex(of: $1) ?? 99, $1) }
            .map { (names[$0] ?? $0.replacingOccurrences(of: "_", with: " ").capitalized, grouped[$0] ?? []) }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline).foregroundStyle(.secondary)
            content()
        }
    }

    private func sceneTile(_ scene: HomeScene) -> some View {
        let current = model.currentScene == scene.entityID
        return Button { Task { await model.activate(scene) } } label: {
            HStack(spacing: 10) {
                Image(systemName: scene.symbol)
                    .font(.system(size: 16))
                    .frame(width: 22)
                    .foregroundStyle(current ? JarvisTheme.accent : .secondary)
                Text(scene.name).lineLimit(1)
                Spacer(minLength: 0)
                if model.busy.contains(scene.entityID) { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 12)
            .frame(height: 44)
            .background(JarvisTheme.card, in: .rect(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .strokeBorder(current ? JarvisTheme.accent : JarvisTheme.border, lineWidth: current ? 1.5 : 1))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .help(current ? "\(scene.name) is on; click to leave it" : "Run \(scene.name)")
    }
}

private struct EntityTile: View {
    let entity: HomeEntity
    let busy: Bool
    let toggle: () -> Void
    let dim: (Int) -> Void
    @State private var brightness: Double = 100
    @State private var dragging = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: entity.symbol)
                    .font(.system(size: 16))
                    .frame(width: 22)
                    .foregroundStyle(entity.isOn ? JarvisTheme.accent : .secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(entity.friendlyName).lineLimit(1)
                    Text(stateText).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                if busy {
                    ProgressView().controlSize(.small)
                } else if entity.isToggleable {
                    Toggle("", isOn: Binding(get: { entity.isOn }, set: { _ in toggle() }))
                        .toggleStyle(.switch)
                        .controlSize(.small)
                        .labelsHidden()
                }
            }
            if entity.light?.dimmable == true {
                Slider(value: $brightness, in: 1...100) { editing in
                    dragging = editing
                    if !editing { dim(Int(brightness.rounded())) }
                }
                .controlSize(.small)
                .disabled(!entity.isToggleable)
            }
        }
        .padding(12)
        .frame(minHeight: 64, alignment: .top)
        .background(JarvisTheme.card, in: .rect(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(JarvisTheme.border))
        .onAppear(perform: sync)
        .onChange(of: entity) { if !dragging { sync() } }
    }

    private var stateText: String {
        if entity.isOn, let pct = entity.light?.brightnessPct { return "On · \(pct)%" }
        return entity.state.replacingOccurrences(of: "_", with: " ").capitalized
    }

    private func sync() { brightness = Double(entity.light?.brightnessPct ?? 100) }
}
