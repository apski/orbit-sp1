import SwiftUI

/// Hosts one generated screen for the parity judge. Launch arguments:
///   -screen <Name>          a struct from Screens.generated.swift (written by `spectra preview register`)
///   -frame x,y,w,h          the container the source laid the screen out in (default: the whole window)
///   -bg r,g,b               container background, 0-255 (default white)
/// Every node frame is logged as `ORBIT_FRAMES <composition> [[x,y,w,h],...]` (preorder, points).
@main
struct PreviewApp: App {
  var body: some Scene {
    WindowGroup {
      let d = UserDefaults.standard
      let name = d.string(forKey: "screen") ?? spectraScreenNames.first ?? ""
      let f = d.string(forKey: "frame").map(numbers)
      let bg = d.string(forKey: "bg").map(numbers) ?? [255, 255, 255]
      let color = Color(red: bg[0] / 255, green: bg[1] / 255, blue: bg[2] / 255)
      if let f, f.count == 4 {  // a fixed container: the screen is placed exactly where the source laid it out
        ZStack(alignment: .topLeading) {
          color
          spectraScreen(name, onFrames: log).frame(width: f[2], height: f[3]).padding(.leading, f[0]).padding(.top, f[1])
        }
        .ignoresSafeArea()
      } else {  // the whole window: no wrapper that ignores the safe area, or the screen would see zero insets
        spectraScreen(name, onFrames: log).background(color)
      }
    }
  }
}

func numbers(_ s: String) -> [CGFloat] { s.split(separator: ",").map { CGFloat(Double($0) ?? 0) } }

func log(_ composition: Int, _ frames: [CGRect]) {
  let rows = frames.map { "[\($0.minX),\($0.minY),\($0.width),\($0.height)]" }.joined(separator: ",")
  NSLog("ORBIT_FRAMES %d [%@]", composition, rows)
}
