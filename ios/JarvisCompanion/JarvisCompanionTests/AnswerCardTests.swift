import XCTest
@testable import JarvisCompanion

/// Answer cards as Core's card stream sends them (`brain/answer_cards.py`).
final class AnswerCardTests: XCTestCase {
    private func decode(_ json: String) throws -> AnswerCard {
        try JSONDecoder().decode(AnswerCard.self, from: Data(json.utf8))
    }

    func testWeatherToday() throws {
        let card = try decode("""
        {"id": 3, "card": "weather_today", "title": "Today · Herriman", "source": "conversation",
         "created_at": 1800000000.5, "ttl_s": 60.0,
         "data": {"online": true, "location": "Herriman, Utah, US", "temp_f": 54, "description": "partly cloudy",
                  "condition": "partly", "is_day": true, "high_f": 61, "low_f": 39, "humidity_percent": 31,
                  "wind_mph": 7, "gust_mph": null, "rain_chance": 60, "sunrise": 1799990000, "sunset": 1800030000,
                  "hours": [{"at": 1800000000, "temp_f": 54, "rain_chance": 5, "condition": "partly", "is_day": true},
                            {"at": 1800003600, "temp_f": 55, "rain_chance": 60, "condition": "rain", "is_day": false}]}}
        """)
        XCTAssertEqual(card.title, "Today · Herriman")
        guard case .weather(let weather) = card.payload else { return XCTFail("not weather") }
        XCTAssertEqual(weather.tempF, 54)
        XCTAssertNil(weather.gustMph)
        XCTAssertEqual(weather.hours.map(\.rainChance), [5, 60])
        XCTAssertEqual(AnswerCard.weatherSymbol(weather.hours[1].condition, day: false), "cloud.rain.fill")
    }

    func testCalendarAndTimers() throws {
        let day = try decode("""
        {"id": 4, "card": "calendar_day", "title": "Friday", "created_at": 1, "ttl_s": 60,
         "data": {"date": 0, "events": [{"title": "Quiz 4", "start_at": 1800000000, "all_day": false,
                                         "location": "", "calendar": "School"}]}}
        """)
        guard case .calendar(let calendar) = day.payload else { return XCTFail("not calendar") }
        XCTAssertEqual(calendar.events.first?.title, "Quiz 4")

        let timers = try decode("""
        {"id": 5, "card": "timers", "title": "Timers", "created_at": 1, "ttl_s": 60,
         "data": {"timers": [{"id": "abc123", "label": "Pasta", "remaining_s": 90.5, "paused": false, "alarming": false}]}}
        """)
        guard case .timers(let list) = timers.payload else { return XCTFail("not timers") }
        XCTAssertEqual(list.timers.first?.remaining, 90.5)
    }

    func testUnknownCardFromANewerCoreIsIgnored() throws {
        let card = try decode("""
        {"id": 6, "card": "movie_details", "title": "Dune", "created_at": 1, "ttl_s": 60, "data": {}}
        """)
        XCTAssertEqual(card.payload, .unknown)
    }
}
