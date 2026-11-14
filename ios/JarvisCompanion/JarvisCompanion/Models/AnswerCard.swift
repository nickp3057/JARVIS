import Foundation

/// An answer screen JARVIS put up (`brain/answer_cards.py`), from Core's
/// `GET /api/clients/events?only=cards`. One payload type per card; an
/// unknown card (a newer Core) decodes as `.unknown` and isn't shown.
struct AnswerCard: Decodable, Identifiable, Equatable {
    let id: Int
    let card: String
    let title: String
    let createdAt: Double
    let ttl: Double
    let payload: Payload

    enum Payload: Equatable {
        case weather(WeatherToday)
        case calendar(CalendarDay)
        case timers(TimerList)
        case unknown
    }

    enum CodingKeys: String, CodingKey {
        case id, card, title, data
        case createdAt = "created_at"
        case ttl = "ttl_s"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(Int.self, forKey: .id)
        card = try values.decode(String.self, forKey: .card)
        title = (try? values.decode(String.self, forKey: .title)) ?? ""
        createdAt = (try? values.decode(Double.self, forKey: .createdAt)) ?? Date().timeIntervalSince1970
        ttl = (try? values.decode(Double.self, forKey: .ttl)) ?? 60
        switch card {
        case "weather_today":
            payload = (try? values.decode(WeatherToday.self, forKey: .data)).map(Payload.weather) ?? .unknown
        case "calendar_day":
            payload = (try? values.decode(CalendarDay.self, forKey: .data)).map(Payload.calendar) ?? .unknown
        case "timers":
            payload = (try? values.decode(TimerList.self, forKey: .data)).map(Payload.timers) ?? .unknown
        default:
            payload = .unknown
        }
    }

    init(id: Int, card: String, title: String, createdAt: Double, ttl: Double, payload: Payload) {
        self.id = id
        self.card = card
        self.title = title
        self.createdAt = createdAt
        self.ttl = ttl
        self.payload = payload
    }

    struct WeatherToday: Decodable, Equatable {
        let location: String?
        let tempF: Int?
        let description: String?
        let condition: String?
        let isDay: Bool?
        let highF: Int?
        let lowF: Int?
        let humidityPercent: Int?
        let windMph: Int?
        let gustMph: Int?
        let rainChance: Int?
        let sunrise: Double?
        let sunset: Double?
        let hours: [Hour]

        struct Hour: Decodable, Equatable, Identifiable {
            let at: Double
            let tempF: Int?
            let rainChance: Int?
            let condition: String?
            let isDay: Bool?
            var id: Double { at }

            enum CodingKeys: String, CodingKey {
                case at, condition
                case tempF = "temp_f"
                case rainChance = "rain_chance"
                case isDay = "is_day"
            }
        }

        enum CodingKeys: String, CodingKey {
            case location, description, condition, sunrise, sunset, hours
            case tempF = "temp_f"
            case isDay = "is_day"
            case highF = "high_f"
            case lowF = "low_f"
            case humidityPercent = "humidity_percent"
            case windMph = "wind_mph"
            case gustMph = "gust_mph"
            case rainChance = "rain_chance"
        }

        init(from decoder: Decoder) throws {
            let v = try decoder.container(keyedBy: CodingKeys.self)
            location = try? v.decodeIfPresent(String.self, forKey: .location)
            tempF = try? v.decodeIfPresent(Int.self, forKey: .tempF)
            description = try? v.decodeIfPresent(String.self, forKey: .description)
            condition = try? v.decodeIfPresent(String.self, forKey: .condition)
            isDay = try? v.decodeIfPresent(Bool.self, forKey: .isDay)
            highF = try? v.decodeIfPresent(Int.self, forKey: .highF)
            lowF = try? v.decodeIfPresent(Int.self, forKey: .lowF)
            humidityPercent = try? v.decodeIfPresent(Int.self, forKey: .humidityPercent)
            windMph = try? v.decodeIfPresent(Int.self, forKey: .windMph)
            gustMph = try? v.decodeIfPresent(Int.self, forKey: .gustMph)
            rainChance = try? v.decodeIfPresent(Int.self, forKey: .rainChance)
            sunrise = try? v.decodeIfPresent(Double.self, forKey: .sunrise)
            sunset = try? v.decodeIfPresent(Double.self, forKey: .sunset)
            hours = (try? v.decodeIfPresent([Hour].self, forKey: .hours)) ?? []
        }
    }

    struct CalendarDay: Decodable, Equatable {
        let events: [Event]

        struct Event: Decodable, Equatable, Identifiable {
            let title: String
            let startAt: Double
            let allDay: Bool
            let location: String?
            let calendar: String?
            var id: String { "\(startAt)-\(title)" }

            enum CodingKeys: String, CodingKey {
                case title, location, calendar
                case startAt = "start_at"
                case allDay = "all_day"
            }
        }
    }

    struct TimerList: Decodable, Equatable {
        let timers: [Row]

        struct Row: Decodable, Equatable, Identifiable {
            let id: String
            let label: String
            let remaining: Double
            let paused: Bool
            let alarming: Bool

            enum CodingKeys: String, CodingKey {
                case id, label, paused, alarming
                case remaining = "remaining_s"
            }
        }
    }
}

extension AnswerCard {
    /// An SF Symbol for an Open-Meteo condition word (`brain/weather.py`'s `condition`).
    static func weatherSymbol(_ condition: String?, day: Bool) -> String {
        switch condition ?? "" {
        case "clear": return day ? "sun.max.fill" : "moon.stars.fill"
        case "partly": return day ? "cloud.sun.fill" : "cloud.moon.fill"
        case "fog": return "cloud.fog.fill"
        case "drizzle": return "cloud.drizzle.fill"
        case "rain": return "cloud.rain.fill"
        case "snow": return "cloud.snow.fill"
        case "storm": return "cloud.bolt.rain.fill"
        default: return "cloud.fill"
        }
    }
}
