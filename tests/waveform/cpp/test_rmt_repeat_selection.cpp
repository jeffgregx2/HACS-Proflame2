// Host adapter around the actual selector, word reader, and RMT conversion.
// Input fixtures are supplied by Python, including private local replay only.
#include <algorithm>
#include <array>
#include <iostream>
#include <string>
#include <vector>

#include "rmt_repeat_selection.h"
#define private public
#include "rmt_ook_receiver.h"
#undef private

using namespace esphome::proflame2_tembed;

RmtOokCapture capture_from_bits(const std::string& bits, uint16_t index) {
  RmtOokCapture capture{};
  capture.pcm_bit_count = static_cast<uint16_t>(bits.size());
  capture.transition_count = index + 10;
  capture.first_high_us = index + 100;
  capture.first_low_us = index + 200;
  for (size_t i = 0; i < std::min(bits.size(), RmtOokCapture::MAX_PCM_BITS); i++) {
    if (bits[i] == '1') {
      capture.pcm_bytes[i / 8] |= static_cast<uint8_t>(1U << (7U - i % 8));
    }
  }
  return capture;
}

void output(RmtRepeatSelection& selection, RmtOokCapture& capture, bool converted) {
  const auto result = converted ? selection.finish(capture) : RmtSelectionResult{};
  const auto second = converted ? selection.finish(capture) : RmtSelectionResult{};
  if (second.replaced || second.skipped_malformed) {
    std::exit(2);
  }
  std::cout << converted << ' ' << result.replaced << ' ' << result.skipped_malformed << ' ' << result.agreeing_repeats
            << ' ' << selection.format_name() << ' ' << capture.symbol_count << ' ' << capture.transition_count << ' '
            << capture.first_high_us << ' ' << capture.first_low_us << ' ';
  if (capture.pcm_bit_count == 0) {
    std::cout << '-';
  }
  for (uint16_t i = 0; i < capture.pcm_bit_count && i < RmtOokCapture::MAX_PCM_BITS; i++) {
    std::cout << ((capture.pcm_bytes[i / 8] >> (7 - i % 8)) & 1);
  }
  std::cout << '\n';
}

int main(int argc, char** argv) {
  static_assert(sizeof(RmtRepeatSelection) <= 256, "Keep selection storage bounded on the polling task stack");
  const std::string command = argc > 1 ? argv[1] : "select";
  if (command == "words") {
    unsigned offset, count;
    std::string bits;
    while (std::cin >> offset >> count >> bits) {
      auto capture = capture_from_bits(bits == "-" ? "" : bits, 0);
      RmtFrameWords words{};
      const bool parsed = rmt_read_frame_words(capture, offset, count, words);
      std::cout << parsed;
      if (parsed) {
        for (unsigned i = 0; i < count; i++) {
          std::cout << ' ' << static_cast<unsigned>(words[i]);
        }
      }
      std::cout << '\n';
    }
    return 0;
  }
  if (command == "counter") {
    uint32_t total, delta;
    while (std::cin >> total >> delta) {
      std::cout << rmt_add_skipped_count(total, delta) << '\n';
    }
    return 0;
  }
  unsigned active, learning, enabled, serial, c1, d1, c2, d2;
  size_t count;
  while (std::cin >> active >> learning >> enabled >> serial >> c1 >> d1 >> c2 >> d2 >> count) {
    const Proflame2DecodeProfile profile{enabled != 0,
                                         serial,
                                         static_cast<uint8_t>(c1),
                                         static_cast<uint8_t>(d1),
                                         static_cast<uint8_t>(c2),
                                         static_cast<uint8_t>(d2)};
    RmtRepeatSelection selection(active != 0, learning != 0, profile);
    RmtOokCapture capture{};
    if (command == "select") {
      for (size_t i = 0; i < count; i++) {
        std::string bits;
        std::cin >> bits;
        const auto segment = capture_from_bits(bits == "-" ? "" : bits, i);
        selection.observe(segment);
        if (segment.pcm_bit_count > capture.pcm_bit_count) {
          capture = segment;
        }
      }
      capture.symbol_count = 250;
      output(selection, capture, true);
      continue;
    }
    std::vector<rmt_symbol_word_t> symbols;
    for (size_t i = 0; i < count; i++) {
      uint32_t word;
      std::cin >> word;
      symbols.push_back({word & 0x7FFFU, (word >> 15U) & 1U, (word >> 16U) & 0x7FFFU, (word >> 31U) & 1U});
    }
    std::string error;
    RmtOokReceiver receiver;
    bool converted;
    if (command == "convert") {
      converted =
          receiver.convert_symbols_(symbols.data(), count, capture, error,
                                    selection.enabled() ? &RmtRepeatSelection::observe_segment : nullptr, &selection);
    } else {
      // Exercise callback -> queue -> poll -> rearm before applying selection.
      receive_calls = 0;
      fail_rearm = false;
      if (!receiver.begin(3, error)) {
        return 3;
      }
      if (count <= receiver.SYMBOL_CAPACITY) {
        std::copy(symbols.begin(), symbols.end(), receiver.symbol_buffer_());
      }
      rmt_rx_done_event_data_t event{receiver.symbol_buffer_(), count, {true}};
      receiver.on_receive_done_(nullptr, &event, receiver.queue_);
      fail_rearm = command == "rearm-failure";
      converted = receiver.poll(capture, error, selection.enabled() ? &RmtRepeatSelection::observe_segment : nullptr,
                                &selection);
      if (converted && receive_calls != 2) {
        return 4;
      }
      receiver.end();
    }
    output(selection, capture, converted);
  }
  return 0;
}
