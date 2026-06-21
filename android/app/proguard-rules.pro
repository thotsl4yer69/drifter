# Keep kotlinx.serialization generated serializers.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class com.mz1312.drifter.** {
    *** Companion;
}
-keepclasseswithmembers class com.mz1312.drifter.** {
    kotlinx.serialization.KSerializer serializer(...);
}
