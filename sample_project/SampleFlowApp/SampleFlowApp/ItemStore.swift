import Foundation
import Combine

final class ItemStore: ObservableObject {
    private static let key = "flowreview.sample.items"

    @Published var items: [String]

    init() {
        items = UserDefaults.standard.stringArray(forKey: Self.key) ?? []
    }

    func add(_ title: String) {
        items.append(title)
        UserDefaults.standard.set(items, forKey: Self.key)
    }

    func delete(at offsets: IndexSet) {
        items.remove(atOffsets: offsets)
        UserDefaults.standard.set(items, forKey: Self.key)
    }

    static func resetPersistedState() {
        UserDefaults.standard.removeObject(forKey: key)
    }
}
