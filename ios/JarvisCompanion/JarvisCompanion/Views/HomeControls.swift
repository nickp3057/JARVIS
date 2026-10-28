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
                        Image(systemName: scene.symbol)
                            .font((large ? Font.title3 : Font.body).weight(.semibold))
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
