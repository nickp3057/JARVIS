import Foundation
import Security

/// Where Core lives and the companion token. The address sits in
/// UserDefaults; the token goes in the login Keychain.
@MainActor
final class MacSettings: ObservableObject {
    static let defaultServerPort = 8000
    private static let addressKey = "serverAddress"
    private static let service = "com.jarvis.donna-mac"
    private static let account = "JARVIS_API_TOKEN"

    @Published var address: String {
        didSet { UserDefaults.standard.set(address, forKey: Self.addressKey) }
    }
    @Published var token: String {
        didSet { Self.saveToken(token) }
    }

    init() {
        address = UserDefaults.standard.string(forKey: Self.addressKey) ?? ""
        token = Self.loadToken() ?? ""
    }

    var isConfigured: Bool { baseURL != nil && !token.trimmingCharacters(in: .whitespaces).isEmpty }

    /// Same forgiving rules as the phone: a bare host gets http:// and, for
    /// plain http with no port, Core's default port 8000.
    var baseURL: URL? {
        var text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") { text = "http://" + text }
        while text.hasSuffix("/") { text.removeLast() }
        guard var components = URLComponents(string: text),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              components.host?.isEmpty == false
        else { return nil }
        if scheme == "http", components.port == nil { components.port = Self.defaultServerPort }
        return components.url
    }

    // MARK: Keychain

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func loadToken() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func saveToken(_ token: String) {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        SecItemDelete(query as CFDictionary)
        guard !trimmed.isEmpty else { return }
        var q = query
        q[kSecValueData as String] = Data(trimmed.utf8)
        SecItemAdd(q as CFDictionary, nil)
    }
}
