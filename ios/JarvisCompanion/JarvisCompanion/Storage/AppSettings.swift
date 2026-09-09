import Foundation
import Security

/// Where JARVIS Core lives and how to authenticate against it.
///
/// The address is harmless UserDefaults state; the bearer token is stored in
/// the iOS Keychain and migrated out of UserDefaults on first launch.
@MainActor
final class AppSettings: ObservableObject {
    private enum Key {
        static let baseURL = "jarvis.baseURL"
        static let apiToken = "jarvis.apiToken"
    }

    private let defaults: UserDefaults
    private let keychain: KeychainTokenStore

    @Published var baseURLString: String {
        didSet { defaults.set(baseURLString, forKey: Key.baseURL) }
    }

    @Published var apiToken: String {
        didSet {
            tokenStorageError = keychain.save(apiToken) ? nil : "Could not save the token securely. Please try again before closing the app."
        }
    }

    @Published private(set) var tokenStorageError: String?

    init(defaults: UserDefaults = .standard, keychain: KeychainTokenStore = KeychainTokenStore()) {
        self.defaults = defaults
        self.keychain = keychain
        baseURLString = defaults.string(forKey: Key.baseURL) ?? ""
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
    }

    /// The port JARVIS Core actually serves on (`JARVIS_SERVER_PORT` default).
    static let defaultServerPort = 8000

    /// The base URL with the rough edges knocked off: surrounding whitespace
    /// trimmed, a trailing slash dropped, a scheme added when the user typed a
    /// bare host, and the server's real port (8000) filled in when a plain
    /// http address names none — so `100.x.y.z` just works for the common
    /// Tailscale case. An explicit port and https URLs are left alone.
    var resolvedBaseURL: URL? {
        var text = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
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
        resolvedBaseURL != nil && !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

final class KeychainTokenStore {
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
