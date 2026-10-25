// Expo binding for LabCore.swift.
import ExpoModulesCore

public class LabModule: Module {
  public func definition() -> ModuleDefinition {
    Name("Lab")

    // Launch args, e.g. `-depth 3 -kind tabs`. xctrace --launch passes them through.
    Constant("args") { ProcessInfo.processInfo.arguments }

    Function("begin") { (name: String, msg: String) -> Int in labBegin(name, msg) }
    Function("end") { (handle: Int, msg: String) in labEnd(handle, msg) }
    Function("event") { (name: String, msg: String) in labEvent(name, msg) }
    Function("footprintMB") { labFootprintMB() }
    Function("sinceProcessStartMs") { labSinceProcessStartMs() }
    Function("startHitches") { HitchMonitor.shared.start() }
    // Ends the process so `xctrace record --launch` stops on its own.
    Function("exit") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { exit(0) } }

    View(LabProbeView.self) {
      Prop("handle") { (view: LabProbeView, h: Int) in view.handle = h }
      Prop("event") { (view: LabProbeView, e: String) in view.event = e }
    }
  }
}

/// Zero-size view placed last in a screen. One runloop turn after the screen's native subtree is
/// attached to the window (so the CA commit has laid it out) it ends signpost interval `handle`, or for
/// event "ready" emits the startup event once: the same "content on screen" point the UIKit twin uses.
/// RN's onLayout fires earlier, from the shadow tree, before the main thread has mounted anything.
final class LabProbeView: ExpoView {
  private static var readySent = false
  var handle = 0 { didSet { fire() } }
  var event = "" { didSet { fire() } }

  override func didMoveToWindow() {
    super.didMoveToWindow()
    fire()
  }

  private func fire() {
    guard window != nil else { return }
    if handle > 0 {
      let h = handle
      handle = 0
      DispatchQueue.main.async { labEnd(h, "") }
    }
    if event == "ready", !Self.readySent {
      Self.readySent = true
      DispatchQueue.main.async {
        labEvent("ready", String(format: "ms=%.1f mb=%.1f", labSinceProcessStartMs(), labFootprintMB()))
      }
    }
  }
}
