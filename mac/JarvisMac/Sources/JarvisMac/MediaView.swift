import AppKit
import SwiftUI

/// Media: what's downloading (pause/resume), and the Plex wall: every movie or
/// show as a poster; a click starts it on the room TV through JARVIS TV.
struct MediaView: View {
    @ObservedObject var model: MediaModel
    @State private var chosen: PlexTitle?

    private let columns = [GridItem(.adaptive(minimum: 140, maximum: 180), spacing: 18)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let error = model.error { ErrorLine(text: error) }
                if let notice = model.notice {
                    Label(notice, systemImage: "tv").foregroundStyle(JarvisTheme.accent)
                }
                downloads
                library
            }
            .padding(24)
        }
        .searchable(text: $model.query, placement: .toolbar, prompt: "Search Plex")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Picker("Library", selection: $model.kind) {
                    Text("Movies").tag("movies")
                    Text("TV Shows").tag("shows")
                }
                .pickerStyle(.segmented)
                .frame(width: 180)
                Menu {
                    Picker("Sort", selection: $model.sort) {
                        Text(model.kind == "shows" ? "Recently watched" : "Recently added").tag("recent")
                        Text("Unwatched").tag("unwatched")
                        Text("A–Z").tag("title")
                    }
                    if !model.libraries.isEmpty {
                        Section("Show these libraries") {
                            ForEach(model.libraries) { library in
                                Toggle(library.title, isOn: Binding(get: { !library.hidden }, set: { shown in
                                    Task { await model.setLibrary(library, shown: shown) }
                                }))
                            }
                        }
                    }
                } label: { Label("Sort and libraries", systemImage: "line.3.horizontal.decrease.circle") }
                .help("Sort, and which Plex libraries show here")
            }
        }
        .task(id: "\(model.kind)|\(model.sort)|\(model.query)") {
            if !model.query.isEmpty { try? await Task.sleep(for: .milliseconds(350)) }
            guard !Task.isCancelled else { return }
            await model.loadTitles()
        }
        .task {
            await model.loadLibraries()
            while !Task.isCancelled {
                await model.loadTorrents()
                try? await Task.sleep(for: .seconds(5))
            }
        }
        .confirmationDialog(chosen?.title ?? "", isPresented: Binding(get: { chosen != nil }, set: { if !$0 { chosen = nil } }),
                            presenting: chosen) { title in
            if model.kind == "shows" {
                Button("Play next episode on the TV") { Task { await model.play(title) } }
            } else if title.resumable {
                Button("Resume from \(Format.clock(ms: title.viewOffsetMs ?? 0))") { Task { await model.play(title) } }
                Button("Start over") { Task { await model.play(title, fromStart: true) } }
            } else {
                Button("Play on the TV") { Task { await model.play(title) } }
            }
        }
    }

    // MARK: Downloads

    @ViewBuilder private var downloads: some View {
        let list = model.torrents?.torrents.filter { !$0.done } ?? []
        Panel("Downloading", symbol: "arrow.down.circle", trailing: {
            if let torrents = model.torrents {
                Pill(text: "↓ \(Format.speed(torrents.dlSpeed))   ↑ \(Format.speed(torrents.upSpeed))")
            }
        }) {
            if list.isEmpty {
                Label("Nothing downloading.", systemImage: "tray").foregroundStyle(.secondary)
            } else {
                VStack(spacing: 12) {
                    ForEach(list) { torrent in
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(torrent.name).lineLimit(1)
                                ProgressView(value: torrent.progress).tint(torrent.isPaused ? .gray : JarvisTheme.accent)
                                Text("\(Int(torrent.progress * 100))% · \(Format.bytes(Int64(torrent.size))) · "
                                     + (torrent.isPaused ? "Paused" : "\(Format.speed(torrent.dlSpeed)), \(Format.eta(torrent.eta)) left"))
                                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            }
                            if torrent.hash != nil {
                                Button { Task { await model.toggle(torrent) } } label: {
                                    Image(systemName: torrent.isPaused ? "play.fill" : "pause.fill").frame(width: 20)
                                }
                                .disabled(model.busyTorrents.contains(torrent.hash ?? ""))
                                .help(torrent.isPaused ? "Resume" : "Pause")
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: Library

    private var library: some View {
        Panel(model.kind == "shows" ? "TV Shows" : "Movies", symbol: "film.stack", trailing: {
            if model.loadingTitles { ProgressView().controlSize(.small) }
        }) {
            if model.titles.isEmpty && !model.loadingTitles {
                Label(model.query.isEmpty ? "Plex has nothing here." : "Nothing matches \u{201C}\(model.query)\u{201D}.",
                      systemImage: "magnifyingglass").foregroundStyle(.secondary)
            }
            LazyVGrid(columns: columns, spacing: 20) {
                ForEach(model.titles) { title in
                    Button { chosen = title } label: {
                        PosterCard(title: title, model: model, starting: model.starting == title.id)
                    }
                    .buttonStyle(.plain)
                    .help("Play \(title.title) on the TV")
                }
            }
        }
    }
}

private struct PosterCard: View {
    let title: PlexTitle
    @ObservedObject var model: MediaModel
    let starting: Bool
    @State private var image: NSImage?
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack(alignment: .bottom) {
                RoundedRectangle(cornerRadius: 10).fill(Color.white.opacity(0.05))
                    .aspectRatio(2 / 3, contentMode: .fit)
                    .overlay {
                        if let image {
                            Image(nsImage: image).resizable().scaledToFill()
                        } else {
                            Image(systemName: "film").font(.largeTitle).foregroundStyle(.white.opacity(0.2))
                        }
                    }
                    .clipShape(.rect(cornerRadius: 10))
                if title.progress > 0.02 && title.progress < 0.999 {
                    GeometryReader { proxy in
                        Capsule().fill(JarvisTheme.gold).frame(width: proxy.size.width * title.progress, height: 3)
                    }
                    .frame(height: 3).padding(6)
                }
                if starting { ProgressView().padding(.bottom, 40) }
            }
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(hovering ? JarvisTheme.accent : .clear, lineWidth: 2))
            .shadow(color: hovering ? JarvisTheme.accent.opacity(0.35) : .clear, radius: 10)
            .scaleEffect(hovering ? 1.02 : 1)
            .animation(.easeOut(duration: 0.15), value: hovering)
            Text(title.title).font(.callout.weight(.medium)).lineLimit(2)
            if let year = title.year { Text(String(year)).font(.caption).foregroundStyle(.secondary) }
        }
        .onHover { hovering = $0 }
        .task(id: title.thumb) { image = await model.poster(for: title.thumb) }
    }
}
