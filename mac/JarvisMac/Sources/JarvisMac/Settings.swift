import Foundation
import Network
import Security

/// Where Core lives and the companion token. Two addresses, like the phone
/// (REMOTE_AND_MONEY_PLAN.md, Part 1): home (the Surface on the LAN) and away
/// (`jarvis.nickolaspearson.com` through Lightsail, which wants this Mac's
/// device certificate). The app uses home whenever it answers, else away.
/// Addresses sit in UserDefaults; the token and certificate in private files
/// (see "Token file").
@MainActor
final class MacSettings: ObservableObject {
    static let defaultServerPort = 8000
    static let defaultAwayAddress = "https://jarvis.nickolaspearson.com"
    private static let addressKey = "serverAddress"
    private static let awayKey = "awayAddress"
    /// The settings JarvisAPI reports unreachable addresses to.
    static weak var current: MacSettings?

    @Published var address: String {
        didSet { UserDefaults.standard.set(address, forKey: Self.addressKey); refreshRoute() }
    }
    @Published var awayAddress: String {
        didSet { UserDefaults.standard.set(awayAddress, forKey: Self.awayKey); refreshRoute() }
    }
    @Published var token: String {
        didSet { Self.saveToken(token) }
    }
    /// True while the home address doesn't answer.
    @Published private(set) var away = false
    @Published private(set) var certificateName: String?

    private let monitor = NWPathMonitor()
    private var probe: Task<Void, Never>?

    init() {
        address = UserDefaults.standard.string(forKey: Self.addressKey) ?? ""
        awayAddress = UserDefaults.standard.string(forKey: Self.awayKey) ?? Self.defaultAwayAddress
        token = Self.loadToken() ?? ""
        certificateName = MacDeviceCertificate.name
        Self.current = self
        monitor.pathUpdateHandler = { [weak self] _ in Task { @MainActor in self?.refreshRoute() } }
        monitor.start(queue: DispatchQueue(label: "jarvis.mac.route"))
        refreshRoute()
    }

    var isConfigured: Bool { baseURL != nil && !token.trimmingCharacters(in: .whitespaces).isEmpty }

    var homeURL: URL? { Self.resolve(address) }
    var awayURL: URL? { Self.resolve(awayAddress) }

    /// The address in use: home while it answers, else away.
    var baseURL: URL? { (away ? awayURL : homeURL) ?? homeURL ?? awayURL }

    var routeLabel: String { away ? "away (\(awayURL?.host() ?? "nickolaspearson.com"))" : "at home" }

    /// Ask the home address (1.5 s); any HTTP answer means home.
    func refreshRoute() {
        probe?.cancel()
        guard let home = homeURL else { away = awayURL != nil; return }
        probe = Task {
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 1.5
            let session = URLSession(configuration: config)
            defer { session.invalidateAndCancel() }
            let answered = (try? await session.data(from: home.appending(path: "health")))?.1 is HTTPURLResponse
            guard !Task.isCancelled else { return }
            away = !answered && awayURL != nil
        }
    }

    func importCertificate(_ data: Data, passphrase: String) throws {
        certificateName = try MacDeviceCertificate.install(data, passphrase: passphrase)
    }

    func removeCertificate() {
        MacDeviceCertificate.remove()
        certificateName = nil
    }

    /// Same forgiving rules as the phone: a bare host gets http:// and, for
    /// plain http with no port, Core's default port 8000.
    static func resolve(_ address: String) -> URL? {
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

/// This Mac's away-access certificate: the `.p12` and its passphrase in the
/// same private folder as the token (0600), opened in memory only so the
/// ad hoc-signed app never touches the login keychain.
enum MacDeviceCertificate {
    private static var folder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appending(path: "JARVIS")
    }
    private static var p12URL: URL { folder.appending(path: "device.p12") }
    private static var passURL: URL { folder.appending(path: "device.pass") }
    nonisolated(unsafe) private static var cached: (URLCredential, String)?
    private static let lock = NSLock()

    enum ImportError: LocalizedError {
        case wrongPassphrase, unreadable
        var errorDescription: String? {
            self == .wrongPassphrase ? "That passphrase doesn't open the certificate." : "That file isn't a device certificate (.p12)."
        }
    }

    static var credential: URLCredential? { loaded()?.0 }
    static var name: String? { loaded()?.1 }

    private static func loaded() -> (URLCredential, String)? {
        lock.lock(); defer { lock.unlock() }
        if cached == nil, let data = try? Data(contentsOf: p12URL),
           let pass = try? String(contentsOf: passURL, encoding: .utf8) {
            cached = try? open(data, passphrase: pass)
        }
        return cached
    }

    static func install(_ data: Data, passphrase: String) throws -> String {
        let opened = try open(data, passphrase: passphrase)
        let fm = FileManager.default
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        fm.createFile(atPath: p12URL.path, contents: data, attributes: [.posixPermissions: 0o600])
        fm.createFile(atPath: passURL.path, contents: Data(passphrase.utf8), attributes: [.posixPermissions: 0o600])
        lock.lock(); cached = opened; lock.unlock()
        return opened.1
    }

    static func remove() {
        try? FileManager.default.removeItem(at: p12URL)
        try? FileManager.default.removeItem(at: passURL)
        lock.lock(); cached = nil; lock.unlock()
    }

    private static func open(_ data: Data, passphrase: String) throws -> (URLCredential, String) {
        var items: CFArray?
        let options: [String: Any] = [kSecImportExportPassphrase as String: passphrase,
                                      kSecImportToMemoryOnly as String: true]
        let status = SecPKCS12Import(data as CFData, options as CFDictionary, &items)
        guard status == errSecSuccess else {
            throw status == errSecAuthFailed || status == errSecPkcs12VerifyFailure ? ImportError.wrongPassphrase : ImportError.unreadable
        }
        guard let first = (items as? [[String: Any]])?.first,
              let identityRef = first[kSecImportItemIdentity as String] else { throw ImportError.unreadable }
        let identity = identityRef as! SecIdentity  // swiftlint:disable:this force_cast
        var certificate: SecCertificate?
        SecIdentityCopyCertificate(identity, &certificate)
        let name = certificate.flatMap { SecCertificateCopySubjectSummary($0) as String? } ?? "Device certificate"
        let chain = (first[kSecImportItemCertChain as String] as? [SecCertificate] ?? []).dropFirst()
        return (URLCredential(identity: identity, certificates: Array(chain), persistence: .forSession), name)
    }
}

/// Answers Caddy's client-certificate challenge with this Mac's certificate.
final class MacCertificateDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    static let session: URLSession = {
        let config = URLSessionConfiguration.default
        return URLSession(configuration: config, delegate: MacCertificateDelegate(), delegateQueue: nil)
    }()

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodClientCertificate,
           let credential = MacDeviceCertificate.credential {
            completionHandler(.useCredential, credential)
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
