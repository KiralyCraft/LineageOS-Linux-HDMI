plugins {
    id("com.android.application")
}

val hdmiProfile = providers.gradleProperty("hdmiProfile").orElse("development")
val hdmiLineage = providers.gradleProperty("hdmiLineage").orElse("unknown")
val hdmiVersionName = providers.gradleProperty("hdmiVersionName").orElse("development")
val hdmiVersionCode = providers.gradleProperty("hdmiVersionCode").orElse("1")

android {
    namespace = "dev.kiraly.hdmilos"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.kiraly.hdmilos"
        minSdk = 35
        targetSdk = 35
        versionCode = hdmiVersionCode.get().toInt()
        versionName = "${hdmiVersionName.get()}-${hdmiProfile.get()}"
        buildConfigField("String", "HDMI_PROFILE", "\"${hdmiProfile.get()}\"")
        buildConfigField("String", "HDMI_LINEAGE", "\"${hdmiLineage.get()}\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
