import Foundation

/// Where JARVIS Core lives and how to authenticate against it.
///
/// Backed by `UserDefaults` for now. The token belongs in the Keychain
/// eventually — every read and write is funnelled through this one type so
/// that swap stays a single-file change.
@MainActor
final class AppSettings: ObservableObject {
    private enum Key {
        static let baseURL = "jarvis.baseURL"
        static let apiToken = "jarvis.apiToken"
    }

    private let defaults: UserDefaults

    @Published var baseURLString: String {
        didSet { defaults.set(baseURLString, forKey: Key.baseURL) }
    }

    @Published var apiToken: String {
        didSet { defaults.set(apiToken, forKey: Key.apiToken) }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        baseURLString = defaults.string(forKey: Key.baseURL) ?? ""
        apiToken = defaults.string(forKey: Key.apiToken) ?? ""
    }

    /// The base URL with the rough edges knocked off: surrounding whitespace
    /// trimmed, a trailing slash dropped, and a scheme added when the user
    /// typed a bare host (`100.x.y.z:8000`), which is the common case for a
    /// Tailscale address.
    var resolvedBaseURL: URL? {
        var text = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }

        if !text.contains("://") {
            text = "http://" + text
        }
        while text.hasSuffix("/") {
            text.removeLast()
        }

        guard let url = URL(string: text),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              url.host?.isEmpty == false
        else { return nil }

        return url
    }

    var isConfigured: Bool {
        resolvedBaseURL != nil && !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
