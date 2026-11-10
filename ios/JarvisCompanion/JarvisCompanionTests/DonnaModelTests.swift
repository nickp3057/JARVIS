import XCTest
@testable import JarvisCompanion

final class DonnaModelTests: XCTestCase {
    func testDecodesStateAndReply() throws {
        let state = try JSONDecoder().decode(DonnaState.self, from: Data("""
        {"session": {"id": 4, "started_at": 1762700000.0, "messages": [
            {"role": "user", "text": "rough week", "at": 1762700001.0},
            {"role": "assistant", "text": "Tell me.", "at": 1762700002.0}]},
         "checkins": [{"id": 2, "due_at": 1762800000.0, "message": "Donna wants to check in."}],
         "has_case_file": true}
        """.utf8))
        XCTAssertEqual(state.session?.messages.map(\.fromNick), [true, false])
        XCTAssertEqual(state.checkins.first?.id, 2)
        XCTAssertTrue(state.hasCaseFile)
        let empty = try JSONDecoder().decode(DonnaState.self, from: Data(
            #"{"session": null, "checkins": [], "has_case_file": false}"#.utf8))
        XCTAssertNil(empty.session)
        let reply = try JSONDecoder().decode(DonnaReply.self, from: Data(
            #"{"reply": "Go on.", "session_id": 4, "checkins": []}"#.utf8))
        XCTAssertEqual(reply.sessionID, 4)
    }

    func testDecodesMemory() throws {
        let memory = try JSONDecoder().decode(DonnaMemory.self, from: Data("""
        {"case_file": "Nick: student.", "notes": [{"id": 1, "started_at": 1762700000.0, "note": "Talked school."}],
         "followups": [{"id": 3, "text": "Ask about Jake", "created_at": 1762700000.0}],
         "moods": [{"at": 1762700000.0, "score": 6, "note": ""}]}
        """.utf8))
        XCTAssertEqual(memory.notes.first?.note, "Talked school.")
        XCTAssertEqual(memory.moods.first?.score, 6)
    }
}

final class AgentUsageTests: XCTestCase {
    func testDecodesAndFormats() throws {
        let usage = try JSONDecoder().decode(AgentUsage.self, from: Data("""
        {"days": 7, "since": 1.0, "tracking_since": 1762700000.0, "total_usd": 1.2345,
         "agents": [{"id": "donna", "name": "Donna", "symbol": "heart.text.square", "usd": 1.2, "calls": 40,
                     "input_tokens": 90000, "output_tokens": 4000, "models": ["claude-sonnet-5-5"]}],
         "daily": [{"day": "2026-11-10", "usd": 1.2345}]}
        """.utf8))
        XCTAssertEqual(usage.agents.first?.calls, 40)
        XCTAssertEqual(AgentUsageView.money(0), "$0")
        XCTAssertEqual(AgentUsageView.money(0.004), "<1¢")
        XCTAssertEqual(AgentUsageView.shortModel("claude-sonnet-5-5"), "Sonnet 5.5")
        XCTAssertEqual(AgentUsageView.shortModel("claude-haiku-4-5-20251001"), "Haiku 4.5")
    }
}
