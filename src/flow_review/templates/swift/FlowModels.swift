import Foundation

// Generic, data-driven model for a Flow Review flow.
//
// This intentionally mirrors the project's flow YAML almost 1:1 (the Python
// CLI just does the YAML -> JSON conversion + validation). Keeping the step
// vocabulary small and flat means this stays a thin interpreter rather than
// a bespoke automation language -- anything more complex than these steps
// should be a hand-written XCTestCase instead.

struct FlowDefinition: Decodable {
    let id: String
    let name: String
    let platform: String?
    let tags: [String]
    let launchArguments: [String]
    let launchEnvironment: [String: String]
    let reviewDelayMs: Int?
    let resetAppState: Bool
    let steps: [FlowStep]

    enum CodingKeys: String, CodingKey {
        case id, name, platform, tags
        case launchArguments = "launch_arguments"
        case launchEnvironment = "launch_environment"
        case reviewDelayMs = "review_delay_ms"
        case resetAppState = "reset_app_state"
        case steps
    }
}

struct FlowStep: Decodable {
    let type: String
    let accessibilityId: String?
    let text: String?
    let label: String?
    let index: Int?
    let value: String?
    let clearFirst: Bool?
    let direction: String?
    let timeout: Double?
    let equals: String?
    let contains: String?
    let durationMs: Int?
    let name: String?

    enum CodingKeys: String, CodingKey {
        case type
        case accessibilityId = "accessibility_id"
        case text, label, index, value
        case clearFirst = "clear_first"
        case direction, timeout, equals, contains
        case durationMs = "duration_ms"
        case name
    }
}

/// A run-wide setting baked in alongside the flow JSON so a build doesn't
/// need to be redone just to change human-playback pacing.
struct RunContext: Decodable {
    let reviewDelayMs: Int

    enum CodingKeys: String, CodingKey {
        case reviewDelayMs = "review_delay_ms"
    }
}

// MARK: - Result reporting (written by the engine, read by the CLI)

struct StepResult: Encodable {
    let index: Int
    let type: String
    let label: String
    var status: String // "passed" | "failed" | "skipped"
    let startedAt: String
    var finishedAt: String?
    var message: String?

    enum CodingKeys: String, CodingKey {
        case index, type, label, status, message
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

struct CheckpointResult: Encodable {
    let name: String
    let stepIndex: Int
    let screenshot: String
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case name, screenshot, timestamp
        case stepIndex = "step_index"
    }
}

struct FlowResult: Encodable {
    let flowId: String
    let flowName: String
    var automationStatus: String // "passed" | "failed"
    let startedAt: String
    var finishedAt: String?
    var durationSeconds: Double?
    var steps: [StepResult]
    var checkpoints: [CheckpointResult]
    var error: String?

    enum CodingKeys: String, CodingKey {
        case flowId = "flow_id"
        case flowName = "flow_name"
        case automationStatus = "automation_status"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case durationSeconds = "duration_seconds"
        case steps, checkpoints, error
    }
}
