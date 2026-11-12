import XCTest

/// Interprets a `FlowDefinition` against a running `XCUIApplication`.
///
/// This is the one place flow-review steps turn into real XCUITest calls.
/// It is deliberately small: each case is a direct, literal translation of
/// one step type. If a flow needs logic beyond this vocabulary, write a
/// normal XCTestCase method instead of trying to grow this interpreter.
final class FlowEngine {
    enum EngineError: Error, CustomStringConvertible {
        case elementNotFound(String)
        case stepFailed(String)

        var description: String {
            switch self {
            case .elementNotFound(let d): return "Element not found: \(d)"
            case .stepFailed(let d): return "Step failed: \(d)"
            }
        }
    }

    let app: XCUIApplication
    let flow: FlowDefinition
    let reviewDelayMs: Int
    let resultsDir: URL
    let screenshotsDir: URL

    private var result: FlowResult
    private var screenshotCounter = 0
    private let startDate = Date()

    init(app: XCUIApplication, flow: FlowDefinition, reviewDelayMs: Int, resultsDir: URL) {
        self.app = app
        self.flow = flow
        self.reviewDelayMs = reviewDelayMs
        self.resultsDir = resultsDir
        self.screenshotsDir = resultsDir.appendingPathComponent("screenshots")
        self.result = FlowResult(
            flowId: flow.id,
            flowName: flow.name,
            automationStatus: "passed",
            startedAt: Self.iso(Date()),
            finishedAt: nil,
            durationSeconds: nil,
            steps: [],
            checkpoints: [],
            error: nil
        )
        try? FileManager.default.createDirectory(at: screenshotsDir, withIntermediateDirectories: true)
    }

    static func iso(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: date)
    }

    /// Runs every step, recording pass/fail per step, and always writes
    /// result.json -- even on failure -- so the CLI/review UI has something
    /// to show. Re-throws so XCTest still marks the test red on failure.
    func run() throws {
        writeCheckpointScreenshot(name: "flow-start", stepIndex: -1, stepLabel: "Flow start")

        for (i, step) in flow.steps.enumerated() {
            let label = Self.label(for: step)
            var stepResult = StepResult(
                index: i, type: step.type, label: label, status: "passed",
                startedAt: Self.iso(Date()), finishedAt: nil, message: nil
            )
            do {
                try perform(step)
                if reviewDelayMs > 0 {
                    Thread.sleep(forTimeInterval: Double(reviewDelayMs) / 1000.0)
                }
            } catch {
                stepResult.status = "failed"
                stepResult.message = String(describing: error)
                stepResult.finishedAt = Self.iso(Date())
                result.steps.append(stepResult)
                result.automationStatus = "failed"
                result.error = "Step \(i) (\(label)): \(error)"
                writeCheckpointScreenshot(name: "failure", stepIndex: i, stepLabel: "Failure: \(label)")
                finalize()
                throw error
            }
            stepResult.finishedAt = Self.iso(Date())
            result.steps.append(stepResult)
        }

        writeCheckpointScreenshot(name: "flow-end", stepIndex: flow.steps.count, stepLabel: "Flow end")
        finalize()
    }

    private func finalize() {
        result.finishedAt = Self.iso(Date())
        result.durationSeconds = Date().timeIntervalSince(startDate)
        write(result)
    }

    static func label(for step: FlowStep) -> String {
        switch step.type {
        case "launch": return "Launch app"
        case "terminate": return "Terminate app"
        case "relaunch": return "Relaunch app"
        case "tap": return "Tap \(locatorDescription(step))"
        case "type": return "Type into \(locatorDescription(step))"
        case "swipe": return "Swipe \(step.direction ?? "")"
        case "scroll": return "Scroll \(step.direction ?? "")"
        case "wait_for": return "Wait for \(locatorDescription(step))"
        case "assert_exists": return "Assert exists: \(locatorDescription(step))"
        case "assert_not_exists": return "Assert not exists: \(locatorDescription(step))"
        case "assert_text": return "Assert text on \(locatorDescription(step))"
        case "pause": return "Pause"
        case "screenshot": return "Checkpoint: \(step.name ?? "screenshot")"
        case "checkpoint": return "Checkpoint: \(step.name ?? "checkpoint")"
        case "reset_app_state": return "Reset app state"
        default: return step.type
        }
    }

    private static func locatorDescription(_ step: FlowStep) -> String {
        if let id = step.accessibilityId { return id }
        if let text = step.text { return "text '\(text)'" }
        if let label = step.label { return "label '\(label)'" }
        return "?"
    }

    // MARK: - Step execution

    private func perform(_ step: FlowStep) throws {
        switch step.type {
        case "launch":
            app.launchArguments += flow.launchArguments
            var env = app.launchEnvironment
            for (k, v) in flow.launchEnvironment { env[k] = v }
            if flow.resetAppState {
                app.launchArguments.append("-RESET_TEST_DATA")
                env["FLOW_REVIEW_RESET_TEST_DATA"] = "1"
            }
            env["FLOW_REVIEW"] = "1"
            app.launchEnvironment = env
            app.launch()

        case "terminate":
            app.terminate()

        case "relaunch":
            app.terminate()
            app.launch()

        case "tap":
            try element(for: step).tap()

        case "type":
            let el = element(for: step)
            if step.clearFirst == true, let existing = el.value as? String, !existing.isEmpty {
                el.tap()
                let deleteString = existing.map { _ in "\u{8}" }.joined()
                el.typeText(deleteString)
            } else {
                el.tap()
            }
            el.typeText(step.value ?? "")

        case "swipe", "scroll":
            let target: XCUIElement = step.accessibilityId != nil ? element(for: step) : app
            switch step.direction {
            case "up": target.swipeUp()
            case "down": target.swipeDown()
            case "left": target.swipeLeft()
            case "right": target.swipeRight()
            default: throw EngineError.stepFailed("unknown direction \(step.direction ?? "nil")")
            }

        case "wait_for":
            let el = element(for: step)
            let timeout = step.timeout ?? 10
            guard el.waitForExistence(timeout: timeout) else {
                throw EngineError.elementNotFound(Self.locatorDescription(step))
            }

        case "assert_exists":
            guard element(for: step).exists else {
                throw EngineError.elementNotFound(Self.locatorDescription(step))
            }

        case "assert_not_exists":
            if element(for: step).exists {
                throw EngineError.stepFailed("expected \(Self.locatorDescription(step)) to not exist")
            }

        case "assert_text":
            let el = element(for: step)
            guard el.exists else { throw EngineError.elementNotFound(Self.locatorDescription(step)) }
            let actual = (el.value as? String) ?? el.label
            if let equals = step.equals, actual != equals {
                throw EngineError.stepFailed("expected text '\(equals)', got '\(actual)'")
            }
            if let contains = step.contains, !actual.contains(contains) {
                throw EngineError.stepFailed("expected text to contain '\(contains)', got '\(actual)'")
            }

        case "pause":
            Thread.sleep(forTimeInterval: Double(step.durationMs ?? 500) / 1000.0)

        case "screenshot", "checkpoint":
            let name = step.name ?? step.type
            let idx = result.steps.count
            let path = writeCheckpointScreenshot(name: name, stepIndex: idx, stepLabel: name)
            result.checkpoints.append(CheckpointResult(
                name: name, stepIndex: idx, screenshot: path, timestamp: Self.iso(Date())
            ))

        case "reset_app_state":
            app.terminate()
            app.launchArguments.append("-RESET_TEST_DATA")
            var env = app.launchEnvironment
            env["FLOW_REVIEW_RESET_TEST_DATA"] = "1"
            app.launchEnvironment = env
            app.launch()

        default:
            throw EngineError.stepFailed("unknown step type \(step.type)")
        }
    }

    private func element(for step: FlowStep) -> XCUIElement {
        let query: XCUIElementQuery
        if let id = step.accessibilityId {
            query = app.descendants(matching: .any).matching(identifier: id)
        } else if let text = step.text {
            query = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR value == %@", text, text))
        } else if let label = step.label {
            query = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS[c] %@", label))
        } else {
            query = app.descendants(matching: .any).matching(identifier: "__flow_review_no_locator__")
        }
        if let index = step.index {
            return query.element(boundBy: index)
        }
        return query.firstMatch
    }

    // MARK: - Artifact writing

    @discardableResult
    private func writeCheckpointScreenshot(name: String, stepIndex: Int, stepLabel: String) -> String {
        screenshotCounter += 1
        let safeName = name.lowercased().replacingOccurrences(of: " ", with: "-")
        let filename = String(format: "%02d-%@.png", screenshotCounter, safeName)
        // Screen-level (not app-level) screenshot: stays valid even before the
        // app has launched or after it has crashed/terminated.
        let data = XCUIScreen.main.screenshot().pngRepresentation
        let url = screenshotsDir.appendingPathComponent(filename)
        try? data.write(to: url)
        // Preserve the checkpoint in the .xcresult bundle. The test runner's
        // temporary directory is not stable after a run, whereas attachments
        // can be inspected in Xcode or exported with xcresulttool.
        let attachment = XCTAttachment(data: data)
        attachment.name = filename
        attachment.lifetime = .keepAlways
        XCTContext.runActivity(named: "Flow Review: \(stepLabel)") { activity in
            activity.add(attachment)
        }
        return filename
    }

    private func write(_ result: FlowResult) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(result) else { return }
        let url = resultsDir.appendingPathComponent("result.json")
        try? data.write(to: url)
    }
}
