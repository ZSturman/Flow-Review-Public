import SwiftUI

@main
struct SampleFlowApp: App {
    init() {
        // Flow Review test-data convention: an app opts into this by checking
        // for the same launch argument / environment variable Flow Review sets.
        // See docs/testing-hooks.md in the flow-review tool for the full protocol.
        if ProcessInfo.processInfo.arguments.contains("-RESET_TEST_DATA") {
            ItemStore.resetPersistedState()
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
