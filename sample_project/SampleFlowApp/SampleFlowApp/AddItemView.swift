import SwiftUI

struct AddItemView: View {
    @ObservedObject var store: ItemStore
    @Binding var isPresented: Bool
    @State private var title: String = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Title", text: $title)
                    .accessibilityIdentifier("titleField")
            }
            .accessibilityIdentifier("itemForm")
            .navigationTitle("New Item")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        store.add(title)
                        isPresented = false
                    }
                    .accessibilityIdentifier("saveButton")
                    .disabled(title.isEmpty)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        isPresented = false
                    }
                    .accessibilityIdentifier("cancelButton")
                }
            }
        }
    }
}
