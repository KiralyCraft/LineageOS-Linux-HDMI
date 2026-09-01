#ifndef HDMI_LOS_PROPERTY_CACHE_H
#define HDMI_LOS_PROPERTY_CACHE_H

#include <stddef.h>
#include <stdint.h>

enum { HDMI_LOS_MAX_CACHED_CONNECTOR_PROPERTIES = 512 };

struct hdmi_los_property_cache_entry {
  uint32_t connector_id;
  uint32_t property_id;
  uint64_t value;
};

struct hdmi_los_property_cache {
  struct hdmi_los_property_cache_entry entries[HDMI_LOS_MAX_CACHED_CONNECTOR_PROPERTIES];
  size_t count;
};

static int hdmi_los_property_cache_store(struct hdmi_los_property_cache *cache,
                                         uint32_t connector_id,
                                         uint32_t property_id, uint64_t value) {
  for (size_t index = 0; index < cache->count; ++index) {
    struct hdmi_los_property_cache_entry *entry = &cache->entries[index];
    if (entry->connector_id == connector_id && entry->property_id == property_id) {
      entry->value = value;
      return 1;
    }
  }
  if (cache->count >= HDMI_LOS_MAX_CACHED_CONNECTOR_PROPERTIES) return 0;
  cache->entries[cache->count++] =
      (struct hdmi_los_property_cache_entry){connector_id, property_id, value};
  return 1;
}

static int hdmi_los_property_cache_is_noop(const struct hdmi_los_property_cache *cache,
                                           uint32_t connector_id,
                                           uint32_t property_id, uint64_t value) {
  for (size_t index = 0; index < cache->count; ++index) {
    const struct hdmi_los_property_cache_entry *entry = &cache->entries[index];
    if (entry->connector_id == connector_id && entry->property_id == property_id) {
      return entry->value == value;
    }
  }
  return 0;
}

static int hdmi_los_xorg_property_type_is_unsupported(uint32_t flags,
                                                      uint32_t bitmask_flag) {
  return (flags & bitmask_flag) != 0;
}

#endif
