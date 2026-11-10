import SwiftUI

/// Pieces the phone's Home tab and the iPad's Room tab share.

/// Which light's brightness/colour sheet is open.
struct LightSheetID: Identifiable { let id: String }

/// One Home Assistant scene, picked like a Focus mode: the last one run is
/// lit, a spinner while it runs. `large` is the iPad Room tab's size.
struct SceneTile: View {
    @ObservedObject var model: HubViewModel
    let scene: HomeScene
    let lit: Bool
    var large = false

    var body: some View {
        Button { model.runScene(scene) } label: {
            HStack(spacing: large ? 14 : 10) {
                ZStack {
                    if model.runningScene == scene.entityID {
                        ProgressView()
                    } else {
                        // Sized to fit: wide symbols (the game controller,
                        // the film) used to run past the circle at a font size.
                        Image(systemName: scene.symbol)
                            .resizable()
                            .scaledToFit()
                            .fontWeight(.semibold)
                            .frame(width: large ? 24 : 18, height: large ? 24 : 18)
                    }
                }
                .frame(width: large ? 46 : 34, height: large ? 46 : 34)
                .background(lit ? JarvisTheme.cyan.opacity(0.9) : JarvisTheme.panelRaised, in: Circle())
                .foregroundStyle(lit ? JarvisTheme.backgroundDeep : JarvisTheme.cyan)
                Text(scene.name)
                    .font(large ? .headline : .subheadline.weight(.semibold))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .padding(large ? 14 : 10)
            .frame(maxWidth: .infinity, minHeight: large ? 76 : 56, alignment: .leading)
            .background(lit ? JarvisTheme.cyan.opacity(0.14) : Color.clear, in: .rect(cornerRadius: large ? 18 : 14))
            .overlay(RoundedRectangle(cornerRadius: large ? 18 : 14)
                .strokeBorder(lit ? JarvisTheme.cyan.opacity(0.7) : JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(model.runningScene != nil)
        .sensoryFeedback(.success, trigger: model.runningScene == scene.entityID)
        .accessibilityLabel(scene.name + (lit ? ", last run" : ""))
    }
}


/// Three quick taps on the reactor: Barn Door Protocol (three more stand it
/// down). A single tap has no action.
struct BarnDoorTaps: ViewModifier {
    @EnvironmentObject private var settings: AppSettings
    @State private var taps: [Date] = []
    @State private var fired = 0

    func body(content: Content) -> some View {
        content
            .contentShape(Circle())
            .onTapGesture(perform: tap)
            .sensoryFeedback(.impact(weight: .heavy), trigger: fired)
            .accessibilityAddTraits(.isButton)
            .accessibilityAction(named: "Barn Door Protocol") { trigger() }
    }

    private func tap() {
        let now = Date()
        taps = taps.filter { now.timeIntervalSince($0) < 0.9 } + [now]
        if taps.count >= 3 {
            taps = []
            trigger()
        }
    }

    private func trigger() {
        fired += 1
        let action = JarvisPalette.current == .barnDoor ? "stand_down" : "engage"
        Task {
            _ = try? await JarvisAPIClient.make(from: settings).barnDoor(action)
            await ThemeSync.pull(settings)  // red (or back) now, not at the next 10 s sync
        }
    }
}

extension View {
    func barnDoorTaps() -> some View {
        modifier(BarnDoorTaps())
    }
}

/// The scenes as one control, like Focus in Control Center: the current
/// scene (the last one run) with a menu of the rest.
struct SceneFocusMenu: View {
    @ObservedObject var model: HubViewModel
    let scenes: HomeScenes
    var title = "Scene"

    /// Barn Door's before-snapshot is a scene in HA but not one to pick.
    private var choices: [HomeScene] {
        scenes.scenes.filter { $0.entityID != "scene.barn_door_before" }
    }

    var body: some View {
        let current = choices.first { $0.entityID == scenes.current }
        Menu {
            ForEach(choices) { scene in
                Button { model.runScene(scene) } label: {
                    Label(scene.name, systemImage: scene.entityID == scenes.current ? "checkmark" : scene.symbol)
                }
            }
        } label: {
            HStack(spacing: 12) {
                ZStack {
                    if model.runningScene != nil {
                        ProgressView()
                    } else {
                        Image(systemName: current?.symbol ?? "sparkles").font(.body.weight(.semibold))
                    }
                }
                .frame(width: 38, height: 38)
                // Nothing picked: a bare icon, like the other strips' glyphs.
                .background(current != nil ? JarvisTheme.cyan.opacity(0.9) : Color.clear, in: Circle())
                .foregroundStyle(current != nil ? JarvisTheme.backgroundDeep : JarvisTheme.cyan)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Text(current?.name ?? "Pick a scene").font(.headline).foregroundStyle(.primary)
                        .lineLimit(1).minimumScaleFactor(0.8)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .jarvisSurface(in: .rect(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(JarvisTheme.border))
            .contentShape(Rectangle())
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(model.runningScene != nil)
        .accessibilityLabel("Scene, \(current?.name ?? "none")")
    }
}

/// A press-and-hold that opened a light's sheet must not also toggle the
/// light when the finger lifts (the button's tap fires on release). The
/// hold records the entity; the tap that follows it is swallowed.
@MainActor
final class HoldSuppressor: ObservableObject {
    private var held: String?
    private var heldAt = Date.distantPast

    func hold(_ id: String) {
        held = id
        heldAt = .now
    }

    /// True when this tap is the release of a hold (and should do nothing).
    func swallows(_ id: String) -> Bool {
        defer { held = nil }
        return held == id && Date.now.timeIntervalSince(heldAt) < 2
    }
}
