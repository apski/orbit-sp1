// orbitfleet's fixture app: an lldb-attachable simulator app for the P2-P4 live checks (scripts/testapp.sh).
import SwiftUI

@main
struct OFHello: App {
    var body: some Scene {
        WindowGroup {
            Text("orbitfleet test app")
                .accessibilityIdentifier("hello")
                .onAppear {
                    print("hello stdout")
                    NSLog("hello nslog")
                }
        }
    }
}
