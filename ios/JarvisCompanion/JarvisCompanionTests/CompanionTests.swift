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

private final class MemoryTokenStore: TokenStoring {
    var token: String?
    func load() -> String? { token }
    func save(_ token: String) -> Bool { self.token = token; return true }
}

@MainActor
private final class RecordingStudyScheduler: AcademicNotificationScheduling {
    var calls = 0
    var permissionRequests = 0
    var lastBriefs: [StudyBriefEntry] = []
    var lastEnabled = true
    func requestPermission() async -> Bool { permissionRequests += 1; return true }
    func sync(briefs: [StudyBriefEntry], enabled: Bool) async {
        calls += 1
        lastBriefs = briefs
        lastEnabled = enabled
    }
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
        settings = AppSettings(defaults: defaults, keychain: MemoryTokenStore())
        settings.apiToken = "test"
        settings.homeURLString = "http://localhost:8000"
        // Transport and token storage never touch the real app or require signing.
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockProtocol.self]
        session = URLSession(configuration: config)
    }

    override func tearDown() async throws {
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

    func testUnchangedHistorySkipsTranscriptButChangedHistoryLoads() async throws {
        let requests = NSLock()
        var transcriptRequests = 0
        var revision = 2
        MockProtocol.handler = { request in
            requests.lock()
            defer { requests.unlock() }
            switch request.url!.path {
            case "/health": return (200, "{}", 0)
            case "/sessions": return (200, """
                {"sessions":[{"session_id":"session","title":"Renamed","last_message_id":\(revision)}]}
                """, 0)
            default:
                transcriptRequests += 1
                return (200, """
                    {"messages":[{"id":1,"role":"user","content":"old"},
                    {"id":\(revision),"role":"assistant","content":"Changed answer"}]}
                    """, 0)
            }
        }
        let conversation = ChatConversation(messages: [ChatMessage(role: .jarvis, text: "Old answer")],
            serverSessionID: "session", serverLastMessageID: 2)
        let chat = model(store: MemoryStore(ChatArchive(conversations: [conversation], activeConversationID: conversation.id)))
        chat.refreshConnectionStatus()
        try await waitUntil({ chat.activeTitle == "Renamed" })
        XCTAssertEqual(requests.withLock { transcriptRequests }, 0)
        requests.withLock { revision = 4 }
        chat.refreshConnectionStatus()
        try await waitUntil({ chat.messages.last?.text == "Changed answer" })
        XCTAssertEqual(requests.withLock { transcriptRequests }, 1)
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

    func testLiveVoiceUsesChatSessionAndPreservesTypedDraft() async throws {
        MockProtocol.handler = { request in
            switch (request.httpMethod ?? "GET", request.url!.path) {
            case ("POST", "/sessions"): return (201, "{\"session_id\":\"voice-session\"}", 0)
            case (_, "/health"): return (200, "{}", 0)
            case (_, "/sessions"): return (200, "{\"sessions\":[]}", 0)
            default: return (200, "{}", 0)
            }
        }
        let store = MemoryStore()
        let chat = model(store: store)
        chat.draft = "An unfinished typed message"
        let (conversation, session) = try await chat.prepareVoiceConversation()
        XCTAssertEqual(session, "voice-session")
        let response = try XCTUnwrap(chat.beginVoiceReply("Lamp on", conversationID: conversation, requestID: "voice-one"))
        XCTAssertTrue(chat.isAwaitingReply)
        XCTAssertTrue(chat.receiveVoiceChunk("\u{1D}Checking", responseID: response, conversationID: conversation))
        XCTAssertTrue(chat.receiveVoiceChunk(" lights\u{1D}Done.", responseID: response, conversationID: conversation))
        XCTAssertEqual(chat.messages.last?.text, "Done.")
        chat.finishVoiceReply(responseID: response, conversationID: conversation)
        XCTAssertFalse(chat.isAwaitingReply)
        XCTAssertEqual(chat.draft, "An unfinished typed message")
        XCTAssertEqual(store.archive.conversations.first(where: { $0.id == conversation })?.messages.last?.requestID, "voice-one")
        try await waitUntil({ chat.connectionState == .online })
    }

    func testLiveVoiceFailurePreservesReceiptForRetryWithoutPartialBubble() async throws {
        MockProtocol.handler = { request in
            switch request.url!.path {
            case "/health": return (200, "{}", 0)
            case "/sessions":
                return request.httpMethod == "POST" ? (201, "{\"session_id\":\"voice-session\"}", 0) : (200, "{\"sessions\":[]}", 0)
            default: return (200, "{}", 0)
            }
        }
        let chat = model()
        let (conversation, _) = try await chat.prepareVoiceConversation()
        let response = try XCTUnwrap(chat.beginVoiceReply("Hello", conversationID: conversation, requestID: "voice-retry"))
        XCTAssertTrue(chat.receiveVoiceChunk("Partial reply", responseID: response, conversationID: conversation))
        chat.finishVoiceReply(responseID: response, conversationID: conversation, error: "Disconnected")
        XCTAssertTrue(chat.canRetry)
        XCTAssertEqual(chat.messages.filter { $0.role == .jarvis }.count, 0)
        XCTAssertEqual(chat.messages.first?.requestID, "voice-retry")
        try await waitUntil({ chat.connectionState == .online })
    }

    func testLiveVoiceCallbacksCannotCrossConversations() async throws {
        MockProtocol.handler = { _ in (201, "{\"session_id\":\"voice-session\"}", 0) }
        let chat = model()
        let (conversation, _) = try await chat.prepareVoiceConversation()
        let response = try XCTUnwrap(chat.beginVoiceReply("Hello", conversationID: conversation, requestID: "voice-one"))
        chat.startNewConversation()
        XCTAssertFalse(chat.receiveVoiceChunk("Old reply", responseID: response, conversationID: conversation))
        chat.finishVoiceReply(responseID: response, conversationID: conversation)
        XCTAssertTrue(chat.messages.isEmpty)
    }

    func testVisualSnapshots() async throws {
        MockProtocol.handler = { request in
            switch request.url!.path {
            case "/health": return (200, "{}", 0)
            case "/sessions": return (200, "{\"sessions\":[]}", 0)
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
            case "/api/maintenance/1/history": return (200, """
                {"task":null,"history":[{"id":2,"task_id":1,"completed_at":"2026-07-20T12:00:00","note":"Wired the lower branch"},
                {"id":1,"task_id":1,"completed_at":"2026-04-18T12:00:00","note":""}]}
                """, 0)
            case "/api/maintenance": return (200, """
                {"tasks":[{"id":1,"name":"Trim bonsai","schedule_type":"interval","interval_value":3,"interval_unit":"months","fixed_month":null,"fixed_day":null,"notes":"","created_at":"2026-10-01T09:00:00","last_done":"2026-07-20T12:00:00","last_note":"","log_count":2,"days_since":99,"next_due":"2026-10-20","days_remaining":-7,"status":"overdue","schedule_label":"Every quarter"},{"id":2,"name":"Rotate mattress","schedule_type":"interval","interval_value":6,"interval_unit":"months","fixed_month":null,"fixed_day":null,"notes":"","created_at":"2026-10-01T09:00:00","last_done":"2026-06-01T12:00:00","last_note":"","log_count":1,"days_since":148,"next_due":"2026-12-01","days_remaining":35,"status":"ok","schedule_label":"Every 6 months"}]}
                """, 0)
            case "/api/now": return (200, """
                {"generated_at":\(Date().timeIntervalSince1970),"timers":[
                {"id":"tea","label":"Tea","state":"running","remaining_seconds":245,"expires_at":\(Date().timeIntervalSince1970 + 245)}],
                "reminders":[{"id":"r1","text":"Call Mum","due_at":null,"is_due":false}],
                "next_event":{"id":"e1","title":"Design review","start_at":1789362000,"all_day":false,"location":"Studio"},
                "calendar_error":"","academic_events":[
                {"id":"quiz-1","title":"Biology quiz","start_at":1789531200,"all_day":true,"location":"","days_left":2}],
                "maintenance":[{"id":1,"name":"Trim bonsai","schedule_type":"interval","interval_value":3,"interval_unit":"months","fixed_month":null,"fixed_day":null,"notes":"","created_at":"2026-10-01T09:00:00","last_done":"2026-07-20T12:00:00","last_note":"","log_count":2,"days_since":99,"next_due":"2026-10-20","days_remaining":-7,"status":"overdue","schedule_label":"Every quarter"}]}
                """, 0)
            case "/api/status/media": return (200, """
                {"radarr":{"error":"","queue":[],"recent":[]},"sonarr":{"error":"","queue":[],"recent":[]}}
                """, 0)
            case "/api/status/storage": return (200, """
                {"error":"","label":"Video vault","total_bytes":12000000000000,
                "used_bytes":7500000000000,"free_bytes":4500000000000,"used_fraction":0.625}
                """, 0)
            case "/api/alerts/recent": return (200, """
                {"alerts":[{"id":2,"kind":"download_stalled","severity":"warning","title":"Download stalled",
                "message":"Debian installation image: stalled at 32.0% with 0 seed(s); no data for 2.0 h.",
                "source":"downloads","created_at":\(Date().timeIntervalSince1970 - 600),"push":true}]}
                """, 0)
            case "/api/home/entities": return (200, """
                {"error":"","entities":[{"entity_id":"light.office","friendly_name":"Office lights","domain":"light","state":"on"}]}
                """, 0)
            default: return (200, "{\"links\":[]}", 0)
            }
        }
        let empty = model()
        empty.refreshConnectionStatus()
        try await waitUntil({ empty.connectionState == .online })
        await snapshot(ChatView(model: empty), name: "Chat — ready")
        let conversation = ChatConversation(messages: [
            ChatMessage(role: .user, text: "Bring me up to speed."),
            ChatMessage(role: .jarvis, text: "The house is quiet, sir. Your downloads are making progress, and the office lights are still on.\n\nA remarkably orderly afternoon. I’ll try not to sound surprised."),
            ChatMessage(role: .user, text: "What’s downloading?"),
            ChatMessage(role: .jarvis, text: "Ubuntu is at 68%, with about three minutes left. The Debian image is paused."),
        ])
        let chat = model(store: MemoryStore(ChatArchive(conversations: [conversation], activeConversationID: conversation.id)))
        chat.refreshConnectionStatus()
        try await waitUntil({ chat.connectionState == .online })
        await snapshot(ChatView(model: chat), name: "Chat — conversation")
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client })
        await hub.refreshAll()
        await snapshot(HubView(model: hub), name: "Hub — home")
        await snapshot(HubView(model: hub, focus: .hub), name: "Hub — hub")
        await snapshot(HubView(model: hub, focus: .media), name: "Hub — media")
        await snapshot(NavigationStack { MaintenanceListView(model: hub) }, name: "Maintenance — all")
        await snapshot(NavigationStack { MaintenanceDetailView(model: hub, taskID: 1) }, name: "Maintenance — detail")
        await snapshot(SettingsView(embedded: true), name: "Settings")
        await snapshot(SettingsView(embedded: true).dynamicTypeSize(.accessibility2), name: "Settings — large text")
        await snapshot(HubView(model: hub).dynamicTypeSize(.accessibility2), name: "Hub — large text")
        await snapshot(RootView(chat: chat, hub: hub), name: "App — navigation")
        for palette in JarvisPalette.allCases where palette != .arc {
            UserDefaults.standard.set(palette.rawValue, forKey: JarvisPalette.storageKey)
            await snapshot(ChatView(model: chat), name: "Theme \(palette.name) — chat")
            await snapshot(HubView(model: hub), name: "Theme \(palette.name) — hub")
            await snapshot(ChatView(model: empty), name: "Theme \(palette.name) — ready")
        }
        UserDefaults.standard.removeObject(forKey: JarvisPalette.storageKey)
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

    func testNowSnapshotSurvivesRelaunchAsStaleData() async throws {
        MockProtocol.handler = { request in
            if request.url!.path == "/api/now" {
                return (200, """
                    {"generated_at":1789358400,"timers":[],"reminders":[
                    {"id":"r1","text":"Call Mum","due_at":null,"is_due":false}],
                    "next_event":null,"calendar_error":""}
                    """, 0)
            }
            return (200, "{}", 0)
        }
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let first = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults)
        await first.refreshNow()
        XCTAssertFalse(first.nowSnapshotIsStale)
        XCTAssertEqual(first.now.value?.reminders.first?.text, "Call Mum")

        let relaunched = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults)
        XCTAssertTrue(relaunched.nowSnapshotIsStale)
        XCTAssertEqual(relaunched.now.value?.reminders.first?.text, "Call Mum")
    }

    func testAcademicCountdownCopy() {
        XCTAssertEqual(AcademicNotificationScheduler.body(title: "Biology quiz", daysLeft: 0),
                       "Biology quiz is today.")
        XCTAssertEqual(AcademicNotificationScheduler.body(title: "Biology quiz", daysLeft: 1),
                       "Biology quiz is tomorrow.")
        XCTAssertEqual(AcademicNotificationScheduler.body(title: "Biology quiz", daysLeft: 4),
                       "Biology quiz is in 4 days.")
    }

    func testMaintenanceCountdownAndDraftEncoding() throws {
        func task(_ remaining: String, since: String = "null", last: String = "\"2026-07-20T12:00:00\"") throws -> MaintenanceTask {
            let json = """
            {"id":1,"name":"Trim bonsai","schedule_type":"interval","interval_value":3,"interval_unit":"months",
            "fixed_month":null,"fixed_day":null,"notes":"","last_done":\(last),"last_note":"","log_count":1,
            "days_since":\(since),"next_due":null,"days_remaining":\(remaining),"status":"ok","schedule_label":"Every quarter"}
            """
            return try JSONDecoder().decode(MaintenanceTask.self, from: Data(json.utf8))
        }
        XCTAssertEqual(try task("-7").countdown, "7 DAYS OVERDUE")
        XCTAssertEqual(try task("-1").countdown, "1 DAY OVERDUE")
        XCTAssertEqual(try task("0").countdown, "DUE TODAY")
        XCTAssertEqual(try task("0", last: "null").countdown, "DUE NOW")
        XCTAssertEqual(try task("12").countdown, "IN 12 DAYS")
        XCTAssertEqual(try task("null", since: "4").countdown, "4 DAYS AGO")
        XCTAssertEqual(try task("null", since: "4").lastDoneText, "Done 4 days ago")
        XCTAssertNotNil(try task("0").lastDoneDate)

        var draft = MaintenanceTaskDraft()
        draft.name = "Flush water heater"
        draft.scheduleType = "fixed_calendar"
        draft.fixedMonth = 4
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(draft)) as? [String: Any]
        XCTAssertEqual(body?["fixed_month"] as? Int, 4)
        XCTAssertEqual(body?["interval_value"] as? Int, 3, "Calendar tasks send the months between dates")
        XCTAssertNil(body?["interval_unit"], "Only the chosen schedule's fields are sent")
        draft.fixedMonth = 1
        draft.fixedDay = 1
        XCTAssertEqual(draft.calendarMonths, [1, 4, 7, 10])
        draft.calendarPeriod = 6
        draft.fixedMonth = 7
        XCTAssertEqual(draft.calendarMonths, [1, 7])
        draft.calendarPeriod = 1
        draft.seasonStart = 3
        draft.seasonEnd = 10
        XCTAssertEqual(draft.calendarMonths, Array(3...10))
        let seasonal = try JSONSerialization.jsonObject(with: JSONEncoder().encode(draft)) as? [String: Any]
        XCTAssertEqual(seasonal?["season_start"] as? Int, 3)
        XCTAssertEqual(seasonal?["season_end"] as? Int, 10)
    }

    func testStudyBriefGroupsEventsAndExcludesFinishedTimedAssessments() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let now = calendar.date(from: DateComponents(year: 2026, month: 9, day: 14, hour: 8))!
        let events = [
            AcademicEvent(id: "1", title: "Biology quiz", startAt: now.addingTimeInterval(86400).timeIntervalSince1970,
                          allDay: true, location: "", daysLeft: 1),
            AcademicEvent(id: "2", title: "Math exam", startAt: now.addingTimeInterval(172800).timeIntervalSince1970,
                          allDay: true, location: "", daysLeft: 2),
            AcademicEvent(id: "3", title: "Finished exam", startAt: now.addingTimeInterval(-3600).timeIntervalSince1970,
                          allDay: false, location: "", daysLeft: 0),
        ]
        let briefs = AcademicNotificationScheduler.briefs(events: events, now: now, calendar: calendar, hour: 9)
        XCTAssertEqual(briefs.count, 3)
        XCTAssertEqual(briefs.first?.body, "Biology quiz is tomorrow.\nMath exam is in 2 days.")
        XCTAssertFalse(briefs.contains { $0.body.contains("Finished") })
        XCTAssertEqual(Set(briefs.map(\.id)).count, briefs.count)
    }

    func testStudyBriefSchedulesServerComposedBriefs() async {
        let scheduler = RecordingStudyScheduler()
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults,
                               academicNotifications: scheduler)
        MockProtocol.handler = { request in
            if request.url!.path.hasSuffix("/api/study/brief") {
                return (200, """
                    {"enabled":true,"hour":7,"window_days":21,"keywords":["quiz"],"style":"blunt",
                     "generated_at":1000,"composed_by":"model","error":"",
                     "briefs":[{"id":"2026-09-16","fire_at":1789596000,"body":"Quiz tomorrow, sir.",
                                "items":[{"title":"Biology quiz","days_left":1}]},
                               {"id":"2026-09-17","fire_at":1789682400,"body":"Quiz today.","items":[]}]}
                    """, 0)
            }
            return (200, """
                {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,"calendar_error":"",
                 "academic_events":[{"id":"1","title":"Biology quiz","start_at":1789682400,"all_day":true,
                                     "location":"","days_left":2}]}
                """, 0)
        }
        await hub.refreshNow()
        XCTAssertEqual(scheduler.calls, 1)
        XCTAssertTrue(scheduler.lastEnabled)
        XCTAssertEqual(scheduler.lastBriefs.map(\.body), ["Quiz tomorrow, sir.", "Quiz today."])
        XCTAssertEqual(scheduler.lastBriefs.first?.items, [StudyBriefItem(title: "Biology quiz", daysLeft: 1)])
        XCTAssertEqual(hub.studyBrief.value?.hour, 7)
        XCTAssertEqual(hub.studyBrief.value?.windowDays, 21)
        XCTAssertTrue(SettingsView.scheduleSummary(hub.studyBrief.value!).hasSuffix("· 21-day warning"))
    }

    func testStudyBriefIsFetchedWhenTheCountdownChangesNotEveryTick() async {
        let scheduler = RecordingStudyScheduler()
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults,
                               academicNotifications: scheduler)
        var briefFetches = 0
        var daysLeft = 2
        var briefFails = false
        MockProtocol.handler = { request in
            if request.url!.path.hasSuffix("/api/study/brief") {
                briefFetches += 1
                if briefFails { return (500, "{\"detail\":\"boom\"}", 0) }
                return (200, """
                    {"enabled":true,"hour":7,"window_days":21,"error":"","briefs":[]}
                    """, 0)
            }
            return (200, """
                {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,"calendar_error":"",
                 "academic_events":[{"id":"1","title":"Biology quiz","start_at":1789682400,"all_day":true,
                                     "location":"","days_left":\(daysLeft)}]}
                """, 0)
        }
        await hub.refreshNow()
        await hub.refreshNow()
        await hub.refreshNow()
        XCTAssertEqual(briefFetches, 1)  // the 5 s tick no longer re-downloads it
        daysLeft = 1  // midnight passed (or the calendar changed)
        await hub.refreshNow()
        XCTAssertEqual(briefFetches, 2)
        await hub.refreshAll()  // launch or a pull down
        XCTAssertEqual(briefFetches, 3)
        briefFails = true
        daysLeft = 0
        await hub.refreshNow()
        await hub.refreshNow()
        XCTAssertEqual(briefFetches, 5)  // a failed fetch is retried on the next tick
    }

    func testRoomCameraPollsSlowlyOnlyWhileCoreReportsTheLensOff() {
        XCTAssertEqual(RoomCameraController.pollSeconds(pushed: false, running: false), 20)
        XCTAssertEqual(RoomCameraController.pollSeconds(pushed: nil, running: false), 1.5)  // stream down, older Core
        XCTAssertEqual(RoomCameraController.pollSeconds(pushed: true, running: false), 1.5)
        XCTAssertEqual(RoomCameraController.pollSeconds(pushed: false, running: true), 1.5)  // must stop promptly
    }

    func testStudyBriefCalendarOutageInPayloadKeepsQueue() async {
        let scheduler = RecordingStudyScheduler()
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults,
                               academicNotifications: scheduler)
        MockProtocol.handler = { request in
            if request.url!.path.hasSuffix("/api/study/brief") {
                return (200, """
                    {"enabled":true,"hour":9,"window_days":14,"error":"Calendar could not be reached.","briefs":[]}
                    """, 0)
            }
            return (200, """
                {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,"calendar_error":"","academic_events":[]}
                """, 0)
        }
        await hub.refreshNow()
        XCTAssertEqual(scheduler.calls, 0)
        XCTAssertEqual(hub.studyBrief.serverError, "Calendar could not be reached.")
    }

    func testOlderCoreWithoutStudyBriefEndpointPlansLocally() async {
        let scheduler = RecordingStudyScheduler()
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults,
                               academicNotifications: scheduler)
        let tomorrow = Calendar.current.startOfDay(for: Date()).addingTimeInterval(86400 + 43200)
        MockProtocol.handler = { request in
            if request.url!.path.hasSuffix("/api/study/brief") { return (404, "{\"detail\":\"Not Found\"}", 0) }
            return (200, """
                {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,"calendar_error":"",
                 "academic_events":[{"id":"1","title":"Biology quiz","start_at":\(tomorrow.timeIntervalSince1970),
                                     "all_day":true,"location":"","days_left":1}]}
                """, 0)
        }
        await hub.refreshNow()
        XCTAssertEqual(scheduler.calls, 1)
        XCTAssertFalse(scheduler.lastBriefs.isEmpty)
        // "tomorrow" before the 9 AM fire hour, "today" after it: either proves the local plan ran.
        XCTAssertTrue(scheduler.lastBriefs.contains { $0.body.contains("Biology quiz is") })
        XCTAssertNil(hub.studyBrief.loadError)
    }

    func testTogglingStudyBriefRequestsPermissionOrClearsQueue() async {
        let scheduler = RecordingStudyScheduler()
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults,
                               academicNotifications: scheduler)
        settings.studyAlertsEnabled = false
        await hub.updateNotificationPreferences()
        XCTAssertEqual(scheduler.calls, 1)
        XCTAssertFalse(scheduler.lastEnabled)
        XCTAssertTrue(scheduler.lastBriefs.isEmpty)
        MockProtocol.handler = { _ in (200, """
            {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,"calendar_error":"Calendar unavailable","academic_events":[]}
            """, 0) }
        settings.studyAlertsEnabled = true
        await hub.updateNotificationPreferences()
        XCTAssertEqual(scheduler.permissionRequests, 1)
    }

    func testCalendarOutageAndOlderCoreDoNotClearScheduledBriefs() async {
        let scheduler = RecordingStudyScheduler()
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults,
                               academicNotifications: scheduler)
        MockProtocol.handler = { _ in (200, """
            {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,
             "calendar_error":"Calendar unavailable","academic_events":[]}
            """, 0) }
        await hub.refreshNow()
        XCTAssertEqual(scheduler.calls, 0)
        MockProtocol.handler = { _ in (200, """
            {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,"calendar_error":""}
            """, 0) }
        await hub.refreshNow()
        XCTAssertEqual(scheduler.calls, 0)
    }

    func testNowCacheIsIsolatedByConnection() async {
        MockProtocol.handler = { _ in (200, """
            {"generated_at":1000,"timers":[],"reminders":[],"next_event":null,"calendar_error":""}
            """, 0) }
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let first = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults)
        await first.refreshNow()
        XCTAssertNotNil(first.now.value)
        settings.homeURLString = "http://different-server:8000"
        let other = HubViewModel(settings: settings, clientFactory: { client }, snapshotDefaults: defaults)
        XCTAssertNil(other.now.value)
    }

    func testPushRegistrationPostsTokenInServerSchema() async throws {
        var captured: URLRequest?
        var body = Data()
        MockProtocol.handler = { request in
            captured = request
            if let stream = request.httpBodyStream {
                stream.open()
                var buffer = [UInt8](repeating: 0, count: 4096)
                while stream.hasBytesAvailable {
                    let count = stream.read(&buffer, maxLength: buffer.count)
                    if count <= 0 { break }
                    body.append(buffer, count: count)
                }
                stream.close()
            }
            return (200, #"{"registered": true, "primary": "app_push"}"#, 0)
        }
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        try await client.registerPushDevice(token: String(repeating: "a", count: 64), environment: "sandbox", name: "iPhone")
        XCTAssertEqual(captured?.url?.path, "/api/push/register")
        XCTAssertEqual(captured?.httpMethod, "POST")
        XCTAssertEqual(captured?.value(forHTTPHeaderField: "Authorization"), "Bearer test")
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
        XCTAssertEqual(json, ["device_token": String(repeating: "a", count: 64), "environment": "sandbox", "device_name": "iPhone"])
    }

    func testTappedAlertUsesServerReportOrFallsBackToAlertText() async throws {
        let alert = try JSONDecoder().decode(JarvisAlert.self, from: Data("""
            {"id":7,"kind":"arr_stack","severity":"critical","title":"Jarvis — arr stack",
            "message":"Radarr unreachable","source":"arr-stack","created_at":1793000000.5}
            """.utf8))
        var sentBody = ""
        var status = 200
        MockProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/alerts/7/report")
            XCTAssertEqual(request.httpMethod, "POST")
            if let stream = request.httpBodyStream {
                stream.open()
                var buffer = [UInt8](repeating: 0, count: 1024)
                let count = stream.read(&buffer, maxLength: buffer.count)
                sentBody = String(decoding: buffer.prefix(max(0, count)), as: UTF8.self)
                stream.close()
            }
            return status == 200
                ? (200, #"{"report_id":"abc123","prompt":"Incident report abc123: Jarvis — arr stack"}"#, 0)
                : (404, #"{"detail":"Alert not found"}"#, 0)
        }
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client })
        let prompt = await hub.incidentPrompt(for: alert)
        XCTAssertEqual(prompt, "Incident report abc123: Jarvis — arr stack")
        XCTAssertTrue(sentBody.contains("1793000000.5"))
        XCTAssertNil(hub.reportingAlertID)
        status = 404
        let fallback = await hub.incidentPrompt(for: alert)
        XCTAssertEqual(fallback, HubViewModel.fallbackPrompt(for: alert))
        XCTAssertTrue(fallback?.contains("Radarr unreachable") == true)
    }

    func testApprovingARepairPostsApplyAndRefreshes() async throws {
        var applied = false
        MockProtocol.handler = { request in
            switch (request.httpMethod ?? "", request.url?.path ?? "") {
            case ("POST", "/api/repairs/ab12cd34/apply"):
                applied = true
                return (200, #"{"message":"Repair ab12cd34 is running","status":"running"}"#, 0)
            case ("GET", "/api/repairs"):
                let status = applied ? "running" : "pending"
                return (200, """
                    {"repairs":[{"id":"ab12cd34","kind":"stack_fix","label":"Rescan the Plex movie and TV libraries",
                    "rationale":"New episode missing","status":"\(status)","result":"","created_at":1}]}
                    """, 0)
            default: return (404, "{}", 0)
            }
        }
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        let hub = HubViewModel(settings: settings, clientFactory: { client })
        await hub.refreshRepairs()
        let repair = try XCTUnwrap(hub.repairs.value?.first)
        XCTAssertTrue(repair.isPending)
        hub.resolveRepair(repair, approve: true)
        try await waitUntil({ hub.repairs.value?.first?.status == "running" })
        XCTAssertTrue(applied)
        XCTAssertNil(hub.actionError)
        XCTAssertTrue(hub.busyRepairs.isEmpty)
    }

    func testLinksUseHomeAddressOnlyWhenItAnswers() async throws {
        MockProtocol.handler = { _ in (200, """
            {"links":[{"name":"Radarr","url":"http://10.0.0.147:7878","remote_url":"http://100.97.179.53:7878","icon_hint":"movie"},
            {"name":"Plex","url":"http://plex.local:32400","icon_hint":"play"}]}
            """, 0) }
        let client = JarvisAPIClient(baseURL: URL(string: "http://localhost:8000")!, token: "test", session: session)
        var home = false
        let hub = HubViewModel(settings: settings, clientFactory: { client }, homeProbe: { url in
            XCTAssertEqual(url.absoluteString, "http://10.0.0.147:7878")
            return home
        })
        await hub.refreshLinks()
        let radarr = try XCTUnwrap(hub.links.value?.first)
        XCTAssertEqual(hub.onHomeNetwork, false)
        XCTAssertEqual(radarr.destination(onHomeNetwork: hub.onHomeNetwork)?.absoluteString, "http://100.97.179.53:7878")
        // No mapping (or an older Core): always the one URL it has.
        XCTAssertEqual(hub.links.value?.last?.destination(onHomeNetwork: false)?.absoluteString, "http://plex.local:32400")
        home = true
        await hub.refreshLinks()
        XCTAssertEqual(radarr.destination(onHomeNetwork: hub.onHomeNetwork)?.absoluteString, "http://10.0.0.147:7878")
    }

    func testSimulatorPushEnvironmentIsSandbox() {
        #if targetEnvironment(simulator)
        XCTAssertEqual(PushRegistrar.apsEnvironment(), "sandbox")
        #endif
    }
}

final class GlanceDecodingTests: XCTestCase {
    func testWeatherAndAgendaDecodeAndToleratesGaps() throws {
        let json = """
        {"weather":{"online":true,"location":"Herriman, Utah, US","temperature_f":60,"description":"light rain",
          "high_f":60,"low_f":43,"humidity_percent":43,"wind_mph":11,"error":""},
         "events":[{"title":"Chem quiz","start_at":1793224800.0,"all_day":false,"calendar":"School"},
                   {"start_at":1793167200.0,"all_day":true}],
         "now":{},"alerts":[]}
        """
        let glance = try JSONDecoder().decode(Glance.self, from: Data(json.utf8))
        XCTAssertEqual(glance.weather?.temperatureF, 60)
        XCTAssertEqual(glance.weather?.symbol(night: false), "cloud.rain.fill")
        XCTAssertEqual(glance.events.count, 2)
        XCTAssertEqual(glance.events[1].title, "Untitled")
        XCTAssertTrue(glance.events[1].allDay)

        let broken = try JSONDecoder().decode(Glance.self, from: Data(#"{"weather":{"error":"down"},"events":"nope"}"#.utf8))
        XCTAssertEqual(broken.weather?.error, "down")
        XCTAssertTrue(broken.events.isEmpty)
    }

    func testClearSkyIsMoonAtNight() throws {
        let weather = try JSONDecoder().decode(GlanceWeather.self, from: Data(#"{"description":"clear"}"#.utf8))
        XCTAssertEqual(weather.symbol(night: true), "moon.stars.fill")
        XCTAssertEqual(weather.symbol(night: false), "sun.max.fill")
    }
}
