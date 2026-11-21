import SwiftUI
import UIKit

/// Movie mode's poster wall: every Plex film, searchable, sorted by recently
/// added / A–Z / unwatched. A tap asks Resume or Start over (or just Play)
/// and starts it on the room TV through Core. Dark and still, like the rest
/// of movie mode.
struct MoviePickerView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var movies: [PlexMovie] = []
    @State private var query = ""
    @State private var sort = "recent"
    @State private var loading = true
    @State private var error: String?
    @State private var chosen: PlexMovie?
    @State private var starting: String?
    @State private var started = 0

    private static let sorts = [("recent", "Recently added"), ("unwatched", "Unwatched"), ("title", "A–Z")]

    var body: some View {
        NavigationStack {
            ScrollView {
                if let error {
                    ContentUnavailableView("Plex isn't answering", systemImage: "film.stack",
                                           description: Text(error))
                        .padding(.top, 60)
                } else if movies.isEmpty && !loading {
                    ContentUnavailableView.search(text: query)
                        .padding(.top, 60)
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: sizeClass == .regular ? 150 : 105, maximum: 200), spacing: 16)], spacing: 20) {
                        ForEach(movies) { movie in
                            Button { chosen = movie } label: { MoviePoster(movie: movie, starting: starting == movie.id) }
                                .buttonStyle(.plain)
                                .accessibilityLabel(movie.year.map { "\(movie.title), \($0)" } ?? movie.title)
                        }
                    }
                    .padding(sizeClass == .regular ? 20 : 14)
                }
            }
            .overlay { if loading && movies.isEmpty { ProgressView() } }
            .background(Color.black.opacity(0.92).ignoresSafeArea())
            .navigationTitle("Pick a movie")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, prompt: "Search Plex")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                if sizeClass == .regular {
                    ToolbarItem(placement: .principal) {
                        Picker("Sort", selection: $sort) {
                            ForEach(Self.sorts, id: \.0) { Text($0.1).tag($0.0) }
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 360)
                    }
                } else {
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            Picker("Sort", selection: $sort) {
                                ForEach(Self.sorts, id: \.0) { Text($0.1).tag($0.0) }
                            }
                        } label: { Label("Sort", systemImage: "arrow.up.arrow.down") }
                    }
                }
            }
            .task(id: "\(query)|\(sort)") {
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
            movies = try await JarvisAPIClient.make(from: settings).plexMovies(query: query, sort: sort)
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
                _ = try await JarvisAPIClient.make(from: settings).playOnTV(movie, fromStart: fromStart)
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

/// One poster tile: artwork through Core, title and year under it, a thin
/// resume bar for a film he's partway through.
private struct MoviePoster: View {
    @EnvironmentObject private var settings: AppSettings
    let movie: PlexMovie
    let starting: Bool
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
                if movie.progress > 0.02 {
                    GeometryReader { proxy in
                        Capsule().fill(JarvisTheme.amber.opacity(0.85))
                            .frame(width: proxy.size.width * min(movie.progress, 1), height: 3)
                    }
                    .frame(height: 3)
                    .padding(.horizontal, 6)
                    .padding(.bottom, 6)
                }
                if starting {
                    ProgressView().padding(8).background(.black.opacity(0.6), in: .circle).padding(.bottom, 40)
                }
            }
            .overlay(alignment: .topTrailing) {
                if movie.watched && movie.progress == 0 {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption).foregroundStyle(.white.opacity(0.7)).padding(6)
                }
            }
            Text(movie.title).font(.subheadline.weight(.medium)).lineLimit(2).foregroundStyle(.white.opacity(0.85))
            if let year = movie.year {
                Text(String(year)).font(.caption).foregroundStyle(.white.opacity(0.45))
            }
        }
        .task(id: movie.thumb) {
            guard !movie.thumb.isEmpty, image == nil else { return }
            if let data = try? await JarvisAPIClient.make(from: settings).plexPoster(path: movie.thumb, width: 300) {
                image = UIImage(data: data)
            }
        }
    }
}
