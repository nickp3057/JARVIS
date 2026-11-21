import SwiftUI
import UIKit

/// Movie mode's poster wall: every Plex film or show, searchable, sorted by
/// recent / A–Z / unwatched. A film asks Resume or Start over (or just Play);
/// a show opens its page (Continue, or pick an episode). Everything starts on
/// the room TV through Core. Dark and still, like the rest of movie mode.
struct MoviePickerView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var kind = "movies"
    @State private var movies: [PlexMovie] = []
    @State private var shows: [PlexShow] = []
    @State private var query = ""
    @State private var sort = "recent"
    @State private var loading = true
    @State private var error: String?
    @State private var chosen: PlexMovie?
    @State private var starting: String?
    @State private var started = 0
    @State private var path: [PlexShow] = []

    private var sorts: [(String, String)] {
        [("recent", kind == "shows" ? "Recently watched" : "Recently added"), ("unwatched", "Unwatched"), ("title", "A–Z")]
    }

    private var columns: [GridItem] {
        [GridItem(.adaptive(minimum: sizeClass == .regular ? 150 : 105, maximum: 200), spacing: 16)]
    }

    var body: some View {
        NavigationStack(path: $path) {
            ScrollView {
                Picker("Library", selection: $kind) {
                    Text("Movies").tag("movies")
                    Text("TV Shows").tag("shows")
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 320)
                .padding(.top, 10)
                if let error {
                    ContentUnavailableView("Plex isn't answering", systemImage: "film.stack", description: Text(error))
                        .padding(.top, 60)
                } else if (kind == "movies" ? movies.isEmpty : shows.isEmpty) && !loading {
                    ContentUnavailableView.search(text: query).padding(.top, 60)
                } else {
                    LazyVGrid(columns: columns, spacing: 20) {
                        if kind == "movies" {
                            ForEach(movies) { movie in
                                Button { chosen = movie } label: {
                                    PosterTile(title: movie.title, subtitle: movie.year.map(String.init), thumb: movie.thumb,
                                               progress: movie.progress, watched: movie.watched, busy: starting == movie.id)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel(movie.year.map { "\(movie.title), \($0)" } ?? movie.title)
                            }
                        } else {
                            ForEach(shows) { show in
                                NavigationLink(value: show) {
                                    PosterTile(title: show.title, subtitle: show.countLine, thumb: show.thumb,
                                               progress: show.progress, watched: show.allWatched, busy: false)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                    .padding(sizeClass == .regular ? 20 : 14)
                }
            }
            .overlay { if loading && (kind == "movies" ? movies.isEmpty : shows.isEmpty) { ProgressView() } }
            .background(Color.black.opacity(0.92).ignoresSafeArea())
            .navigationTitle(kind == "movies" ? "Pick a movie" : "Pick a show")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, prompt: "Search Plex")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Picker("Sort", selection: $sort) {
                            ForEach(sorts, id: \.0) { Text($0.1).tag($0.0) }
                        }
                    } label: { Label("Sort", systemImage: "arrow.up.arrow.down") }
                }
            }
            .navigationDestination(for: PlexShow.self) { show in
                ShowEpisodesView(show: show) { dismiss() }
            }
            .task(id: "\(kind)|\(query)|\(sort)") {
                // Wait for typing to settle before asking Plex.
                if !query.isEmpty { try? await Task.sleep(for: .milliseconds(350)) }
                guard !Task.isCancelled else { return }
                await load()
            }
            .confirmationDialog(chosen?.title ?? "", isPresented: Binding(get: { chosen != nil },
                                                                          set: { if !$0 { chosen = nil } }),
                                titleVisibility: .visible, presenting: chosen) { movie in
                if movie.resumable {
                    Button("Resume from \(Self.clock(movie.viewOffsetMs))") { play(movie, fromStart: false) }
                    Button("Start over") { play(movie, fromStart: true) }
                } else {
                    Button("Play on the TV") { play(movie, fromStart: false) }
                }
            } message: { movie in
                Text([movie.year.map(String.init), movie.durationMs > 0 ? Self.clock(movie.durationMs) : nil,
                      movie.contentRating?.isEmpty == false ? movie.contentRating : nil]
                    .compactMap { $0 }.joined(separator: " · "))
            }
            .sensoryFeedback(.success, trigger: started)
        }
        .preferredColorScheme(.dark)
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let client = try JarvisAPIClient.make(from: settings)
            if kind == "movies" {
                movies = try await client.plexMovies(query: query, sort: sort)
            } else {
                shows = try await client.plexShows(query: query, sort: sort)
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func play(_ movie: PlexMovie, fromStart: Bool) {
        starting = movie.id
        Task {
            defer { starting = nil }
            do {
                _ = try await JarvisAPIClient.make(from: settings).playOnTV(ratingKey: movie.ratingKey, fromStart: fromStart)
                started += 1
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    static func clock(_ ms: Int) -> String {
        let minutes = ms / 60_000
        return minutes >= 60 ? "\(minutes / 60)h \(minutes % 60)m" : "\(minutes)m"
    }
}

/// One show: Continue (the next-up episode) at the top, then every season's
/// episodes. A tap plays it; a part-watched one offers Resume / Start over.
private struct ShowEpisodesView: View {
    @EnvironmentObject private var settings: AppSettings
    let show: PlexShow
    let done: () -> Void
    @State private var listing: ShowEpisodes?
    @State private var error: String?
    @State private var chosen: PlexEpisode?
    @State private var starting: String?

    private var nextUp: PlexEpisode? {
        listing?.seasons.flatMap(\.episodes).first { $0.ratingKey == listing?.nextUp }
    }

    var body: some View {
        List {
            if let next = nextUp {
                Section {
                    Button { start(next, fromStart: false) } label: {
                        HStack {
                            Image(systemName: "play.fill").foregroundStyle(JarvisTheme.amber)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(next.resumable ? "Continue" : "Play next").font(.headline)
                                Text("\(next.code) · \(next.title)").font(.subheadline).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if starting == next.id { ProgressView() }
                        }
                    }
                }
            }
            ForEach(listing?.seasons ?? [], id: \.season) { season in
                Section(season.season == 0 ? "Specials" : "Season \(season.season)") {
                    ForEach(season.episodes) { episode in
                        Button { choose(episode) } label: { row(episode) }
                    }
                }
            }
            if let error { Text(error).foregroundStyle(.secondary) }
        }
        .overlay { if listing == nil && error == nil { ProgressView() } }
        .scrollContentBackground(.hidden)
        .background(Color.black.opacity(0.92).ignoresSafeArea())
        .navigationTitle(show.title)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            do { listing = try await JarvisAPIClient.make(from: settings).showEpisodes(showKey: show.ratingKey) } catch {
                self.error = error.localizedDescription
            }
        }
        .confirmationDialog(chosen?.title ?? "", isPresented: Binding(get: { chosen != nil },
                                                                      set: { if !$0 { chosen = nil } }),
                            titleVisibility: .visible, presenting: chosen) { episode in
            Button("Resume from \(MoviePickerView.clock(episode.viewOffsetMs))") { start(episode, fromStart: false) }
            Button("Start over") { start(episode, fromStart: true) }
        }
    }

    private func row(_ episode: PlexEpisode) -> some View {
        HStack(spacing: 12) {
            Text("\(episode.episode)").font(.subheadline.monospacedDigit()).foregroundStyle(.secondary).frame(width: 26)
            VStack(alignment: .leading, spacing: 3) {
                Text(episode.title).font(.subheadline).foregroundStyle(.primary).lineLimit(2)
                if episode.progress > 0.02 {
                    ProgressView(value: min(episode.progress, 1)).tint(JarvisTheme.amber).frame(maxWidth: 160)
                }
            }
            Spacer()
            if starting == episode.id {
                ProgressView()
            } else if episode.watched {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.secondary)
            }
        }
    }

    private func choose(_ episode: PlexEpisode) {
        if episode.resumable { chosen = episode } else { start(episode, fromStart: false) }
    }

    private func start(_ episode: PlexEpisode, fromStart: Bool) {
        starting = episode.id
        Task {
            defer { starting = nil }
            do {
                _ = try await JarvisAPIClient.make(from: settings).playOnTV(ratingKey: episode.ratingKey, fromStart: fromStart)
                done()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// One poster tile: artwork through Core, title and a line under it, a thin
/// progress bar for something partway through.
private struct PosterTile: View {
    @EnvironmentObject private var settings: AppSettings
    let title: String
    let subtitle: String?
    let thumb: String
    let progress: Double
    let watched: Bool
    let busy: Bool
    @State private var image: UIImage?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack(alignment: .bottom) {
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.white.opacity(0.06))
                    .aspectRatio(2 / 3, contentMode: .fit)
                    .overlay {
                        if let image {
                            Image(uiImage: image).resizable().scaledToFill()
                        } else {
                            Image(systemName: "film").font(.largeTitle).foregroundStyle(.white.opacity(0.25))
                        }
                    }
                    .clipShape(.rect(cornerRadius: 10))
                if progress > 0.02 && progress < 0.999 {
                    GeometryReader { proxy in
                        Capsule().fill(JarvisTheme.amber.opacity(0.85))
                            .frame(width: proxy.size.width * min(progress, 1), height: 3)
                    }
                    .frame(height: 3)
                    .padding(.horizontal, 6)
                    .padding(.bottom, 6)
                }
                if busy {
                    ProgressView().padding(8).background(.black.opacity(0.6), in: .circle).padding(.bottom, 40)
                }
            }
            .overlay(alignment: .topTrailing) {
                if watched {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption).foregroundStyle(.white.opacity(0.7)).padding(6)
                }
            }
            Text(title).font(.subheadline.weight(.medium)).lineLimit(2).foregroundStyle(.white.opacity(0.85))
            if let subtitle {
                Text(subtitle).font(.caption).foregroundStyle(.white.opacity(0.45))
            }
        }
        .task(id: thumb) {
            guard !thumb.isEmpty, image == nil else { return }
            if let data = try? await JarvisAPIClient.make(from: settings).plexPoster(path: thumb, width: 300) {
                image = UIImage(data: data)
            }
        }
    }
}
