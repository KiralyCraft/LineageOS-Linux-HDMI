$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit_only.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/base_system.mk)

PRODUCT_NAME := hdmi_pdx234
PRODUCT_DEVICE := hdmi_pdx234
PRODUCT_BRAND := Sony
PRODUCT_MODEL := XQ-DQ72 HDMI build target
PRODUCT_MANUFACTURER := Sony

# Only these non-root Soong namespaces are relevant to the three replacement
# artifacts.  The source revisions still come from the installed-build manifest.
PRODUCT_SOONG_NAMESPACES += \
    hardware/qcom-caf/sm8550/display \
    kernel/sony/sm8550-modules \
    vendor/qcom/opensource/commonsys/display \
    vendor/qcom/opensource/commonsys-intf/display \
    vendor/qcom/opensource/display

