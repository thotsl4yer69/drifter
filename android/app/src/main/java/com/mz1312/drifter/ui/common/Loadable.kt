package com.mz1312.drifter.ui.common

import com.mz1312.drifter.data.model.FailureKind

/** Generic async cell for a single screen value. */
sealed interface Loadable<out T> {
    data object Idle : Loadable<Nothing>
    data object Loading : Loadable<Nothing>
    data class Success<T>(val value: T) : Loadable<T>
    data class Error(val kind: FailureKind, val message: String) : Loadable<Nothing>

    val valueOrNull: T? get() = (this as? Success)?.value
}
