import SwiftUI

// Golden cases for ui_literal facts. Flagged lines carry their api; the rest must not match.
struct FeedStrings: View {
    @State var name = ""
    let n = 3
    var body: some View {
        VStack {
            Text("Welcome back")                                  // flagged: Text
            Text(L("feed_title"))                                 // localized
            Text(verbatim: "v1.2")                                // deliberate opt-out
            Text("")                                              // empty
            Text("\(n)")                                          // interpolation only
            Text("·")                                             // no letters
            Text("\(n) sources connected")                        // flagged: words around an interpolation
            Button("Add source") {}                               // flagged: Button
            Label("Settings", systemImage: "gearshape")           // flagged once: systemImage name is not text
            TextField("Search assets", text: $name)               // flagged: placeholder
            Image("hero")                                         // asset name
            // Text("commented out")
            Text("Hello") + Text("world")                         // flagged twice on one line
        }
        .navigationTitle("Feed")                                  // flagged
        .alert("Delete source?", isPresented: .constant(false)) {} // flagged
        .accessibilityLabel("Feed list")                          // flagged
        .accessibilityIdentifier("feed.list")                     // identifier, not text
        .onAppear { print("feed shown") }                         // log
    }
}

#Preview("Feed strings") {
    VStack {
        Text("Preview only")
    }
}

#Preview("One line") { Text("Also preview") }
