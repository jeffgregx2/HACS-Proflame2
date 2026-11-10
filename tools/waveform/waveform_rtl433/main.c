// Offline adapter to rtl_433's detector, not a new RF demodulator.
// SPDX-License-Identifier: GPL-3.0-or-later
#include "baseband.h"
#include "pulse_detect.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define CHUNK_SAMPLES 16384
static uint8_t iq[CHUNK_SAMPLES * 2];
static uint16_t envelope[CHUNK_SAMPLES];
static int16_t filtered[CHUNK_SAMPLES];
static int16_t fm[CHUNK_SAMPLES];
static pulse_data_t ook, fsk;

static void emit_event(const pulse_data_t* data, unsigned id) {
  uint64_t end = data->offset;
  printf("{\"kind\":\"event\",\"id\":%u,\"start_sample\":%" PRIu64
         ",\"pulse_count\":%u,\"capacity_reached\":%s,\"signed_samples\":[",
         id, data->offset, data->num_pulses, data->num_pulses >= PD_MAX_PULSES ? "true" : "false");
  for (unsigned index = 0; index < data->num_pulses; index++) {
    if (index)
      printf(",");
    printf("%d", data->pulse[index]);
    end += data->pulse[index];
    if (index + 1 < data->num_pulses) {
      printf(",%d", -data->gap[index]);
      end += data->gap[index];
    }
  }
  // The last gap ends at the detector's stop criterion, not a measured RF edge.
  printf("],\"end_sample\":%" PRIu64 ",\"terminal_gap_samples\":%d}\n", end, data->gap[data->num_pulses - 1]);
}

int main(int argc, char** argv) {
  if (argc < 3 || argc > 4) {
    fprintf(stderr, "Usage: waveform_rtl433 recording.cu8 sample_rate_hz [fixed_level_db]\n");
    return 2;
  }
  char* end;
  errno = 0;
  unsigned long rate = strtoul(argv[2], &end, 10);
  if (errno || *end || end == argv[2] || rate < 100000 || rate > 2400000)
    return 2;
  double level = 0;
  if (argc == 4) {
    errno = 0;
    level = strtod(argv[3], &end);
    if (errno || *end || end == argv[3] || !isfinite(level) || level < -100 || level >= 0)
      return 2;
  }
  FILE* input = fopen(argv[1], "rb");
  if (!input)
    return 2;
  pulse_detect_t* detector = pulse_detect_create();
  if (!detector) {
    fclose(input);
    return 2;
  }
  baseband_init();
  filter_state_t lowpass = {0};
  pulse_detect_set_levels(detector, 0, (float)level, -12, 9, 0);
  printf("{\"kind\":\"engine\",\"schema_version\":1,\"sample_rate_hz\":%lu,"
         "\"sample_format\":\"cu8\",\"detector\":\"rtl_433\",\"fixed_level_db\":",
         rate);
  if (argc == 4)
    printf("%.9g", level);
  else
    printf("null");
  printf("}\n");
  uint64_t offset = 0;
  unsigned events = 0;
  int result = 0;
  size_t bytes;
  while ((bytes = fread(iq, 1, sizeof(iq), input)) > 0) {
    if (bytes % 2) {
      result = 2;
      break;
    }
    unsigned samples = bytes / 2;
    envelope_detect(iq, envelope, samples);
    baseband_low_pass_filter(&lowpass, envelope, filtered, samples);
    int kind;
    while ((kind = pulse_detect_package(detector, filtered, fm, samples, (uint32_t)rate, offset, &ook, &fsk,
                                        FSK_PULSE_DETECT_OLD)) != 0) {
      if (kind == PULSE_DATA_OOK && ook.num_pulses) {
        if (events >= 4096) {
          pulse_detect_free(detector);
          fclose(input);
          return 3;
        }
        emit_event(&ook, ++events);
      }
    }
    offset += samples;
  }
  if (ferror(input))
    result = 2;
  pulse_detect_free(detector);
  fclose(input);
  if (!result)
    printf("{\"kind\":\"end\",\"samples_read\":%" PRIu64 ",\"event_count\":%u,"
           "\"eof_partial_event_possible\":true}\n",
           offset, events);
  if (fflush(stdout) || ferror(stdout))
    result = 2;
  return result;
}
