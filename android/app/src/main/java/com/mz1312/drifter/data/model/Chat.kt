package com.mz1312.drifter.data.model

/** Turn author in the assistant conversation. */
enum class ChatRole { USER, ASSISTANT }

/**
 * One message in the troubleshooting conversation. [via] tags an assistant
 * turn with where the answer came from ("Claude · …", "Pi on-board LLM",
 * "refused", "error") so the UI can show provenance — important when the
 * cloud brain silently falls back to the Pi's own LLM (or vice-versa).
 */
data class ChatMessage(
    val role: ChatRole,
    val text: String,
    val via: String? = null,
    val ts: Long = System.currentTimeMillis(),
)

/** Outcome of one assistant turn. */
sealed interface AssistantReply {
    /** A usable answer. [via] is the provenance label shown in the UI. */
    data class Ok(val text: String, val via: String) : AssistantReply

    /** The model declined (Claude `stop_reason: "refusal"`). */
    data class Refused(val explanation: String) : AssistantReply

    /** No answer — transport/auth/config problem; [message] is operator-facing. */
    data class Failed(val message: String) : AssistantReply
}
