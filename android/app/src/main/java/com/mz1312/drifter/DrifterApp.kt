package com.mz1312.drifter

import android.app.Application
import com.mz1312.drifter.data.DrifterRepository
import com.mz1312.drifter.data.store.SettingsStore

/** Hand-rolled DI container — no Hilt, just the two singletons the app needs. */
class AppContainer(app: Application) {
    val settingsStore: SettingsStore = SettingsStore(app)
    val repository: DrifterRepository = DrifterRepository()
}

class DrifterApp : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
