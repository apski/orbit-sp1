package expo.modules.lab

import android.app.Activity
import android.content.Context
import android.os.Bundle
import expo.modules.core.BasePackage
import expo.modules.core.interfaces.ReactActivityLifecycleListener
import lab.LabCore

/** Reads the launch args before the JS bundle can ask for them. */
class LabPackage : BasePackage() {
  override fun createReactActivityLifecycleListeners(activityContext: Context): List<ReactActivityLifecycleListener> =
    listOf(object : ReactActivityLifecycleListener {
      override fun onCreate(activity: Activity, savedInstanceState: Bundle?) = LabCore.init(activity)
    })
}
