import Foundation

/// One setting Flow Review's Core inferred: a resolved value when it is
/// confident, or a candidate list when a human genuinely needs to choose.
/// Mirrors `inference.Choice.to_json()` in the Python core -- this struct is
/// the actual contract between the two, not a guess at one.
struct Choice: Codable, Equatable {
    var value: String?
    var options: [String]
    var source: String
    var ambiguous: Bool
    var detail: String

    var isResolved: Bool { value != nil }
}

struct EnvironmentCheck: Codable, Equatable, Identifiable {
    var name: String
    var ok: Bool
    var detail: String
    var id: String { name }
}

/// Mirrors `inference.ProjectAnalysis.to_json()`. Decoded with
/// `.convertFromSnakeCase` so this stays a direct copy of the Python field
/// names without hand-maintained `CodingKeys`.
struct ProjectAnalysis: Codable, Equatable {
    var root: String
    var steps: [String]
    var errors: [String]

    var hasXcodeproj: Bool
    var xcodeprojPath: String?
    var xcworkspacePath: String?

    var appTargets: [String]
    var schemes: [String]
    var uiTestTargets: [String]

    var existingConfig: Bool
    var projectName: String
    var flowCount: Int
    var flowIds: [String]

    var appTarget: Choice
    var scheme: Choice
    var platform: Choice
    var uiTestTarget: Choice
    var simulator: Choice

    var environmentOk: Bool
    var environment: [EnvironmentCheck]

    var readyToRun: Bool
    var needsSetup: Bool
    var blockingReason: String
    var fullyResolved: Bool

    var projectURL: URL { URL(fileURLWithPath: root) }
}

/// A destination Flow Review can review against. iOS/iPadOS/macOS are wired
/// up today; the case list intentionally leaves room to grow rather than
/// scattering "is this iOS?" checks through the UI. Raw values match the
/// platform strings the Python core already uses in config.yaml.
enum ReviewDestination: String, CaseIterable, Codable, Identifiable {
    case iOS = "iOS"
    case iPadOS = "iPadOS"
    case macOS = "macOS"

    var id: String { rawValue }
    var usesSimulator: Bool { self != .macOS }
    var symbolName: String {
        switch self {
        case .iOS: return "iphone"
        case .iPadOS: return "ipad"
        case .macOS: return "macbook"
        }
    }
}

/// Deliberately not modeled as a case here yet: Flow Review's run pipeline
/// (simulator.py / xcodebuild destinations) only knows iOS/iPadOS/macOS
/// today. Adding watchOS means teaching that pipeline a new destination
/// type, not just adding a UI case -- see docs/ARCHITECTURE.md.
