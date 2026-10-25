import SwiftUI

struct SettingsView: View {
    var body: some View {
        Toggle("Alerts", isOn: .constant(true))
    }
}
