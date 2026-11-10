#pragma once

#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS

#include <array>
#include <cstddef>
#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

struct RawOokMetadata {
  size_t count{0};
  int64_t poll_us{0};
  int64_t rearm_us{0};
  int64_t arm_us{0};
  int64_t callback_us{0};
  int64_t seal_us{0};
  int64_t prior_gap_us{0};
  uint32_t callback_count{0};
  uint32_t queue_losses{0};
  uint32_t ignored_callbacks{0};
  uint32_t ignored_symbols{0};
  uint32_t sequence{0};
  bool saturated{false};
  bool timed_out{false};
  bool final_seen{false};
};

// Preserve both RMT levels/durations, including terminal zero durations.
struct RawOokWindow : RawOokMetadata {
  static constexpr size_t CAPACITY = 4096;
  std::array<uint32_t, CAPACITY> words{};

  static constexpr uint32_t pack(uint16_t duration0, bool level0, uint16_t duration1, bool level1) {
    return (duration0 & 0x7FFFU) | (static_cast<uint32_t>(level0) << 15U) |
           (static_cast<uint32_t>(duration1 & 0x7FFFU) << 16U) | (static_cast<uint32_t>(level1) << 31U);
  }

  static uint32_t checksum(const uint32_t* data, size_t length) {
    uint32_t hash = 2166136261U;
    for (size_t index = 0; index < length; index++) {
      for (unsigned shift = 0; shift < 32; shift += 8) {
        hash = (hash ^ ((data[index] >> shift) & 0xFFU)) * 16777619U;
      }
    }
    return hash;
  }

  uint32_t checksum() const {
    return checksum(words.data(), count);
  }

  bool has_frame_prefix() const {
    // Screen a derived stream only: never trim or quantize the exported words.
    // Seven structural words also retain the prefix of extended frames, even
    // when their integrity is unknown or a later repeat is truncated.
    std::array<uint8_t, 26> streaks{};
    uint32_t bits = 0;
    size_t bit_count = 0;
    auto bit = [&](bool high) {
      bits = ((bits << 1U) | static_cast<uint32_t>(high)) & 0x3FFFFFFU;
      if (++bit_count < 26)
        return false;
      auto& streak = streaks[bit_count % 26];
      if ((bits & 0x3C00003U) != 0x3800002U) {
        streak = 0;
        return false;
      }
      uint16_t value = 0;
      uint8_t ones = 0;
      for (unsigned index = 0; index < 9; index++) {
        const uint8_t pair = (bits >> (20U - index * 2U)) & 3U;
        if (pair != 1 && pair != 2) {
          streak = 0;
          return false;
        }
        value = (value << 1U) | (pair == 2);
        ones += pair == 2;
      }
      const uint8_t parity = (bits >> 2U) & 3U;
      if (parity != ((ones & 1U) ? 2 : 1)) {
        streak = 0;
        return false;
      }
      streak = (value & 1U) ? 1 : (streak == 0 ? 0 : streak + 1);
      return streak >= 7;
    };
    for (size_t index = 0; index < count; index++) {
      for (unsigned shift : {0U, 16U}) {
        const uint32_t duration = (words[index] >> shift) & 0x7FFFU;
        if (duration == 0)
          continue;
        const bool high = (words[index] & (1U << (shift + 15U))) != 0;
        const uint32_t units = (duration + 208U) / 417U;
        if (units == 0 || units > (high ? 3U : 80U)) {
          bits = 0;
          bit_count = 0;
          streaks.fill(0);
          continue;
        }
        for (uint32_t unit = 0; unit < units; unit++) {
          if (bit(high))
            return true;
        }
      }
    }
    // RMT may omit only the last low half of the final Manchester symbol.
    return bit(false);
  }
};

// Task-owned history, never touched by DMA/ISR. Keep whole measured words;
// acquisition boundaries remain separate, including any unobserved gaps.
struct RawOokHistory {
  static constexpr uint32_t TARGET_US = 75000;
  static constexpr size_t MAX_SEGMENTS = 32;
  struct Segment : RawOokMetadata {
    size_t source_offset{0};
  };
  std::array<uint32_t, RawOokWindow::CAPACITY> words{};
  std::array<Segment, MAX_SEGMENTS> segments{};
  size_t count{0};
  size_t segment_count{0};
  uint32_t duration_us{0};
  bool limited{false};

  static uint32_t duration(uint32_t word) {
    return (word & 0x7FFFU) + ((word >> 16U) & 0x7FFFU);
  }

  void drop_front(size_t length) {
    for (size_t index = 0; index < length; index++)
      duration_us -= duration(words[index]);
    for (size_t index = length; index < count; index++)
      words[index - length] = words[index];
    count -= length;
    while (segment_count && length >= segments[0].count) {
      length -= segments[0].count;
      for (size_t index = 1; index < segment_count; index++)
        segments[index - 1] = segments[index];
      segment_count--;
    }
    if (segment_count) {
      segments[0].count -= length;
      segments[0].source_offset += length;
    }
  }

  void append(const RawOokWindow& window) {
    if (!window.count)
      return;
    size_t start = window.count;
    uint32_t added_us = 0;
    while (start && added_us < TARGET_US)
      added_us += duration(window.words[--start]);
    // Bound storage even when unfiltered noise contains very short runs.
    const size_t added = window.count - start;
    bool storage_limited = count + added > words.size();
    size_t remove = 0;
    uint32_t remaining_us = duration_us;
    while (remove < count &&
           (count - remove + added > words.size() || remaining_us + added_us - duration(words[remove]) >= TARGET_US)) {
      remaining_us -= duration(words[remove++]);
    }
    if (segment_count == MAX_SEGMENTS && remove < segments[0].count) {
      storage_limited = true;
      remove = segments[0].count;
    }
    drop_front(remove);
    auto& segment = segments[segment_count++];
    static_cast<RawOokMetadata&>(segment) = window;
    segment.source_offset = start;
    segment.count = added;
    for (size_t index = start; index < window.count; index++)
      words[count++] = window.words[index];
    duration_us += added_us;
    limited = duration_us < TARGET_US && (limited || storage_limited);
  }
};

enum class RawCaptureState : uint8_t { ACQUIRING, SEALED, EXPORTING, PAUSED, FAILED };

struct RawCaptureLimits {
  uint16_t max_exports{32};
  uint16_t max_rejected{4};
  uint32_t session_ms{30000};
  uint32_t filter_ns{0};
  bool capture_rejected{false};
};

} // namespace proflame2_tembed
} // namespace esphome

#endif
