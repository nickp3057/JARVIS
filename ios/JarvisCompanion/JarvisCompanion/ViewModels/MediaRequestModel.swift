import Foundation

/// Chat-free movie/show requests: search, tap one exact result, confirm.
/// The server's two-step gate means a tap only opens a proposal; nothing is
/// added until `confirm()` sends the proposal's code.
@MainActor
final class MediaRequestModel: ObservableObject {
    enum Kind: String, CaseIterable, Identifiable {
        case movie, show
        var id: String { rawValue }
        var label: String { self == .movie ? "Movie" : "Show" }
    }

    struct Proposal: Identifiable {
        let id = UUID()
        let title: String
        let description: String
        let code: String
    }

    @Published var query = ""
    @Published var kind: Kind = .movie
    @Published private(set) var results: [MediaSearchResult] = []
    @Published private(set) var isSearching = false
    @Published private(set) var busyID: Int?
    @Published var proposal: Proposal?
    /// Last outcome or error, shown under the search field.
    @Published var notice: String?
    @Published private(set) var noticeIsError = false
    /// Quality profiles for the current kind (Radarr's for movies, Sonarr's
    /// for shows) and the picked one; Core's default until Nick changes it.
    @Published private(set) var profiles: [String] = []
    @Published var profile = ""

    private let makeClient: () throws -> JarvisAPIClient
    private var searchTask: Task<Void, Never>?

    init(makeClient: @escaping () throws -> JarvisAPIClient) {
        self.makeClient = makeClient
    }

    func loadProfiles() async {
        let kind = kind.rawValue
        guard let response = try? await makeClient().mediaProfiles(kind: kind), kind == self.kind.rawValue else { return }
        profiles = response.profiles
        profile = response.profiles.contains(response.default) ? response.default : (response.profiles.first ?? "")
    }

    func search() {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        searchTask?.cancel()
        isSearching = true
        notice = nil
        let kind = kind.rawValue
        searchTask = Task { [weak self] in
            guard let self else { return }
            defer { isSearching = false }
            do {
                let response = try await makeClient().mediaSearch(query: text, kind: kind)
                guard !Task.isCancelled else { return }
                results = response.results
                if let error = response.error, !error.isEmpty {
                    show(error, isError: true)
                } else if response.results.isEmpty {
                    show("No matches. Try the exact title, or add the year.", isError: false)
                }
            } catch {
                guard !Task.isCancelled else { return }
                results = []
                show(error.localizedDescription, isError: true)
            }
        }
    }

    /// Step one: open the server-side proposal for this exact title.
    func request(_ item: MediaSearchResult) {
        guard busyID == nil else { return }
        busyID = item.id
        Task { [weak self] in
            guard let self else { return }
            defer { busyID = nil }
            do {
                let quality = profiles.contains(profile) ? profile : nil
                let response = try await makeClient().requestMedia(kind: item.kind, id: item.id, quality: quality)
                if response.status == "needs_confirmation", let code = response.code {
                    proposal = Proposal(title: item.title, description: response.description ?? "Add \(item.title)", code: code)
                } else {
                    show(response.message ?? "Couldn't request \(item.title).", isError: response.status != "exists")
                }
            } catch {
                show(error.localizedDescription, isError: true)
            }
        }
    }

    /// Step two: approve. Called only from the confirmation dialog.
    func confirm(_ proposal: Proposal) {
        self.proposal = nil
        Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await makeClient().confirmMediaRequest(code: proposal.code)
                show(response.message ?? response.status, isError: response.status != "added")
                if response.status == "added" { results = [] ; query = "" }
            } catch {
                show(error.localizedDescription, isError: true)
            }
        }
    }

    private func show(_ text: String, isError: Bool) {
        notice = text
        noticeIsError = isError
    }
}
