import Foundation

/// Everything the app knows about running Flow Review lives behind this one
/// type: it shells out to the same `flow-review` CLI a Terminal user would
/// run. The Core/inference/build/test logic is not reimplemented here -- the
/// app is a client of it, so `flow-review analyze`/`run`/`init` keep working
/// unchanged from a terminal or CI script.
enum CLIError: LocalizedError {
    case notInstalled
    case processFailed(command: String, exitCode: Int32, output: String)
    case invalidOutput(String)

    var errorDescription: String? {
        switch self {
        case .notInstalled:
            return "Flow Review's command-line core was not found."
        case .processFailed(let command, let exitCode, let output):
            return "\(command) exited with status \(exitCode).\n\n\(output)"
        case .invalidOutput(let detail):
            return "Flow Review returned output the app could not parse: \(detail)"
        }
    }

    var recoverySuggestion: String? {
        switch self {
        case .notInstalled:
            return "From Terminal, run `flow-review install-finder` from the Flow Review checkout to (re)install this app and its command-line link."
        default:
            return nil
        }
    }
}

struct FlowReviewCLI {
    /// Written by `flow-review install-finder`, alongside this app itself,
    /// so the app never has to guess which Python environment owns
    /// `flow-review`. See docs/ARCHITECTURE.md.
    static var shimURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".flow-review/bin/flow-review")
    }

    static var isInstalled: Bool {
        FileManager.default.isExecutableFile(atPath: shimURL.path)
    }

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private func makeProcess(_ arguments: [String]) throws -> Process {
        guard FlowReviewCLI.isInstalled else { throw CLIError.notInstalled }
        let process = Process()
        process.executableURL = FlowReviewCLI.shimURL
        process.arguments = arguments
        return process
    }

    private func runCapturing(_ arguments: [String]) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            do {
                let process = try makeProcess(arguments)
                let stdout = Pipe()
                let stderr = Pipe()
                process.standardOutput = stdout
                process.standardError = stderr
                process.terminationHandler = { proc in
                    let out = String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                    let err = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                    if proc.terminationStatus == 0 {
                        continuation.resume(returning: out)
                    } else {
                        let combined = [out, err].filter { !$0.isEmpty }.joined(separator: "\n")
                        continuation.resume(throwing: CLIError.processFailed(
                            command: arguments.joined(separator: " "),
                            exitCode: proc.terminationStatus,
                            output: combined
                        ))
                    }
                }
                try process.run()
            } catch let error as CLIError {
                continuation.resume(throwing: error)
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }

    // MARK: Analysis

    func analyze(projectPath: String) async throws -> ProjectAnalysis {
        let output = try await runCapturing(["analyze", projectPath, "--json"])
        guard let data = output.data(using: .utf8) else {
            throw CLIError.invalidOutput(output)
        }
        do {
            return try FlowReviewCLI.decoder.decode(ProjectAnalysis.self, from: data)
        } catch {
            throw CLIError.invalidOutput("\(error)\n\n\(output.prefix(2000))")
        }
    }

    // MARK: Configuration

    struct InitRequest {
        var projectPath: String
        var name: String
        var platform: ReviewDestination
        var device: String?
        var osVersion: String?
        var scheme: String
        var appTarget: String
        var uiTestTarget: String
        var force: Bool
    }

    func createOrRepairConfiguration(_ request: InitRequest) async throws {
        var args = [
            "init", request.projectPath,
            "--name", request.name,
            "--platform", request.platform.rawValue,
            "--scheme", request.scheme,
            "--app-target", request.appTarget,
            "--ui-test-target", request.uiTestTarget,
        ]
        if let device = request.device, !device.isEmpty { args += ["--device", device] }
        if let osVersion = request.osVersion, !osVersion.isEmpty { args += ["--os-version", osVersion] }
        if request.force { args.append("--force") }
        _ = try await runCapturing(args)
    }

    // MARK: Running a review

    /// Streams `flow-review run`'s stdout line by line as it happens, so the
    /// UI can show a live, passive log instead of waiting silently for a
    /// process that can take a minute or more (build + simulator boot +
    /// XCUITest + video encode).
    func streamRun(projectPath: String, flowIDs: [String], review: Bool) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            var arguments = ["run", projectPath]
            for id in flowIDs { arguments += ["--flow", id] }
            if review { arguments.append("--review") }

            do {
                let process = try makeProcess(arguments)
                let stdout = Pipe()
                process.standardOutput = stdout
                process.standardError = stdout

                stdout.fileHandleForReading.readabilityHandler = { handle in
                    let data = handle.availableData
                    guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
                    for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
                        continuation.yield(String(line))
                    }
                }
                process.terminationHandler = { proc in
                    stdout.fileHandleForReading.readabilityHandler = nil
                    if proc.terminationStatus == 0 {
                        continuation.finish()
                    } else {
                        continuation.finish(throwing: CLIError.processFailed(
                            command: arguments.joined(separator: " "),
                            exitCode: proc.terminationStatus,
                            output: ""
                        ))
                    }
                }
                continuation.onTermination = { _ in
                    if process.isRunning { process.terminate() }
                }
                try process.run()
            } catch {
                continuation.finish(throwing: error)
            }
        }
    }

    // MARK: Local AI

    func fetchLocalAIModels(provider: String, baseURL: String) async throws -> [String] {
        let output = try await runCapturing(["ai-models", "--provider", provider, "--base-url", baseURL])
        return output.split(separator: "\n").map(String.init).filter { !$0.isEmpty }
    }

    func generateLocalAIDraft(projectPath: String, provider: String, baseURL: String, model: String) async throws -> String {
        try await runCapturing([
            "ai-discover", projectPath, "--provider", provider, "--base-url", baseURL, "--model", model,
        ])
    }
}
