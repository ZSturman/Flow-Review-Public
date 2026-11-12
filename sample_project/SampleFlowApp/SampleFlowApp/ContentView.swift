import SwiftUI

struct ContentView: View {
    @StateObject private var store = ItemStore()
    @State private var showingAddItem = false

    var body: some View {
        NavigationStack {
            List {
                if store.items.isEmpty {
                    Text("No items yet")
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("emptyState")
                }
                ForEach(store.items, id: \.self) { item in
                    Text(item)
                        .accessibilityIdentifier("item-\(item)")
                }
                .onDelete(perform: store.delete)
            }
            .navigationTitle("Items")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingAddItem = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityIdentifier("addButton")
                }
            }
            .sheet(isPresented: $showingAddItem) {
                AddItemView(store: store, isPresented: $showingAddItem)
            }
        }
    }
}
