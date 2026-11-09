import SwiftUI
import WebKit

/// Schwab's login, inside the app. Schwab finishes by redirecting to the
/// app's registered callback (https://127.0.0.1:8182, where nothing
/// listens); the web view catches that address before it loads and hands it
/// to Core, which trades the code for tokens. Password AutoFill works here.
struct SchwabLoginView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    var onLinked: () -> Void = {}
    @State private var start: (url: URL, callback: String)?
    @State private var status: String?
    @State private var failed = false

    var body: some View {
        NavigationStack {
            ZStack {
                if let start, status == nil {
                    SchwabWebView(url: start.url, callback: start.callback, onRedirect: finish)
                }
                if let status {
                    VStack(spacing: 14) {
                        if !failed { ProgressView() }
                        Text(status).multilineTextAlignment(.center)
                            .foregroundStyle(failed ? JarvisTheme.amber : .primary)
                        if failed { Button("Try again") { Task { await begin() } } }
                    }
                    .padding(24)
                }
            }
            .navigationTitle("Charles Schwab").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
        .task { await begin() }
    }

    private func begin() async {
        failed = false
        status = "Opening Schwab…"
        do {
            let login = try await JarvisAPIClient.make(from: settings).schwabLogin()
            if login.external {
                // One Schwab login for JARVIS and Coopa Trada: it lives on the Schwab hub.
                openURL(login.url)
                dismiss()
                return
            }
            start = (login.url, login.callback)
            status = nil
        } catch {
            fail("Couldn't reach JARVIS: \(error.localizedDescription)")
        }
    }

    private func finish(_ redirect: URL) {
        status = "Linking…"
        Task {
            do {
                try await JarvisAPIClient.make(from: settings).finishSchwabLogin(redirect: redirect)
                onLinked()
                dismiss()
            } catch {
                fail("Schwab login didn't finish: \(error.localizedDescription)")
            }
        }
    }

    private func fail(_ text: String) {
        failed = true
        status = text
    }
}

private struct SchwabWebView: UIViewRepresentable {
    let url: URL
    let callback: String
    let onRedirect: (URL) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(callback: callback, onRedirect: onRedirect) }

    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        view.navigationDelegate = context.coordinator
        view.load(URLRequest(url: url))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        let callback: String
        let onRedirect: (URL) -> Void
        private var done = false

        init(callback: String, onRedirect: @escaping (URL) -> Void) {
            self.callback = callback
            self.onRedirect = onRedirect
        }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url, !callback.isEmpty,
                  url.absoluteString.hasPrefix(callback) else {
                decisionHandler(.allow)
                return
            }
            decisionHandler(.cancel)
            guard !done else { return }
            done = true
            onRedirect(url)
        }
    }
}
