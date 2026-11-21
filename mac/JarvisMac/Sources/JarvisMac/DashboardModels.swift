import AppKit
import Foundation

// MARK: - Overview

/// Everything the Overview page shows, each piece loaded on its own so one
/// slow service never blanks the rest.
@MainActor
final class OverviewModel: ObservableObject {
    @Published private(set) var now: NowSnapshot?
    @Published private(set) var services: [ServiceState] = []
    @Published private(set) var storage: StorageState?
    @Published private(set) var presence: PresenceState?
    @Published private(set) var downloads: TorrentList?
    @Published private(set) var online: Bool?
    @Published var error: String?

    private let settings: MacSettings

    init(settings: MacSettings) { self.settings = settings }

    func load() async {
        guard let api = try? JarvisAPI.make(settings) else { return }
        async let n = try? api.now()
        async let s = try? api.services()
        async let st = try? api.storage()
        async let p = try? api.presence()
        async let d = try? api.torrents()
        let (nowValue, servicesValue, storageValue, presenceValue, downloadsValue) = await (n, s, st, p, d)
        now = nowValue
        services = servicesValue ?? services
        storage = storageValue
        presence = presenceValue
        downloads = downloadsValue
        online = nowValue != nil || servicesValue != nil
        error = online == false ? "JARVIS Core isn't answering." : nil
    }

    var servicesDown: [ServiceState] { services.filter { !$0.up } }
    var activeDownloads: [Torrent] { (downloads?.torrents ?? []).filter { !$0.done } }
}

// MARK: - Media

@MainActor
final class MediaModel: ObservableObject {
    @Published private(set) var torrents: TorrentList?
    @Published private(set) var busyTorrents: Set<String> = []
    @Published var kind = "movies"
    @Published var sort = "recent"
    @Published var query = ""
    @Published private(set) var titles: [PlexTitle] = []
    @Published private(set) var libraries: [PlexLibrary] = []
    @Published private(set) var loadingTitles = false
    @Published private(set) var starting: String?
    @Published var notice: String?
    @Published var error: String?
    private var posters: [String: NSImage] = [:]

    private let settings: MacSettings

    init(settings: MacSettings) { self.settings = settings }

    func loadTorrents() async {
        do {
            torrents = try await JarvisAPI.make(settings).torrents()
            if let problem = torrents?.error, !problem.isEmpty { error = problem }
        } catch {
            self.error = error.localizedDescription
        }
    }

    func toggle(_ torrent: Torrent) async {
        guard let hash = torrent.hash, !busyTorrents.contains(hash) else { return }
        busyTorrents.insert(hash)
        defer { busyTorrents.remove(hash) }
        do {
            try await JarvisAPI.make(settings).torrentControl(hash: hash, action: torrent.isPaused ? "resume" : "pause")
            await loadTorrents()
        } catch {
            self.error = error.localizedDescription
        }
    }

    func loadTitles() async {
        loadingTitles = true
        defer { loadingTitles = false }
        do {
            let api = try JarvisAPI.make(settings)
            titles = kind == "movies" ? try await api.plexMovies(query: query, sort: sort)
                                      : try await api.plexShows(query: query, sort: sort)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func loadLibraries() async {
        libraries = (try? await JarvisAPI.make(settings).plexLibraries()) ?? libraries
    }

    func setLibrary(_ library: PlexLibrary, shown: Bool) async {
        var hidden = Set(libraries.filter(\.hidden).map(\.key))
        if shown { hidden.remove(library.key) } else { hidden.insert(library.key) }
        if let updated = try? await JarvisAPI.make(settings).setHiddenLibraries(Array(hidden)) {
            libraries = updated
            await loadTitles()
        }
    }

    /// A film resumes unless `fromStart`; a show plays its next-up episode.
    func play(_ title: PlexTitle, fromStart: Bool = false) async {
        starting = title.id
        defer { starting = nil }
        do {
            notice = try await JarvisAPI.make(settings).playOnTV(ratingKey: title.ratingKey, fromStart: fromStart)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func poster(for path: String) async -> NSImage? {
        if let cached = posters[path] { return cached }
        guard !path.isEmpty, let data = try? await JarvisAPI.make(settings).poster(path: path),
              let image = NSImage(data: data) else { return nil }
        posters[path] = image
        return image
    }
}

// MARK: - Skills and fixes

@MainActor
final class SkillsModel: ObservableObject {
    @Published private(set) var skills: [LearnedSkill] = []
    @Published private(set) var wishes: [SkillWish] = []
    @Published private(set) var repairs: [RepairItem] = []
    @Published private(set) var busy: Set<String> = []
    @Published var error: String?

    private let settings: MacSettings

    init(settings: MacSettings) { self.settings = settings }

    var pendingCount: Int { skills.filter(\.isDraft).count + repairs.filter(\.isPending).count }

    func load() async {
        guard let api = try? JarvisAPI.make(settings) else { return }
        async let s = try? api.skills()
        async let w = try? api.skillWishes()
        async let r = try? api.repairs()
        let (skillList, wishList, repairList) = await (s, w, r)
        skills = skillList ?? skills
        wishes = wishList ?? wishes
        repairs = repairList ?? repairs
    }

    func setSkill(_ skill: LearnedSkill, status: String) async {
        await run(skill.id) { try await $0.setSkill(id: skill.id, status: status) }
    }

    func closeWish(_ wish: SkillWish, status: String) async {
        await run(wish.id) { try await $0.closeWish(id: wish.id, status: status) }
    }

    /// The drafter works in the background; poll until it answers.
    func draft(_ wish: SkillWish) async {
        await run(wish.id) { try await $0.draftSkill(wishID: wish.id) }
        for _ in 0..<40 {
            try? await Task.sleep(for: .seconds(3))
            await load()
            if wishes.first(where: { $0.id == wish.id })?.drafting != true { break }
        }
    }

    func resolve(_ repair: RepairItem, approve: Bool) async {
        await run(repair.id) { try await $0.resolveRepair(id: repair.id, approve: approve) }
    }

    private func run(_ id: String, _ action: (JarvisAPI) async throws -> Void) async {
        busy.insert(id)
        defer { busy.remove(id) }
        do {
            try await action(JarvisAPI.make(settings))
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        await load()
    }
}
