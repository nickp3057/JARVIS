import XCTest
@testable import JarvisCompanion

/// Decoding of the room endpoints the phone and iPad read, including replies
/// from an older Core that lacks the newer fields.
final class HomeModelTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    func testEntitiesWithLightDetailsAndSun() throws {
        let body = try decode(HomeEntities.self, """
        {"error": "", "sun": "below_horizon", "entities": [
          {"entity_id": "light.desk", "friendly_name": "Desk", "domain": "light", "state": "on",
           "light": {"dimmable": true, "color": true, "temperature": true, "brightness_pct": 50,
                     "rgb": [255, 0, 0], "kelvin": null, "min_kelvin": 2000, "max_kelvin": 6500,
                     "rainbow": true}},
          {"entity_id": "switch.lamp", "friendly_name": "Lamp", "domain": "switch", "state": "off"}]}
        """)
        XCTAssertEqual(body.sun, "below_horizon")
        XCTAssertEqual(body.entities[0].light?.brightnessPct, 50)
        XCTAssertEqual(body.entities[0].light?.rainbow, true)
        XCTAssertTrue(body.entities[0].isOn)
        XCTAssertNil(body.entities[1].light)
        XCTAssertTrue(body.entities[1].isToggleable)
    }

    func testOlderCoreWithoutSunOrLights() throws {
        let body = try decode(HomeEntities.self, """
        {"error": "", "entities": [{"entity_id": "light.desk", "friendly_name": "Desk",
                                    "domain": "light", "state": "unavailable"}]}
        """)
        XCTAssertNil(body.sun)
        XCTAssertNil(body.entities[0].light)
        XCTAssertFalse(body.entities[0].isToggleable)
    }

    func testOlderLightWithoutRainbow() throws {
        let light = try decode(LightDetails.self, """
        {"dimmable": true, "color": false, "temperature": false, "brightness_pct": null,
         "rgb": null, "kelvin": null, "min_kelvin": null, "max_kelvin": null}
        """)
        XCTAssertNil(light.rainbow)
    }

    func testScenesAndTheirSymbols() throws {
        let body = try decode(HomeScenes.self, """
        {"error": "", "last": "scene.movie", "scenes": [
          {"entity_id": "scene.movie", "name": "Movie", "icon": "mdi:movie-open", "last_activated": null},
          {"entity_id": "scene.good_morning", "name": "Good Morning", "icon": "", "last_activated": null},
          {"entity_id": "scene.peace", "name": "Peace", "icon": "mdi:hand-peace", "last_activated": null},
          {"entity_id": "scene.x", "name": "Something", "icon": "", "last_activated": null}]}
        """)
        XCTAssertEqual(body.last, "scene.movie")
        XCTAssertEqual(body.scenes.map(\.symbol), ["film", "sun.horizon", "hand.wave", "sparkles"])
    }

    func testVoiceState() throws {
        let state = try decode(VoiceState.self, """
        {"seq": 7, "phase": "listening", "transcript": "game time", "reply": "", "at": 1793211685.3}
        """)
        XCTAssertEqual(state.phase, "listening")
        XCTAssertEqual(state.transcript, "game time")
        XCTAssertEqual(VoiceState.idle.phase, "idle")
    }
}
