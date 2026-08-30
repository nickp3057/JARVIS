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
        didSet { keychain.save(apiToken) }
    }

    init(defaults: UserDefaults = .standard, keychain: KeychainTokenStore = KeychainTokenStore()) {
        self.defaults = defaults
        self.keychain = keychain
        baseURLString = defaults.string(forKey: Key.baseURL) ?? ""
        let legacyToken = defaults.string(forKey: Key.apiToken) ?? ""
        apiToken = keychain.load() ?? legacyToken
        if !legacyToken.isEmpty {
            keychain.save(legacyToken)
            defaults.removeObject(forKey: Key.apiToken)
        }
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

final class KeychainTokenStore {
    private let service = "com.nickolaspearson.JarvisCompanion"
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

    func save(_ token: String) {
        SecItemDelete(baseQuery as CFDictionary)
        guard !token.isEmpty else { return }
        var query = baseQuery
        query[kSecValueData as String] = Data(token.utf8)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(query as CFDictionary, nil)
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
