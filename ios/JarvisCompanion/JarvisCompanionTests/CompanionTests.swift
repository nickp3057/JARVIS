import XCTest
import SwiftUI
import UIKit
@testable import JarvisCompanion

private final class MemoryStore: ChatStore {
    var archive: ChatArchive
    init(_ archive: ChatArchive = .empty()) { self.archive = archive }
    func load() -> ChatArchive { archive }
    func save(_ archive: ChatArchive) { self.archive = archive }
}

private final class MockProtocol: URLProtocol {
    static var handler: ((URLRequest) -> (Int, String, TimeInterval))!
    private var delivery: DispatchWorkItem?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let (status, body, delay) = Self.handler(request)
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            let response = HTTPURLResponse(url: request.url!, statusCode: status,
                httpVersion: "HTTP/1.1", headerFields: ["X-Jarvis-Session-ID": "session"] )!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(body.utf8))
            client?.urlProtocolDidFinishLoading(self)
        }
        delivery = work
        DispatchQueue.global().asyncAfter(deadline: .now() + delay, execute: work)
    }
    override func stopLoading() { delivery?.cancel() }
}

@MainActor
final class CompanionTests: XCTestCase {
    private var session: URLSession!
    private var settings: AppSettings!
    private var defaults: UserDefaults!
    private var suite: String!

    override func setUp() async throws {
        suite = "JarvisCompanionTests.\(UUID())"
        defaults = UserDefaults(suiteName: suite)!
        settings = AppSettings(defaults: defaults, keychain: KeychainTokenStore(service: suite))
        settings.apiToken = "test"
        settings.baseURLString = "http://localhost:8000"
        // Both transport and Keychain namespace are isolated from the real app.
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockProtocol.self]
        session = URLSession(configuration: config)
    }

    override func tearDown() async throws {
        KeychainTokenStore(service: suite).save("")
        session.invalidateAndCancel()
        defaults.removePersistentDomain(forName: suite)
    }

    private func model(store: MemoryStore = MemoryStore()) -> ChatViewModel {
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        return ChatViewModel(settings: settings, store: store, clientFactory: { client })
    }

    private func waitUntil(_ condition: () -> Bool, timeout: TimeInterval = 4) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition(), Date() < deadline { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertTrue(condition())
    }

    func testMultilinePasteRemainsAnUnsentDraft() async throws {
        let store = MemoryStore()
        let chat = model(store: store)
        chat.draft = "first line\nsecond line"
        chat.draftChanged()
        try await Task.sleep(for: .milliseconds(450))
        XCTAssertEqual(chat.draft, "first line\nsecond line")
        XCTAssertTrue(chat.messages.isEmpty)
        XCTAssertFalse(chat.isAwaitingReply)
        XCTAssertEqual(store.archive.conversations.first?.draft, chat.draft)
    }

    func testStopThenNewTurnCannotBeClearedByOldTask() async throws {
        MockProtocol.handler = { request in
            switch request.url!.path {
            case "/sessions": return (201, "{\"session_id\":\"session\"}", 0)
            case "/chat/stream": return (200, "A completed reply", 0.5)
            default: return (200, "{\"messages\":[]}", 0)
            }
        }
        let chat = model()
        chat.draft = "first"
        chat.send()
        try await Task.sleep(for: .milliseconds(100))
        chat.startNewConversation()
        chat.draft = "second"
        chat.send()
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertTrue(chat.isAwaitingReply)
        XCTAssertEqual(chat.messages.first?.text, "second")
        try await waitUntil({ !chat.isAwaitingReply })
        XCTAssertEqual(chat.messages.last?.text, "A completed reply")
        XCTAssertFalse(chat.messages.contains { $0.text == "first" })
    }

    func testHistoryRefreshPreservesInFlightUserAndReplyPlaceholder() async throws {
        MockProtocol.handler = { request in
            switch request.url!.path {
            case "/health": return (200, "{}", 0)
            case "/sessions": return (200, "{\"sessions\":[{\"session_id\":\"session\"}]}", 0)
            case "/chat/stream": return (200, "New reply", 0.5)
            default: return (200, """
                {"messages":[{"id":1,"role":"user","content":"old","request_id":"old"},
                {"id":2,"role":"assistant","content":"Old answer","request_id":"old"}]}
                """, 0)
            }
        }
        let conversation = ChatConversation(messages: [ChatMessage(role: .user, text: "old"),
            ChatMessage(role: .jarvis, text: "Old answer")], serverSessionID: "session")
        let chat = model(store: MemoryStore(ChatArchive(conversations: [conversation], activeConversationID: conversation.id)))
        chat.draft = "new"
        chat.send()
        let placeholder = chat.messages.last?.id
        chat.refreshConnectionStatus()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertTrue(chat.messages.contains { $0.text == "new" })
        XCTAssertEqual(chat.messages.last?.id, placeholder)
        XCTAssertTrue(chat.isAwaitingReply)
        try await waitUntil({ !chat.isAwaitingReply })
        XCTAssertEqual(chat.messages.last?.text, "New reply")
    }

    func testRepeatedTextDoesNotRecoverAnOlderAnswer() async throws {
        MockProtocol.handler = { request in
            switch request.url!.path {
            case "/sessions": return (201, "{\"session_id\":\"session\"}", 0)
            case "/chat/stream": return (200, "New answer", 0)
            default:
                return (200, """
                    {"messages":[{"id":1,"role":"user","content":"yes","request_id":"old"},
                    {"id":2,"role":"assistant","content":"Old answer","request_id":"old"}]}
                    """, 0)
            }
        }
        let chat = model()
        chat.draft = "yes"
        chat.send()
        try await waitUntil({ !chat.isAwaitingReply })
        XCTAssertEqual(chat.messages.last?.text, "New answer")
    }

    func testFailedDeletePersistsForRetryAcrossLaunch() async throws {
        MockProtocol.handler = { _ in (503, "offline", 0) }
        let conversation = ChatConversation(messages: [ChatMessage(role: .user, text: "hello")], serverSessionID: "session")
        let store = MemoryStore(ChatArchive(conversations: [conversation], activeConversationID: conversation.id))
        let chat = model(store: store)
        chat.deleteConversation(conversation.id)
        try await waitUntil({ chat.historyError != nil })
        XCTAssertEqual(store.archive.pendingEdits?.first?.sessionID, "session")
        XCTAssertNil(store.archive.pendingEdits?.first?.title)
        let reopened = model(store: store)
        XCTAssertTrue(reopened.visibleConversations.isEmpty)
        XCTAssertEqual(store.archive.pendingEdits?.count, 1)
    }

    func testInterruptedPlaceholderRestoresRetry() {
        let conversation = ChatConversation(messages: [
            ChatMessage(role: .user, text: "hello", requestID: "request"),
            ChatMessage(role: .jarvis, text: "", requestID: "request"),
        ], serverSessionID: "session")
        let chat = model(store: MemoryStore(ChatArchive(conversations: [conversation], activeConversationID: conversation.id)))
        XCTAssertTrue(chat.canRetry)
        XCTAssertEqual(chat.messages.last?.role, .error)
    }

    func testVisualSnapshots() async throws {
        MockProtocol.handler = { request in
            switch request.url!.path {
            case "/api/status/services": return (200, """
                {"services":[{"name":"Home Assistant","up":true,"latency_ms":12,"error":""},
                {"name":"Plex","up":true,"latency_ms":8,"error":""},
                {"name":"qBittorrent","up":true,"latency_ms":16,"error":""}]}
                """, 0)
            case "/api/status/torrents": return (200, """
                {"error":"","dl_speed":8388608,"up_speed":524288,"torrents":[
                {"name":"Ubuntu 26.04 LTS","hash":"abc","state":"downloading","progress":0.68,"dl_speed":8388608,"up_speed":0,"eta":180,"size":4000000000},
                {"name":"Debian installation image","hash":"def","state":"stoppedDL","progress":0.32,"dl_speed":0,"up_speed":0,"eta":0,"size":2000000000}]}
                """, 0)
            case "/api/status/media": return (200, """
                {"radarr":{"error":"","queue":[],"recent":[]},"sonarr":{"error":"","queue":[],"recent":[]}}
                """, 0)
            case "/api/home/entities": return (200, """
                {"error":"","entities":[{"entity_id":"light.office","friendly_name":"Office lights","domain":"light","state":"on"}]}
                """, 0)
            default: return (200, "{\"links\":[]}", 0)
            }
        }
        let empty = model()
        await snapshot(ChatView(model: empty), name: "Chat — ready")
        let conversation = ChatConversation(messages: [
            ChatMessage(role: .user, text: "Bring me up to speed."),
            ChatMessage(role: .jarvis, text: "The house is quiet, sir. Your downloads are making progress, and the office lights are still on.\n\nA remarkably orderly afternoon. I’ll try not to sound surprised."),
            ChatMessage(role: .user, text: "What’s downloading?"),
            ChatMessage(role: .jarvis, text: "Ubuntu is at 68%, with about three minutes left. The Debian image is paused."),
        ])
        let chat = model(store: MemoryStore(ChatArchive(conversations: [conversation], activeConversationID: conversation.id)))
        await snapshot(ChatView(model: chat), name: "Chat — conversation")
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client })
        await hub.refreshAll()
        await snapshot(HubView(model: hub), name: "Control room")
        hub.stopPolling()
    }

    private func snapshot<V: View>(_ view: V, name: String) async {
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first else {
            XCTFail("No window scene available for visual validation")
            return
        }
        let window = UIWindow(windowScene: scene)
        window.frame = CGRect(x: 0, y: 0, width: 393, height: 852)
        let host = UIHostingController(rootView: view.environmentObject(settings)
            .tint(JarvisTheme.cyan).preferredColorScheme(.dark))
        window.rootViewController = host
        window.makeKeyAndVisible()
        host.view.frame = window.bounds
        host.view.layoutIfNeeded()
        try? await Task.sleep(for: .milliseconds(300))
        let image = UIGraphicsImageRenderer(bounds: window.bounds).image { _ in
            XCTAssertTrue(window.drawHierarchy(in: window.bounds, afterScreenUpdates: true))
        }
        let attachment = XCTAttachment(image: image)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        window.isHidden = true
        window.rootViewController = nil
    }

    func testPausedAndStoppedStatesOfferResume() throws {
        for state in ["pausedDL", "pausedUP", "stoppedDL", "stoppedUP"] {
            let json = """
                {"name":"Download","progress":0.5,"state":"\(state)","dl_speed":0,"up_speed":0,"eta":0,"size":100,"hash":"hash"}
                """
            XCTAssertTrue(try JSONDecoder().decode(TorrentTransfer.self, from: Data(json.utf8)).isPaused)
        }
    }
}
