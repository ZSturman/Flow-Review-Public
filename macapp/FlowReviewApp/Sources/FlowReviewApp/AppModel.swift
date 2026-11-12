import Foundation
import AppKit

/// Explicit overrides a person has chosen for a field Flow Review left
/// unresolved (or wants to change) -- the Advanced section. `nil` always
/// means "use what Core inferred," never "unset."
struct Overrides {
    var appTarget: String?
    var scheme: String?
    var platform: ReviewDestination?
    var device: String?
    var osVersion: String?
    var uiTestTarget: String?
    var projectName: String?
}

enum RunOutcome: Equatable {
    case succeeded
    case failed(String)
}

@MainActor
final class AppModel: ObservableObject {
    @Published var analysis: ProjectAnalysis?
    @Published var isAnalyzing = false
    @Published var isBusy = false
    @Published var busyLabel = ""
    @Published var logLines: [String] = []
    @Published var lastError: LocalizedError?
    @Published var overrides = Overrides()
    @Published var showAdvanced = false
    @Published var runOutcome: RunOutcome?
    @Published var cliMissing = false
    @Published private(set) var projectPath: String?

    private let cli = FlowReviewCLI()

    init(projectPath: String? = nil) {
        self.projectPath = projectPath
    }

    // MARK: Derived, effective values (override wins, otherwise Core's inference)

    var effectiveAppTarget: String? { overrides.appTarget ?? analysis?.appTarget.value }
    var effectiveScheme: String? { overrides.scheme ?? analysis?.scheme.value }
    var effectivePlatform: ReviewDestination {
        overrides.platform ?? ReviewDestination(rawValue: analysis?.platform.value ?? "iOS") ?? .iOS
    }
    var effectiveUITestTarget: String? { overrides.uiTestTarget ?? analysis?.uiTestTarget.value }
    var effectiveDevice: String? { overrides.device ?? analysis?.simulator.value }
    var effectiveProjectName: String { overrides.projectName ?? analysis?.projectName ?? "" }

    /// Ambiguity still blocking configuration, after accounting for anything
    /// the person has already resolved in Advanced.
    var unresolvedAmbiguities: [String] {
        var items: [String] = []
        if let a = analysis {
            if a.appTarget.ambiguous && overrides.appTarget == nil { items.append("app target") }
            if a.scheme.ambiguous && overrides.scheme == nil { items.append("build scheme") }
            if a.simulator.ambiguous && overrides.device == nil && effectivePlatform.usesSimulator { items.append("simulator") }
        }
        return items
    }

    var canConfigure: Bool { analysis?.hasXcodeproj == true && unresolvedAmbiguities.isEmpty }

    // MARK: Lifecycle

    func start() async {
        if projectPath == nil {
            projectPath = await Self.promptForProjectFolder()
        }
        guard projectPath != nil else { return }
        guard FlowReviewCLI.isInstalled else {
            cliMissing = true
            return
        }
        await refresh()
    }

    /// `NSOpenPanel.runModal()` cannot run synchronously from inside a
    /// SwiftUI `.task` (it fires mid view-update transaction and AppKit
    /// silently suppresses it); `begin(completionHandler:)` is the
    /// non-blocking equivalent, bridged back into async/await.
    private static func promptForProjectFolder() async -> String? {
        await withCheckedContinuation { continuation in
            DispatchQueue.main.async {
                let panel = NSOpenPanel()
                panel.title = "Choose a Project Folder"
                panel.message = "Flow Review was launched without a project. Choose the folder containing your Xcode project."
                panel.canChooseDirectories = true
                panel.canChooseFiles = false
                panel.canCreateDirectories = false
                panel.allowsMultipleSelection = false
                NSApp.activate(ignoringOtherApps: true)
                panel.begin { response in
                    continuation.resume(returning: response == .OK ? panel.url?.path : nil)
                }
            }
        }
    }

    func refresh() async {
        guard let projectPath else { return }
        isAnalyzing = true
        defer { isAnalyzing = false }
        do {
            let result = try await cli.analyze(projectPath: projectPath)
            analysis = result
            seedOverridesFromAnalysis(result)
        } catch {
            lastError = error as? LocalizedError ?? Wrapped(error)
        }
    }

    private func seedOverridesFromAnalysis(_ analysis: ProjectAnalysis) {
        // Only seed a field the person has not already touched, and only
        // when Core left it ambiguous -- resolved fields never need an
        // override to begin with.
        if overrides.appTarget == nil, !analysis.appTarget.ambiguous { overrides.appTarget = nil }
        if overrides.uiTestTarget == nil { overrides.uiTestTarget = analysis.uiTestTarget.value }
    }

    // MARK: Actions

    func createOrRepairConfiguration() async {
        guard let projectPath, let analysis, let appTarget = effectiveAppTarget, let scheme = effectiveScheme else { return }
        isBusy = true
        busyLabel = "Preparing Flow Review configuration…"
        defer { isBusy = false }
        let request = FlowReviewCLI.InitRequest(
            projectPath: projectPath,
            name: effectiveProjectName.isEmpty ? analysis.projectName : effectiveProjectName,
            platform: effectivePlatform,
            device: effectiveDevice,
            osVersion: overrides.osVersion,
            scheme: scheme,
            appTarget: appTarget,
            uiTestTarget: effectiveUITestTarget ?? "\(appTarget)FlowReviewUITests",
            force: analysis.existingConfig
        )
        do {
            try await cli.createOrRepairConfiguration(request)
            await refresh()
        } catch {
            lastError = error as? LocalizedError ?? Wrapped(error)
        }
    }

    func runReview(flowIDs: [String] = []) async {
        guard let projectPath else { return }
        isBusy = true
        busyLabel = flowIDs.isEmpty ? "Running all flows…" : "Running \(flowIDs.joined(separator: ", "))…"
        logLines = []
        runOutcome = nil
        defer { isBusy = false }
        do {
            for try await line in cli.streamRun(projectPath: projectPath, flowIDs: flowIDs, review: true) {
                logLines.append(line)
            }
            runOutcome = .succeeded
        } catch {
            runOutcome = .failed((error as? LocalizedError)?.errorDescription ?? "\(error)")
        }
        await refresh()
    }

    func revealProjectInFinder() {
        guard let analysis else { return }
        NSWorkspace.shared.activateFileViewerSelecting([analysis.projectURL])
    }

    func openXcodeProject() {
        guard let analysis, let path = analysis.xcodeprojPath ?? analysis.xcworkspacePath else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    func revealRunsFolder() {
        guard let analysis else { return }
        let runs = analysis.projectURL.appendingPathComponent(".flow-review/runs")
        NSWorkspace.shared.activateFileViewerSelecting([runs])
    }

    // MARK: Local AI

    func fetchLocalAIModels(provider: String, baseURL: String) async throws -> [String] {
        try await cli.fetchLocalAIModels(provider: provider, baseURL: baseURL)
    }

    func generateLocalAIDraft(provider: String, baseURL: String, model: String) async throws -> String {
        guard let projectPath else { throw CLIError.notInstalled }
        return try await cli.generateLocalAIDraft(projectPath: projectPath, provider: provider, baseURL: baseURL, model: model)
    }

    /// A normal macOS save: always asks where, suggests a project-relative
    /// default only the first time, and remembers the folder a person
    /// actually picked so repeat saves start there instead of silently
    /// reusing a hard-coded path. See docs/ARCHITECTURE.md ("save location").
    @discardableResult
    func saveUserDocument(suggestedName: String, defaultSubdirectory: String, contents: String) -> URL? {
        guard let analysis else { return nil }
        let panel = NSSavePanel()
        panel.title = "Save Flow Review Document"
        panel.nameFieldStringValue = suggestedName
        panel.canCreateDirectories = true
        if let remembered = SaveLocationMemory.lastDirectory {
            panel.directoryURL = remembered
        } else {
            panel.directoryURL = analysis.projectURL.appendingPathComponent(defaultSubdirectory)
        }
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try contents.write(to: url, atomically: true, encoding: .utf8)
            SaveLocationMemory.lastDirectory = url.deletingLastPathComponent()
            return url
        } catch {
            lastError = Wrapped(error)
            return nil
        }
    }
}

/// Persists only the directory a person chose in a save panel -- never a
/// value that changes what gets generated, so this stays a pure UI memory.
enum SaveLocationMemory {
    private static let key = "FlowReviewLastSaveDirectory"

    static var lastDirectory: URL? {
        get {
            guard let path = UserDefaults.standard.string(forKey: key) else { return nil }
            let url = URL(fileURLWithPath: path)
            return FileManager.default.fileExists(atPath: path) ? url : nil
        }
        set { UserDefaults.standard.set(newValue?.path, forKey: key) }
    }
}

/// Bridges a plain `Error` into `LocalizedError` for uniform display.
private struct Wrapped: LocalizedError {
    let underlying: Error
    init(_ underlying: Error) { self.underlying = underlying }
    var errorDescription: String? { "\(underlying)" }
}
