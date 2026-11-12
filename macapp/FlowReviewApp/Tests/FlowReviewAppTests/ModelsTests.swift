import XCTest
@testable import FlowReviewApp

final class ProjectAnalysisDecodingTests: XCTestCase {
    /// A real payload shape captured from `flow-review analyze --json` against
    /// the bundled sample project -- not a hand-invented schema, so this test
    /// fails the moment the Swift model and the Python core drift apart.
    static let sampleJSON = """
    {
      "root": "/tmp/SampleFlowApp",
      "steps": ["Analyzing SampleFlowApp…", "Configuration ready"],
      "errors": [],
      "has_xcodeproj": true,
      "xcodeproj_path": "/tmp/SampleFlowApp/SampleFlowApp.xcodeproj",
      "xcworkspace_path": null,
      "app_targets": ["SampleFlowApp"],
      "schemes": ["SampleFlowApp"],
      "ui_test_targets": ["SampleFlowAppUITests"],
      "existing_config": true,
      "project_name": "Sample Flow App",
      "flow_count": 1,
      "flow_ids": ["create-item"],
      "app_target": {"value": "SampleFlowApp", "options": ["SampleFlowApp"], "source": "existing_config", "ambiguous": false, "detail": ""},
      "scheme": {"value": "SampleFlowApp", "options": ["SampleFlowApp"], "source": "existing_config", "ambiguous": false, "detail": ""},
      "platform": {"value": "iOS", "options": ["iOS", "iPadOS", "macOS"], "source": "existing_config", "ambiguous": false, "detail": ""},
      "ui_test_target": {"value": "SampleFlowAppUITests", "options": ["SampleFlowAppUITests"], "source": "existing_config", "ambiguous": false, "detail": ""},
      "simulator": {"value": "iPhone 17 Pro", "options": ["iPhone 17 Pro", "iPhone 17"], "source": "existing_config", "ambiguous": false, "detail": ""},
      "environment_ok": true,
      "environment": [{"name": "xcodebuild", "ok": true, "detail": "/usr/bin/xcodebuild"}],
      "ready_to_run": true,
      "needs_setup": false,
      "blocking_reason": "",
      "fully_resolved": true
    }
    """

    private func decode(_ json: String) throws -> ProjectAnalysis {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(ProjectAnalysis.self, from: Data(json.utf8))
    }

    func test_decodes_a_fully_resolved_ready_project() throws {
        let analysis = try decode(Self.sampleJSON)
        XCTAssertTrue(analysis.readyToRun)
        XCTAssertFalse(analysis.needsSetup)
        XCTAssertEqual(analysis.appTarget.value, "SampleFlowApp")
        XCTAssertEqual(analysis.simulator.options.count, 2)
        XCTAssertTrue(analysis.fullyResolved)
    }

    func test_ambiguous_choice_has_no_resolved_value() throws {
        let json = Self.sampleJSON.replacingOccurrences(
            of: "\"app_target\": {\"value\": \"SampleFlowApp\", \"options\": [\"SampleFlowApp\"], \"source\": \"existing_config\", \"ambiguous\": false, \"detail\": \"\"}",
            with: "\"app_target\": {\"value\": null, \"options\": [\"SampleFlowApp\", \"SampleFlowAppWatch\"], \"source\": \"ambiguous\", \"ambiguous\": true, \"detail\": \"Multiple application targets were found.\"}"
        )
        let analysis = try decode(json)
        XCTAssertFalse(analysis.appTarget.isResolved)
        XCTAssertTrue(analysis.appTarget.ambiguous)
        XCTAssertEqual(analysis.appTarget.options.count, 2)
    }
}

final class ReviewDestinationTests: XCTestCase {
    func test_macos_never_uses_a_simulator() {
        XCTAssertFalse(ReviewDestination.macOS.usesSimulator)
        XCTAssertTrue(ReviewDestination.iOS.usesSimulator)
        XCTAssertTrue(ReviewDestination.iPadOS.usesSimulator)
    }

    func test_raw_values_match_the_python_core_platform_strings() {
        // These are the literal strings written into flow-review/config.yaml
        // by the Python core (config.DeviceConfig.platform); a mismatch here
        // would silently desync the app from every project's saved config.
        XCTAssertEqual(ReviewDestination(rawValue: "iOS"), .iOS)
        XCTAssertEqual(ReviewDestination(rawValue: "iPadOS"), .iPadOS)
        XCTAssertEqual(ReviewDestination(rawValue: "macOS"), .macOS)
    }
}

final class AppModelResolutionTests: XCTestCase {
    @MainActor
    func test_override_wins_over_ambiguous_inference() throws {
        let analysis = try JSONDecoder.flowReview.decode(ProjectAnalysis.self, from: Data(
            ProjectAnalysisDecodingTests.sampleJSON.replacingOccurrences(
                of: "\"app_target\": {\"value\": \"SampleFlowApp\", \"options\": [\"SampleFlowApp\"], \"source\": \"existing_config\", \"ambiguous\": false, \"detail\": \"\"}",
                with: "\"app_target\": {\"value\": null, \"options\": [\"SampleFlowApp\", \"SampleFlowAppWatch\"], \"source\": \"ambiguous\", \"ambiguous\": true, \"detail\": \"\"}"
            ).utf8
        ))
        let model = AppModel(projectPath: "/tmp/SampleFlowApp")
        model.analysis = analysis
        XCTAssertNil(model.effectiveAppTarget)
        XCTAssertEqual(model.unresolvedAmbiguities, ["app target"])
        model.overrides.appTarget = "SampleFlowAppWatch"
        XCTAssertEqual(model.effectiveAppTarget, "SampleFlowAppWatch")
        XCTAssertTrue(model.unresolvedAmbiguities.isEmpty)
    }
}

extension JSONDecoder {
    static var flowReview: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }
}
