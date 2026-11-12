import SwiftUI

/// Local AI is an action inside the normal window, not a reason for more
/// setup dialogs: this sheet only ever talks to a local URL the person
/// chooses, and never contacts anything until they press Fetch or Generate.
struct LocalAIDraftSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var provider = "lm_studio"
    @State private var baseURL = "http://127.0.0.1:1234/v1"
    @State private var models: [String] = []
    @State private var selectedModel = ""
    @State private var draft = ""
    @State private var status = "Select a local server, load a model there, then fetch its model list."
    @State private var isWorking = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Local AI Flow Drafts").font(.title2.bold())
            Text("Flow Review connects only to the local URL below and sends only bounded UI source context, never automatically.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Picker("Provider", selection: $provider) {
                Text("LM Studio").tag("lm_studio")
                Text("Local notebook").tag("local_notebook")
            }
            .onChange(of: provider) { _, newValue in
                baseURL = newValue == "lm_studio" ? "http://127.0.0.1:1234/v1" : "http://127.0.0.1:11434/v1"
            }

            LabeledContent("Server URL") { TextField("", text: $baseURL) }
            LabeledContent("Model") {
                Picker("", selection: $selectedModel) {
                    ForEach(models, id: \.self) { Text($0).tag($0) }
                }.labelsHidden()
            }

            Text(status).font(.caption).foregroundStyle(.secondary)

            TextEditor(text: $draft)
                .font(.system(.body, design: .monospaced))
                .frame(minHeight: 220)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.3)))

            HStack {
                Button("Fetch Models") { Task { await fetchModels() } }.disabled(isWorking)
                Button("Generate Draft") { Task { await generateDraft() } }.disabled(isWorking || selectedModel.isEmpty)
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save Reviewed Draft…") { saveDraft() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 560, height: 520)
    }

    private func fetchModels() async {
        isWorking = true
        defer { isWorking = false }
        do {
            models = try await model.fetchLocalAIModels(provider: provider, baseURL: baseURL)
            selectedModel = models.first ?? ""
            status = models.isEmpty ? "No loaded models were found at \(baseURL)." : "Found \(models.count) loaded model(s). No source has been sent."
        } catch {
            status = "\((error as? LocalizedError)?.errorDescription ?? "\(error)")"
        }
    }

    private func generateDraft() async {
        isWorking = true
        defer { isWorking = false }
        do {
            draft = try await model.generateLocalAIDraft(provider: provider, baseURL: baseURL, model: selectedModel)
            status = "Draft received. Review and edit before saving; it is never added to runnable flows automatically."
        } catch {
            status = "\((error as? LocalizedError)?.errorDescription ?? "\(error)")"
        }
    }

    private func saveDraft() {
        let timestamp = Self.timestampFormatter.string(from: Date())
        if model.saveUserDocument(
            suggestedName: "ai-flow-draft-\(timestamp).yaml",
            defaultSubdirectory: "flow-review/suggestions",
            contents: draft
        ) != nil {
            dismiss()
        }
    }

    private static let timestampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter
    }()
}
