import Foundation
import Security
import CryptoKit

/// Where JARVIS Core lives and how to authenticate against it.
///
/// Two addresses, picked on their own by `ServerRoute`: home (the Surface on
/// the LAN) and away (`jarvis.nickolaspearson.com` through Lightsail, which
/// needs this device's certificate). An optional Tailscale address is the
/// last resort. Addresses are harmless UserDefaults state; the bearer token
/// is stored in the iOS Keychain and migrated out of UserDefaults on first launch.
@MainActor
final class AppSettings: ObservableObject {
    private enum Key {
        static let baseURL = "jarvis.baseURL"  // before home/away (6.124): migrated
        static let homeURL = "jarvis.homeURL"
        static let awayURL = "jarvis.awayURL"
        static let fallbackURL = "jarvis.fallbackURL"
        static let routeMode = "jarvis.routeMode"
        static let apiToken = "jarvis.apiToken"
        static let studyAlerts = "jarvis.studyAlerts"
        static let studyHour = "jarvis.studyHour"
    }

    private let defaults: UserDefaults
    private let keychain: any TokenStoring

    static let defaultHomeURL = "http://10.0.0.73:8000"
    static let defaultAwayURL = "https://jarvis.nickolaspearson.com"

    @Published var homeURLString: String {
        didSet { defaults.set(homeURLString, forKey: Key.homeURL); routeChanged() }
    }
    @Published var awayURLString: String {
        didSet { defaults.set(awayURLString, forKey: Key.awayURL); routeChanged() }
    }
    /// The old Tailscale address, kept as a last resort (blank = none).
    @Published var fallbackURLString: String {
        didSet { defaults.set(fallbackURLString, forKey: Key.fallbackURL); routeChanged() }
    }
    @Published var routeMode: ServerRoute.Mode {
        didSet { defaults.set(routeMode.rawValue, forKey: Key.routeMode); routeChanged() }
    }

    private func routeChanged() {
        if didInit { ServerRoute.shared.reevaluate() }
    }
    private var didInit = false

    @Published var apiToken: String {
        didSet {
            tokenStorageError = keychain.save(apiToken) ? nil : "Could not save the token securely. Please try again before closing the app."
        }
    }

    @Published private(set) var tokenStorageError: String?
    @Published var studyAlertsEnabled: Bool {
        didSet { defaults.set(studyAlertsEnabled, forKey: Key.studyAlerts) }
    }
    @Published var studyAlertHour: Int {
        didSet { defaults.set(studyAlertHour, forKey: Key.studyHour) }
    }

    /// Namespace caches without persisting credentials in their keys. Stable
    /// across home/away switches: it's the same Core either way.
    var connectionIdentity: String {
        let addresses = ServerRoute.Kind.allCases.map { url(for: $0)?.absoluteString ?? "" }.joined(separator: "|")
        return SHA256.hash(data: Data("\(addresses)|\(apiToken)".utf8))
            .map { String(format: "%02x", $0) }.joined()
    }

    init(defaults: UserDefaults = .standard, keychain: any TokenStoring = KeychainTokenStore()) {
        self.defaults = defaults
        self.keychain = keychain
        studyAlertsEnabled = defaults.object(forKey: Key.studyAlerts) as? Bool ?? true
        studyAlertHour = min(23, max(0, defaults.object(forKey: Key.studyHour) as? Int ?? 9))
        let migrated = Self.migrate(defaults)
        homeURLString = migrated.home
        awayURLString = migrated.away
        fallbackURLString = migrated.fallback
        routeMode = ServerRoute.Mode(rawValue: defaults.string(forKey: Key.routeMode) ?? "") ?? .auto
        let legacyToken = defaults.string(forKey: Key.apiToken) ?? ""
        let storedToken = keychain.load()
        apiToken = storedToken ?? legacyToken
        if !legacyToken.isEmpty {
            if storedToken != nil || keychain.save(legacyToken) {
                defaults.removeObject(forKey: Key.apiToken)
            } else {
                tokenStorageError = "Could not migrate the token to Keychain. It will retry next launch."
            }
        }
        didInit = true
    }

    /// One address per slot. The old single address moves into the slot it
    /// matches: a Tailscale `100.x` one becomes the fallback, an https or
    /// nickolaspearson.com one the away address, anything else home.
    private static func migrate(_ defaults: UserDefaults) -> (home: String, away: String, fallback: String) {
        if defaults.object(forKey: Key.homeURL) != nil {
            return (defaults.string(forKey: Key.homeURL) ?? "", defaults.string(forKey: Key.awayURL) ?? defaultAwayURL,
                    defaults.string(forKey: Key.fallbackURL) ?? "")
        }
        var home = defaultHomeURL, away = defaultAwayURL, fallback = ""
        let old = (defaults.string(forKey: Key.baseURL) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if let url = resolve(old), let host = url.host {
            if host.hasPrefix("100.") || host.hasSuffix(".ts.net") { fallback = old }
            else if url.scheme == "https" || host.hasSuffix("nickolaspearson.com") { away = old }
            else { home = old }
        }
        defaults.set(home, forKey: Key.homeURL)
        defaults.set(away, forKey: Key.awayURL)
        defaults.set(fallback, forKey: Key.fallbackURL)
        return (home, away, fallback)
    }

    func url(for kind: ServerRoute.Kind) -> URL? {
        switch kind {
        case .home: return Self.resolve(homeURLString)
        case .away: return Self.resolve(awayURLString)
        case .fallback: return Self.resolve(fallbackURLString)
        }
    }

    /// The address in use right now (`ServerRoute` picks home or away).
    var resolvedBaseURL: URL? {
        url(for: ServerRoute.shared.current) ?? ServerRoute.Kind.allCases.lazy.compactMap { self.url(for: $0) }.first
    }

    /// Any address set at all.
    var hasAddress: Bool { ServerRoute.Kind.allCases.contains { url(for: $0) != nil } }

    /// The port JARVIS Core actually serves on (`JARVIS_SERVER_PORT` default).
    static let defaultServerPort = 8000

    /// The base URL with the rough edges knocked off: surrounding whitespace
    /// trimmed, a trailing slash dropped, a scheme added when the user typed a
    /// bare host, and the server's real port (8000) filled in when a plain
    /// http address names none — so `100.x.y.z` just works for the common
    /// Tailscale case. An explicit port and https URLs are left alone.
    static func resolve(_ address: String) -> URL? {
        var text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }

        if !text.contains("://") {
            text = "http://" + text
        }
        while text.hasSuffix("/") {
            text.removeLast()
        }

        guard var components = URLComponents(string: text),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              components.host?.isEmpty == false
        else { return nil }

        if scheme == "http", components.port == nil {
            components.port = Self.defaultServerPort
        }

        return components.url
    }

    var isConfigured: Bool {
        hasAddress && !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

protocol TokenStoring {
    func load() -> String?
    @discardableResult func save(_ token: String) -> Bool
}

final class KeychainTokenStore: TokenStoring {
    private let service: String

    init(service: String = "com.nickolaspearson.JarvisCompanion") {
        self.service = service
    }
    private let account = "jarvis-api-token"

    func load() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    func save(_ token: String) -> Bool {
        if token.isEmpty {
            let status = SecItemDelete(baseQuery as CFDictionary)
            return status == errSecSuccess || status == errSecItemNotFound
        }
        let attributes: [String: Any] = [
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let status = SecItemUpdate(baseQuery as CFDictionary, attributes as CFDictionary)
        if status == errSecSuccess { return true }
        guard status == errSecItemNotFound else { return false }
        return SecItemAdd(baseQuery.merging(attributes) { _, new in new } as CFDictionary, nil) == errSecSuccess
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
