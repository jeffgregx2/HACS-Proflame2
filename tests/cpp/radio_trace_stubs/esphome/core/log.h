#pragma once
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <string>
#include <vector>
inline std::vector<std::string> trace_logs;
inline void trace_test_log(const char*, const char* format, ...) __attribute__((format(printf, 2, 3)));
inline void trace_test_log(const char*, const char* format, ...) {
  char buffer[2048];
  va_list args;
  va_start(args, format);
  const int length = vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  assert(length >= 0 && static_cast<size_t>(length) < sizeof(buffer));
  trace_logs.emplace_back(buffer);
}
#define ESP_LOGI(...) trace_test_log(__VA_ARGS__)
#define ESP_LOGCONFIG(...) trace_test_log(__VA_ARGS__)
#define ESP_LOGW(...) trace_test_log(__VA_ARGS__)
#define ESP_LOGE(...) trace_test_log(__VA_ARGS__)
#define YESNO(value) ((value) ? "YES" : "NO")
