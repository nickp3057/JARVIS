import SwiftUI

/// The door panel's "Dynamic Island" (Nick, 2026-11-20): a dark capsule
/// under Scenes that only appears while something is happening, and is
/// empty space otherwise. Today it knows two activities, in this order:
///
/// - **On the TV**: the Plex film the TV reports to Core (light sync's
///   `film`, pushed through the home stream), playing or paused, with
///   rewind / play-pause / forward and the Movie scene beside them (start
///   it, or Exit movie when it's on). "Lights synced" when light sync runs.
/// - **Movie scene**: the Movie scene is on but nothing plays: Exit movie.
///
/// New activities (a timer ringing, a download, the washer) are another
/// case of `Activity` and a row in `activity`; the first one wins.
struct RoomIsland: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: HubViewModel
    @Environment(\.scenePhase) private var scenePhase
    /// Play/pause pressed: show the new state until the TV reports it.
    @State private var expectPaused: Bool?
    @State private var tick = 0

    enum Activity: Equatable {
        case tv(title: String, paused: Bool, buffering: Bool, synced: Bool)
        case movieScene

        var key: String {
            switch self {
            case .tv(let title, _, _, _): return "tv-\(title)"
            case .movieScene: return "movie"
            }
        }
    }

    private var movieScene: HomeScene? {
        model.scenes.value?.scenes.first { $0.entityID == "scene.movie" }
    }

    private var movieOn: Bool { model.scenes.value?.current == "scene.movie" }

    private var activity: Activity? {
        if let state = model.lightSync.value, let film = state.film, !film.title.isEmpty,
           ["playing", "paused", "buffering"].contains(film.state) {
            return .tv(title: film.title, paused: expectPaused ?? (film.state == "paused"),
                       buffering: film.state == "buffering", synced: state.running)
        }
        if movieOn { return .movieScene }
        return nil
    }

    var body: some View {
        ZStack {
            if let activity {
                island(activity)
                    .transition(.scale(scale: 0.92, anchor: .top).combined(with: .opacity))
            }
        }
        .animation(.spring(duration: 0.4), value: activity?.key)
        .sensoryFeedback(.impact(weight: .light), trigger: tick)
        .onChange(of: model.lightSync.value?.film?.state) { _, _ in expectPaused = nil }
        // Light sync's state (the TV's film) is fetched only while someone
        // wants it; the home stream says when it changes, this is the fallback.
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                model.wantsLightSync = true
                await model.refreshLightSync()
                try? await Task.sleep(for: .seconds(model.homeStreamLive ? 30 : 5))
            }
        }
    }

    @ViewBuilder private func island(_ activity: Activity) -> some View {
        HStack(spacing: 14) {
            switch activity {
            case .tv(let title, let paused, let buffering, let synced):
                badge("tv.fill")
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.headline).lineLimit(1)
                    Text([buffering ? "Loading" : paused ? "Paused" : "Playing on the TV",
                          synced ? "Lights synced" : nil].compactMap { $0 }.joined(separator: " · "))
                        .font(.subheadline).foregroundStyle(.white.opacity(0.6)).lineLimit(1)
                }
                Spacer(minLength: 8)
                control("backward.fill", "Rewind", key: "rewind")
                control(paused ? "play.fill" : "pause.fill", paused ? "Play" : "Pause", key: "play_pause", big: true) {
                    expectPaused = !paused
                }
                control("forward.fill", "Fast forward", key: "fast_forward")
                movieButton
            case .movieScene:
                badge("film.fill")
                VStack(alignment: .leading, spacing: 1) {
                    Text("Movie").font(.headline)
                    Text("Nothing playing yet").font(.subheadline).foregroundStyle(.white.opacity(0.6))
                }
                Spacer(minLength: 8)
                movieButton
            }
        }
        .foregroundStyle(.white)
        .padding(.leading, 12)
        .padding(.trailing, 12)
        .frame(maxWidth: .infinity, minHeight: 76, maxHeight: 76)
        .background(Color.black.opacity(0.82), in: .capsule)
        .overlay(Capsule().strokeBorder(.white.opacity(0.08)))
        .accessibilityElement(children: .contain)
    }

    private func badge(_ symbol: String) -> some View {
        Image(systemName: symbol)
            .font(.title3.weight(.semibold))
            .foregroundStyle(JarvisTheme.backgroundDeep)
            .frame(width: 52, height: 52)
            .background(JarvisTheme.cyan.opacity(0.9), in: Circle())
    }

    private func control(_ symbol: String, _ label: String, key: String, big: Bool = false,
                         then: (() -> Void)? = nil) -> some View {
        Button {
            tick += 1
            then?()
            Task {
                do { try await JarvisAPIClient.make(from: settings).pressTVRemote(key) } catch {
                    expectPaused = nil
                    model.actionError = "The TV didn't get that: \(error.localizedDescription)"
                }
            }
        } label: {
            Image(systemName: symbol)
                .font(big ? .title2.weight(.semibold) : .title3.weight(.semibold))
                .frame(width: big ? 56 : 48, height: big ? 56 : 48)
                .background(.white.opacity(big ? 0.16 : 0.08), in: Circle())
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel(label)
    }

    /// The Movie scene from the island: start it, or Exit movie when it's on
    /// (Core puts the room back how it was, `brain/movie_exit.py`).
    @ViewBuilder private var movieButton: some View {
        if movieOn {
            pill(model.runningScene == "scene.movie" ? nil : "arrow.uturn.backward", "Exit movie") {
                model.exitMovie()
            }
            .accessibilityHint("Ends the Movie scene and puts the lights back the way they were")
        } else if let movie = movieScene {
            pill(model.runningScene == movie.entityID ? nil : "film", "Movie") { model.runScene(movie) }
                .accessibilityHint("Runs the Movie scene")
        }
    }

    private func pill(_ symbol: String?, _ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if let symbol { Image(systemName: symbol) } else { ProgressView().tint(.white) }
                Text(title)
            }
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 16)
            .frame(height: 48)
            .background(.white.opacity(0.08), in: .capsule)
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(model.runningScene != nil)
    }
}
