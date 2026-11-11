import Foundation

/// Where Core lives and the companion token. The address sits in
/// UserDefaults; the token in a private file (see "Token file").
@MainActor
final class MacSettings: ObservableObject {
    static let defaultServerPort = 8000
    private static let addressKey = "serverAddress"

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

    // MARK: Token file
    // Not the Keychain: this app is signed ad hoc, so every rebuild would
    // ask for the login password again. A file only Nick's account can read
    // (0600, in a 0700 folder) is the same protection the user account gives.

    private static var tokenURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "JARVIS/token")
    }

    private static func loadToken() -> String? {
        guard let data = try? Data(contentsOf: tokenURL) else { return nil }
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func saveToken(_ token: String) {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        let fm = FileManager.default
        guard !trimmed.isEmpty else {
            try? fm.removeItem(at: tokenURL)
            return
        }
        let folder = tokenURL.deletingLastPathComponent()
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        fm.createFile(atPath: tokenURL.path, contents: Data(trimmed.utf8), attributes: [.posixPermissions: 0o600])
        try? fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: tokenURL.path)
    }
}
