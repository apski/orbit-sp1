// Signposts for the navigation lab, shared by both arms: the RN app (via LabModule.swift) and the
// UIKit twin (native/, which compiles this file directly), so both are measured with the same ruler. Everything the analyzer reads comes from these:
// intervals (boot/descend/push/render/pop) and events (ready/hitch/done), subsystem "lab",
// category PointsOfInterest so Time Profiler's POI track shows them next to CPU samples.
// The message is "k=v k=v"; analyze.py parses it.

import QuartzCore
import UIKit
import os

private let signposter = OSSignposter(subsystem: "lab", category: .pointsOfInterest)
private var open: [Int: (String, OSSignpostIntervalState)] = [:]
private var nextId = 0
private let lock = NSLock()

func labBegin(_ name: String, _ msg: String) -> Int {
  let id = signposter.makeSignpostID()
  let st: OSSignpostIntervalState
  // OSSignposter names must be StaticString.
  switch name {
  case "descend": st = signposter.beginInterval("descend", id: id, "\(msg, privacy: .public)")
  case "push": st = signposter.beginInterval("push", id: id, "\(msg, privacy: .public)")
  case "render": st = signposter.beginInterval("render", id: id, "\(msg, privacy: .public)")
  case "pop": st = signposter.beginInterval("pop", id: id, "\(msg, privacy: .public)")
  case "navaction": st = signposter.beginInterval("navaction", id: id, "\(msg, privacy: .public)")  // navprobe
  case "span": st = signposter.beginInterval("span", id: id, "\(msg, privacy: .public)")            // navprobe span()
  case "listmount": st = signposter.beginInterval("listmount", id: id, "\(msg, privacy: .public)")  // lists lab
  case "scroll": st = signposter.beginInterval("scroll", id: id, "\(msg, privacy: .public)")        // lists lab
  default: st = signposter.beginInterval("other", id: id, "\(name, privacy: .public) \(msg, privacy: .public)")
  }
  lock.lock(); defer { lock.unlock() }
  nextId += 1
  open[nextId] = (name, st)
  return nextId
}

func labEnd(_ handle: Int, _ msg: String) {
  lock.lock(); let o = open.removeValue(forKey: handle); lock.unlock()
  guard let (name, st) = o else { return }
  switch name {
  case "descend": signposter.endInterval("descend", st, "\(msg, privacy: .public)")
  case "push": signposter.endInterval("push", st, "\(msg, privacy: .public)")
  case "render": signposter.endInterval("render", st, "\(msg, privacy: .public)")
  case "pop": signposter.endInterval("pop", st, "\(msg, privacy: .public)")
  case "navaction": signposter.endInterval("navaction", st, "\(msg, privacy: .public)")
  case "span": signposter.endInterval("span", st, "\(msg, privacy: .public)")
  case "listmount": signposter.endInterval("listmount", st, "\(msg, privacy: .public)")
  case "scroll": signposter.endInterval("scroll", st, "\(msg, privacy: .public)")
  default: signposter.endInterval("other", st, "\(msg, privacy: .public)")
  }
}

func labEvent(_ name: String, _ msg: String) {
  switch name {
  case "ready": signposter.emitEvent("ready", "\(msg, privacy: .public)")
  // Thermal state rides on the run's first and last event: a long device sweep heats the phone, and
  // the report flags runs that ended above nominal.
  case "done": signposter.emitEvent("done", "\(msg, privacy: .public) thermal_end=\(labThermal(), privacy: .public)")
  case "config": signposter.emitEvent("config", "\(msg, privacy: .public) thermal=\(labThermal(), privacy: .public)")
  case "layout": signposter.emitEvent("layout", "\(msg, privacy: .public)")
  case "navstate": signposter.emitEvent("navstate", "\(msg, privacy: .public)")
  default: signposter.emitEvent("mark", "\(name, privacy: .public) \(msg, privacy: .public)")
  }
}

func labThermal() -> String {
  switch ProcessInfo.processInfo.thermalState {
  case .nominal: return "nominal"
  case .fair: return "fair"
  case .serious: return "serious"
  case .critical: return "critical"
  @unknown default: return "unknown"
  }
}

/// Resident memory as Xcode's memory gauge shows it.
func labFootprintMB() -> Double {
  var info = task_vm_info_data_t()
  var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
  let kr = withUnsafeMutablePointer(to: &info) {
    $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count) }
  }
  return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : -1
}

/// ms since the kernel started this process (includes dyld + pre-main).
func labSinceProcessStartMs() -> Double {
  var kp = kinfo_proc()
  var size = MemoryLayout<kinfo_proc>.stride
  var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
  guard sysctl(&mib, 4, &kp, &size, nil, 0) == 0 else { return -1 }
  let tv = kp.kp_proc.p_un.__p_starttime
  let start = Double(tv.tv_sec) * 1000 + Double(tv.tv_usec) / 1000
  return Date().timeIntervalSince1970 * 1000 - start
}

/// Main-thread frames that missed their deadline by > half a frame, as "hitch" events.
/// The analyzer attributes them to whichever interval they fall in.
final class HitchMonitor: NSObject {
  static let shared = HitchMonitor()
  private var link: CADisplayLink?
  private var last: CFTimeInterval = 0

  /// Also keeps the screen awake for the run: on a device, auto-lock mid-sweep breaks `xctrace --launch`.
  func start() {
    DispatchQueue.main.async {
      UIApplication.shared.isIdleTimerDisabled = true
      guard self.link == nil else { return }
      self.link = CADisplayLink(target: self, selector: #selector(self.tick))
      self.link?.add(to: .main, forMode: .common)
    }
  }

  @objc private func tick(_ l: CADisplayLink) {
    defer { last = l.timestamp }
    guard last > 0 else { return }
    let frame = l.targetTimestamp - l.timestamp
    let late = (l.timestamp - last) - frame
    if late > frame * 0.5 {
      signposter.emitEvent("hitch", "ms=\(Int((late * 1000).rounded()), privacy: .public)")
    }
  }
}

