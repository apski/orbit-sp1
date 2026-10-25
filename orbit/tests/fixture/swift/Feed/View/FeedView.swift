import SwiftUI

struct FeedView: View {
    let store = UserDefaults.standard
    var body: some View { Text("feed") }
}
extension FeedView: Identifiable {}
