import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            if model.cliMissing {
                CLIMissingView()
            } else if model.projectPath == nil {
                Text("No project folder was chosen.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ProjectWindowView()
            }
        }
        .frame(minWidth: 640, minHeight: 560)
    }
}

private struct CLIMissingView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Flow Review's command-line core was not found", systemImage: "exclamationmark.triangle.fill")
                .font(.title3.bold())
                .foregroundStyle(.orange)
            Text("This app is a client of the `flow-review` command line tool and does not work without it.")
            Text("From Terminal, inside your Flow Review checkout, run:")
            Text("flow-review install-finder")
                .font(.system(.body, design: .monospaced))
                .padding(8)
                .background(Color.secondary.opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            Text("That installs both this app and the link it uses to reach the Python core, then reopen Flow Review from Finder.")
                .foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// The one consolidated window: project identity, what Core detected, live
/// status, and the actions that make sense right now. This replaces the old
/// chain of eight-plus sequential dialogs with a single stateful view.
struct ProjectWindowView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                if let analysis = model.analysis {
                    DetectedSummaryView(analysis: analysis)
                    StatusListView(analysis: analysis)
                    if !model.logLines.isEmpty || model.isBusy {
                        RunLogView()
                    }
                    if let outcome = model.runOutcome {
                        RunOutcomeBanner(outcome: outcome)
                    }
                    if let error = model.lastError {
                        ErrorBanner(error: error)
                    }
                    ActionBar(analysis: analysis)
                    DisclosureGroup("Advanced", isExpanded: $model.showAdvanced) {
                        AdvancedSettingsView(analysis: analysis)
                    }
                    .padding(.top, 4)
                } else if model.isAnalyzing {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Analyzing \(URL(fileURLWithPath: model.projectPath ?? "").lastPathComponent)…")
                    }
                } else if let error = model.lastError {
                    ErrorBanner(error: error)
                }
                Spacer(minLength: 0)
                FooterBar()
            }
            .padding(20)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Flow Review")
                .font(.largeTitle.bold())
            if let path = model.projectPath {
                Text(URL(fileURLWithPath: path).lastPathComponent)
                    .font(.title2)
                Text(path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }
    }
}

private struct DetectedSummaryView: View {
    let analysis: ProjectAnalysis
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionHeading("Detected")
            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 6) {
                if analysis.appTarget.ambiguous {
                    pickerRow("App", options: analysis.appTarget.options, selection: Binding(
                        get: { model.overrides.appTarget ?? analysis.appTarget.options.first ?? "" },
                        set: { model.overrides.appTarget = $0 }
                    ))
                } else {
                    staticRow("App", value: model.effectiveAppTarget, choice: analysis.appTarget)
                }

                if analysis.scheme.ambiguous {
                    pickerRow("Scheme", options: analysis.scheme.options, selection: Binding(
                        get: { model.overrides.scheme ?? analysis.scheme.options.first ?? "" },
                        set: { model.overrides.scheme = $0 }
                    ))
                } else {
                    staticRow("Scheme", value: model.effectiveScheme, choice: analysis.scheme)
                }

                staticRow("Platform", value: model.effectivePlatform.rawValue, choice: analysis.platform)

                if model.effectivePlatform.usesSimulator {
                    if analysis.simulator.ambiguous {
                        pickerRow("Simulator", options: analysis.simulator.options, selection: Binding(
                            get: { model.overrides.device ?? analysis.simulator.options.first ?? "" },
                            set: { model.overrides.device = $0 }
                        ))
                    } else {
                        staticRow("Simulator", value: model.effectiveDevice, choice: analysis.simulator)
                    }
                }

                staticRow("UI Tests", value: model.effectiveUITestTarget, choice: analysis.uiTestTarget)
            }
        }
    }

    private func staticRow(_ label: String, value: String?, choice: Choice) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary)
            HStack(spacing: 6) {
                Text(value ?? "—")
                if !choice.detail.isEmpty {
                    Text(choice.detail).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func pickerRow(_ label: String, options: [String], selection: Binding<String>) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary)
            Picker("", selection: selection) {
                ForEach(options, id: \.self) { Text($0).tag($0) }
            }.labelsHidden()
        }
    }
}

private struct StatusListView: View {
    let analysis: ProjectAnalysis

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            SectionHeading("Status")
            ForEach(analysis.steps, id: \.self) { step in
                Label(step, systemImage: "checkmark.circle.fill")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            ForEach(analysis.errors, id: \.self) { error in
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.callout)
                    .foregroundStyle(.orange)
            }
            if !analysis.blockingReason.isEmpty && analysis.needsSetup {
                Label(analysis.blockingReason, systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(.blue)
            }
        }
    }
}

private struct ActionBar: View {
    let analysis: ProjectAnalysis
    @EnvironmentObject private var model: AppModel
    @State private var showAIDraft = false

    var body: some View {
        HStack(spacing: 10) {
            if analysis.readyToRun {
                Button {
                    Task { await model.runReview() }
                } label: {
                    Label("Run Review", systemImage: "play.fill")
                }
                .keyboardShortcut(.defaultAction)
                .disabled(model.isBusy)
            } else if model.canConfigure {
                Button {
                    Task { await model.createOrRepairConfiguration() }
                } label: {
                    Label(analysis.existingConfig ? "Repair Configuration" : "Prepare Configuration", systemImage: "gearshape.fill")
                }
                .keyboardShortcut(.defaultAction)
                .disabled(model.isBusy)
            }

            if analysis.existingConfig {
                Button { showAIDraft = true } label: {
                    Label("Draft Flow with Local AI", systemImage: "sparkles")
                }
                .disabled(model.isBusy)
                Button {
                    model.saveUserDocument(
                        suggestedName: "new-flow.yaml",
                        defaultSubdirectory: "flow-review/flows",
                        contents: FlowTemplate.starter
                    )
                } label: {
                    Label("Create Flow Manually", systemImage: "doc.badge.plus")
                }
                .disabled(model.isBusy)
            }

            if model.isBusy {
                ProgressView().controlSize(.small)
                Text(model.busyLabel).foregroundStyle(.secondary).font(.callout)
            }
        }
        .sheet(isPresented: $showAIDraft) { LocalAIDraftSheet() }
    }
}

private struct RunLogView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionHeading("Progress")
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(Array(model.logLines.enumerated()), id: \.offset) { index, line in
                            Text(line).font(.system(.caption, design: .monospaced)).id(index)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 160)
                .background(Color.secondary.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .onChange(of: model.logLines.count) { _, count in
                    proxy.scrollTo(count - 1, anchor: .bottom)
                }
            }
        }
    }
}

private struct RunOutcomeBanner: View {
    let outcome: RunOutcome

    var body: some View {
        switch outcome {
        case .succeeded:
            Label("All flows passed. Review the video and .xcresult in .flow-review/runs.", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed(let detail):
            VStack(alignment: .leading, spacing: 4) {
                Label("A flow failed", systemImage: "xmark.circle.fill").foregroundStyle(.red)
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(4)
            }
        }
    }
}

private struct ErrorBanner: View {
    let error: LocalizedError
    @EnvironmentObject private var model: AppModel
    @State private var showDetails = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(error.errorDescription ?? "Something went wrong", systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red)
                .font(.headline)
            if let suggestion = error.recoverySuggestion {
                Text(suggestion).font(.callout)
            }
            DisclosureGroup("Details", isExpanded: $showDetails) {
                Text(error.errorDescription ?? "")
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
            }
            HStack {
                Button("Retry") { Task { await model.refresh() } }
                Button("Open Project") { model.revealProjectInFinder() }
            }
        }
        .padding(12)
        .background(Color.red.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct AdvancedSettingsView: View {
    let analysis: ProjectAnalysis
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 8) {
            GridRow {
                Text("Project name").foregroundStyle(.secondary)
                TextField("", text: Binding(
                    get: { model.overrides.projectName ?? analysis.projectName },
                    set: { model.overrides.projectName = $0 }
                ))
            }
            GridRow {
                Text("App target").foregroundStyle(.secondary)
                Picker("", selection: Binding(
                    get: { model.effectiveAppTarget ?? "" },
                    set: { model.overrides.appTarget = $0 }
                )) {
                    ForEach(analysis.appTargets, id: \.self) { Text($0).tag($0) }
                }.labelsHidden()
            }
            GridRow {
                Text("Scheme").foregroundStyle(.secondary)
                Picker("", selection: Binding(
                    get: { model.effectiveScheme ?? "" },
                    set: { model.overrides.scheme = $0 }
                )) {
                    ForEach(analysis.schemes, id: \.self) { Text($0).tag($0) }
                }.labelsHidden()
            }
            GridRow {
                Text("Platform").foregroundStyle(.secondary)
                Picker("", selection: Binding(
                    get: { model.effectivePlatform },
                    set: { model.overrides.platform = $0 }
                )) {
                    ForEach(ReviewDestination.allCases) { Text($0.rawValue).tag($0) }
                }.labelsHidden()
            }
            if model.effectivePlatform.usesSimulator {
                GridRow {
                    Text("Simulator").foregroundStyle(.secondary)
                    TextField("", text: Binding(
                        get: { model.effectiveDevice ?? "" },
                        set: { model.overrides.device = $0 }
                    ))
                }
                GridRow {
                    Text("Runtime (optional)").foregroundStyle(.secondary)
                    TextField("", text: Binding(
                        get: { model.overrides.osVersion ?? "" },
                        set: { model.overrides.osVersion = $0 }
                    ))
                }
            }
            GridRow {
                Text("UI-test target").foregroundStyle(.secondary)
                TextField("", text: Binding(
                    get: { model.effectiveUITestTarget ?? "" },
                    set: { model.overrides.uiTestTarget = $0 }
                ))
            }
        }
        .padding(.top, 6)
    }
}

private struct FooterBar: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack(spacing: 14) {
            Button("Reveal in Finder") { model.revealProjectInFinder() }
            if model.analysis?.xcodeprojPath != nil {
                Button("Open in Xcode") { model.openXcodeProject() }
            }
            Button("Show Runs") { model.revealRunsFolder() }
        }
        .font(.callout)
        .buttonStyle(.link)
    }
}

private struct SectionHeading: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased())
            .font(.caption.bold())
            .foregroundStyle(.secondary)
    }
}

enum FlowTemplate {
    static let starter = """
    # Flow Review flow. See docs/FLOW_FORMAT.md for the full step vocabulary.
    id: new-flow
    name: New Flow
    tags: []
    steps:
      - launch
      - tap:
          accessibility_id: "some-button"
      - checkpoint:
          name: "after-tap"
    """
}
