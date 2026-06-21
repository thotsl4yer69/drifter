package com.mz1312.drifter.data.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.Inet4Address

/** What the phone's current network can tell us about reaching the node. */
data class NetworkInfo(
    val onWifi: Boolean,
    /** Default-route gateway on the active network — on the MZ1312_DRIFTER
     *  hotspot this IS the Pi (10.42.0.1), so it's the best auto-detect host. */
    val gateway: String?,
)

/**
 * Reads the live network state via ConnectivityManager (no extra permission
 * beyond ACCESS_NETWORK_STATE, no deprecated WifiManager). The node runs its
 * own Wi-Fi hotspot, so the default-route gateway of the connected Wi-Fi is the
 * Pi — that makes "detect the node" a reliable one-tap action instead of asking
 * the operator to type an IP.
 */
class NetworkInspector(private val context: Context) {

    fun inspect(): NetworkInfo {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return NetworkInfo(onWifi = false, gateway = null)
        val active = cm.activeNetwork ?: return NetworkInfo(onWifi = false, gateway = null)
        val caps = cm.getNetworkCapabilities(active)
        val onWifi = caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        val gateway = cm.getLinkProperties(active)?.routes
            ?.firstOrNull { it.isDefaultRoute && it.gateway is Inet4Address }
            ?.gateway?.hostAddress
        return NetworkInfo(onWifi = onWifi, gateway = gateway)
    }
}
