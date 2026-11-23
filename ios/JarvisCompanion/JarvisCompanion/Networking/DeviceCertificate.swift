import Foundation
import Security

/// Away access's device certificate (REMOTE_AND_MONEY_PLAN.md, Part 1).
///
/// Away from home the app reaches Core through `jarvis.nickolaspearson.com`,
/// where Lightsail's Caddy refuses any device without a client certificate
/// signed by Nick's device CA (`ops/remote/make-device-cert.sh`). iOS apps
/// can't use certificates installed as profiles, so the app imports the
/// `.p12` itself and keeps it (with its passphrase) in its own Keychain, this
/// device only. Every request answers the TLS client-certificate challenge
/// with it (`ClientCertificateDelegate`); at home (plain http to the LAN) it
/// is never asked for.
final class DeviceCertificate: @unchecked Sendable {
    static let shared = DeviceCertificate()

    struct Summary: Equatable {
        let name: String
    }

    enum ImportError: LocalizedError {
        case wrongPassphrase, unreadable, noIdentity, keychain
        var errorDescription: String? {
            switch self {
            case .wrongPassphrase: return "That passphrase doesn't open the certificate."
            case .unreadable: return "That file isn't a device certificate (.p12)."
            case .noIdentity: return "The file has no certificate with its private key."
            case .keychain: return "Couldn't save the certificate in Keychain. Try again."
            }
        }
    }

    private let service = "com.nickolaspearson.JarvisCompanion.device-certificate"
    private let lock = NSLock()
    private var loaded = false
    private var cachedCredential: URLCredential?
    private var cachedSummary: Summary?

    /// The credential for a client-certificate challenge, or nil when none is installed.
    var credential: URLCredential? {
        lock.lock(); defer { lock.unlock() }
        loadIfNeeded()
        return cachedCredential
    }

    var summary: Summary? {
        lock.lock(); defer { lock.unlock() }
        loadIfNeeded()
        return cachedSummary
    }

    var isInstalled: Bool { credential != nil }

    /// Opens the `.p12` with its passphrase and, only if that works, keeps both.
    @discardableResult
    func install(_ data: Data, passphrase: String) throws -> Summary {
        let (credential, summary) = try Self.open(data, passphrase: passphrase)
        guard save(data, account: "p12"), save(Data(passphrase.utf8), account: "passphrase") else {
            throw ImportError.keychain
        }
        lock.lock()
        cachedCredential = credential
        cachedSummary = summary
        loaded = true
        lock.unlock()
        return summary
    }

    func remove() {
        delete(account: "p12")
        delete(account: "passphrase")
        lock.lock()
        cachedCredential = nil
        cachedSummary = nil
        loaded = true
        lock.unlock()
    }

    // MARK: - Private

    private func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        guard let data = read(account: "p12"),
              let passphrase = read(account: "passphrase").flatMap({ String(data: $0, encoding: .utf8) }),
              let opened = try? Self.open(data, passphrase: passphrase) else { return }
        cachedCredential = opened.0
        cachedSummary = opened.1
    }

    private static func open(_ data: Data, passphrase: String) throws -> (URLCredential, Summary) {
        var items: CFArray?
        let status = SecPKCS12Import(data as CFData, [kSecImportExportPassphrase as String: passphrase] as CFDictionary, &items)
        switch status {
        case errSecSuccess: break
        case errSecAuthFailed, errSecPkcs12VerifyFailure: throw ImportError.wrongPassphrase
        default: throw ImportError.unreadable
        }
        guard let first = (items as? [[String: Any]])?.first,
              let identityRef = first[kSecImportItemIdentity as String] else { throw ImportError.noIdentity }
        // swiftlint:disable:next force_cast
        let identity = identityRef as! SecIdentity
        var certificate: SecCertificate?
        SecIdentityCopyCertificate(identity, &certificate)
        let name = certificate.flatMap { SecCertificateCopySubjectSummary($0) as String? } ?? "Device certificate"
        // The chain after the leaf (the CA), so Caddy can build the path.
        let chain = (first[kSecImportItemCertChain as String] as? [SecCertificate] ?? []).dropFirst()
        let credential = URLCredential(identity: identity, certificates: Array(chain), persistence: .forSession)
        return (credential, Summary(name: name))
    }

    private func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private func read(account: String) -> Data? {
        var q = query(account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    private func save(_ data: Data, account: String) -> Bool {
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let status = SecItemUpdate(query(account) as CFDictionary, attributes as CFDictionary)
        if status == errSecSuccess { return true }
        guard status == errSecItemNotFound else { return false }
        return SecItemAdd(query(account).merging(attributes) { _, new in new } as CFDictionary, nil) == errSecSuccess
    }

    private func delete(account: String) {
        SecItemDelete(query(account) as CFDictionary)
    }
}

/// The one session delegate every JARVIS `URLSession` shares: answers Caddy's
/// client-certificate challenge with the device certificate, and leaves
/// everything else (server trust included) to the system.
final class ClientCertificateDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    static let shared = ClientCertificateDelegate()

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodClientCertificate else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        if let credential = DeviceCertificate.shared.credential {
            completionHandler(.useCredential, credential)
        } else {
            // No certificate: let the handshake fail (Caddy refuses), which reads as "away access not set up".
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
