package com.mz1312.drifter.data.model

/** Why a request to the Pi failed — drives the troubleshooting copy. */
enum class FailureKind {
    /** TCP/connect refused or unreachable — Pi down, wrong IP, or not on the hotspot. */
    UNREACHABLE,
    /** Connected but the request timed out — Pi loaded, slow link, or wedged service. */
    TIMEOUT,
    /** HTTP 403 — request came from off the 10.42.0.0/24 subnet (not on the hotspot). */
    FORBIDDEN,
    /** HTTP 503 — dashboard up but a required service is degraded. */
    DEGRADED,
    /** HTTP 4xx/5xx other than the above. */
    HTTP_ERROR,
    /** Body did not parse as the expected JSON shape. */
    BAD_RESPONSE,
}

/** Minimal Result type so callers can branch on success vs. a classified failure. */
sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>
    data class Err(
        val kind: FailureKind,
        val message: String,
        val httpCode: Int? = null,
    ) : ApiResult<Nothing>
}

inline fun <T> ApiResult<T>.onOk(block: (T) -> Unit): ApiResult<T> {
    if (this is ApiResult.Ok) block(value)
    return this
}
