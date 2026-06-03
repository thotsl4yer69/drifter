# MZ1312 Map Customization

Brand-styled map for the Drifter cockpit and companion app, following Google's
[cloud customization taxonomy](https://developers.google.com/maps/documentation/android-sdk/cloud-customization/taxonomy).

Aesthetic: matte-black base (`#0a0a0f`), electric-purple accent (`#8B5CF6`)
reserved for the road hierarchy you actually navigate by, muted labels, POI
business clutter suppressed.

## Two ways to apply it (and why both exist)

Google has **two** styling systems with **different taxonomies**:

| System | Taxonomy | How styled | Where it works |
|---|---|---|---|
| **Cloud-based styling** (new) | Point of Interest / Political / Infrastructure / Natural | In the **Cloud Console**, referenced by a **Map ID** | Maps SDK Android/iOS, Maps JS *vector* |
| **JSON styling** (legacy) | `administrative` / `landscape` / `poi` / `road` / `transit` / `water` × `geometry`/`labels` | A `styles` array **in code** | Maps JS, `react-native-maps` `customMapStyle`, Leaflet+GoogleMutant |

The cloud taxonomy in the linked doc is **Console-only** — it cannot be expressed
as a code-side array, and it needs a Map ID created in *your* Google Cloud
project (which can't be done from this repo). So:

- **`ui/map-style-mz1312.json`** is the **legacy `styles`** realization — drop-in,
  works today, no Console needed. The cockpit uses it now (see below).
- This doc's **taxonomy mapping** below is the spec to recreate the same look as
  a **cloud Map ID** when you want the new system (recommended for the Android
  app / vector maps).

## Cloud taxonomy → MZ1312 (build this in the Console, publish, get a Map ID)

| Cloud feature type | MZ1312 treatment |
|---|---|
| **Natural** → Land / Land cover | geometry `#0a0a0f`–`#0c0c12` |
| **Natural** → Water | geometry `#06060c`, label fill `#3a4a6a` |
| **Infrastructure** → Road network → Road | local `#101018`, arterial `#1b1830` |
| **Infrastructure** → Road (highway/ramp/controlled-access) | geometry `#2a2350`→`#3a2f6b`, **stroke `#8B5CF6`**, label `#c4b5fd` |
| **Infrastructure** → Railway track / Transit station | geometry `#161620`–`#1d1a2e`, label `#8a7fb0` |
| **Infrastructure** → Building / Urban area | geometry `#101018`, stroke `#16161f` |
| **Political** → Country / State / City borders | stroke `#2a2a3a`–`#3a3450` |
| **Political** → City label | text `#a78bfa` (accent-tinted) |
| **Political** → Neighborhood / Land parcel | neighborhood `#6b6b80`; land parcel **off** |
| **POI** → Recreation (Park, Nature reserve) | geometry `#0f1512`, label `#5c7a63` |
| **POI** → Emergency (Hospital/Pharmacy) | geometry `#1a1014` |
| **POI** → Retail / Food & drink / Service (business) | **off** (declutter) |
| **POI** → Entertainment / Landmark | label `#7a6b9a` |

Element model (cloud): set **Fill color**, **Stroke color/width**, **Text fill**,
**Text stroke**, **Visibility** per the table; for POI pins set **Pin fill**
`#1a1726`, **Pin outline** `#8B5CF6`, **Pin glyph** `#c4b5fd`.

### Console steps
1. Cloud Console → **Google Maps Platform → Map Styles → Create Map Style** → start blank/dark.
2. Apply the colors above to each feature category (the editor groups them as
   POI / Political / Infrastructure / Natural exactly like the table).
3. **Publish**, then create/attach a **Map ID** (vector) to the style.
4. Set it for the apps:
   - Drifter env: `GHOST_MAP_ID=<your-map-id>` (read by the app config).
   - Android: `<meta-data android:name="com.google.android.geo.MAP_ID" .../>` or `GoogleMapOptions.mapId(...)`.
   - Maps JS vector: `new google.maps.Map(el, { mapId: GHOST_MAP_ID })`.

> When a `mapId` is set, the cloud style wins and any in-code `styles` array is
> ignored — use one or the other, not both.

## Cockpit (Leaflet) — already wired

The cockpit map is Leaflet, which can't consume a Map ID, so it renders the
**legacy JSON** via Google tiles (GoogleMutant). The basemap toggle now cycles
**dark → satellite → mz1312**. The Google key is served local-only from
`/api/mapconfig` (same `_is_local_peer` gate as the rest of the dashboard); if no
key is configured the `mz1312` basemap is hidden and the toggle falls back to
dark/sat. Google basemaps need internet (same as the existing CARTO/Esri tiles).

## React Native app (`drifter-app`)

When the app gains a map, use `react-native-maps`:

```tsx
import MapView from 'react-native-maps';
import mz1312 from './map-style-mz1312.json';

// Legacy JSON (no Console needed):
<MapView customMapStyle={mz1312} />
// or cloud style (after you publish a Map ID):
<MapView googleMapId={process.env.GHOST_MAP_ID} />
```

## Security note
`GOOGLE_MAPS_API_KEY` is server-side today (Elevation/Places). Exposing it to the
cockpit for Maps JS is gated to the local hotspot, but you should still add an
**HTTP-referrer / IP restriction** to the key in the Cloud Console and, ideally,
a **separate Maps-JS key** distinct from the server key.
