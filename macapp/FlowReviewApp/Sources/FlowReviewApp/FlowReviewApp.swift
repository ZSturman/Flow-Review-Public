import SwiftUI

/// Disables AppKit's window state restoration. Without this, a process
/// that was previously force-quit (or just killed mid-test) leaves saved
/// window state behind; the next launch then blocks its first window on a
/// restoration prompt that never appears when the app was opened via
/// `open -n` rather than a normal Dock/Finder double-click, so no window
/// ever shows even though the process is running. `NSQuitAlwaysKeepsWindows
/// = false` in Info.plist alone is not sufficient -- this is the documented
/// per-app override.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { false }
}

/// One process = one project. Finder launches a fresh instance per
/// invocation (`open -n`), so there is no multi-window / multi-project
/// state to juggle here -- each window's model owns exactly the project it
/// was opened for.
@main
struct FlowReviewMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model: AppModel

    init() {
        let args = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-") }
        let path = args.first(where: { FileManager.default.fileExists(atPath: $0) })
        _model = StateObject(wrappedValue: AppModel(projectPath: path))
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .task { await model.start() }
        }
        .windowResizability(.contentSize)
        .defaultSize(width: 660, height: 600)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}
