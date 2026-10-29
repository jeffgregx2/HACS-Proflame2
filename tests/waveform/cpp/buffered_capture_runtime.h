#pragma once
// Host driver/RTOS model. Actual capture/task implementation is compiled unchanged.
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "esp_err.h"

using BaseType_t = int;
constexpr int pdFALSE = 0, pdTRUE = 1, pdPASS = 1;
constexpr unsigned portMAX_DELAY = 0xffffffffU;
#define portNUM_PROCESSORS 2
#define pdMS_TO_TICKS(ms) (ms)
using portMUX_TYPE = std::recursive_mutex;
#define portMUX_INITIALIZER_UNLOCKED                                                                                   \
  {                                                                                                                    \
  }
inline void portENTER_CRITICAL(portMUX_TYPE* p) {
  p->lock();
}
inline void portEXIT_CRITICAL(portMUX_TYPE* p) {
  p->unlock();
}
inline void portENTER_CRITICAL_ISR(portMUX_TYPE* p) {
  p->lock();
}
inline void portEXIT_CRITICAL_ISR(portMUX_TYPE* p) {
  p->unlock();
}
inline std::atomic<int> fault{0}, allocations{0}, receive_calls{0};
inline std::atomic<bool> driver_enabled{false}, fail_rearm{false};
inline std::atomic<int64_t> mock_time{100000};
inline const auto epoch = std::chrono::steady_clock::now();
inline int64_t esp_timer_get_time() {
  return mock_time.load() +
         std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - epoch).count();
}
struct HostQueue {
  std::mutex mutex;
  std::condition_variable cv;
  size_t capacity, size;
  std::deque<std::vector<unsigned char>> data;
  HostQueue(size_t capacity, size_t size) : capacity(capacity), size(size) {}
};
using QueueHandle_t = HostQueue*;
using SemaphoreHandle_t = HostQueue*;
inline QueueHandle_t xQueueCreate(size_t count, size_t size) {
  return fault == 4 ? nullptr : new HostQueue(count, size);
}
inline int xQueueSend(HostQueue* q, const void* item, unsigned) {
  std::lock_guard<std::mutex> lock(q->mutex);
  if (q->data.size() == q->capacity || (fault == 31 && q->size != 0))
    return 0;
  q->data.emplace_back(q->size);
  if (q->size)
    std::memcpy(q->data.back().data(), item, q->size);
  q->cv.notify_all();
  return 1;
}
inline int xQueueReceive(HostQueue* q, void* item, unsigned timeout) {
  std::unique_lock<std::mutex> lock(q->mutex);
  if (timeout)
    q->cv.wait_for(lock, std::chrono::milliseconds(timeout), [&] { return !q->data.empty(); });
  if (q->data.empty())
    return 0;
  if (q->size)
    std::memcpy(item, q->data.front().data(), q->size);
  q->data.pop_front();
  return 1;
}
inline void vQueueDelete(HostQueue* q) {
  delete q;
}
inline SemaphoreHandle_t xSemaphoreCreateBinary() {
  return xQueueCreate(1, 0);
}
inline int xSemaphoreGive(HostQueue* q) {
  return xQueueSend(q, nullptr, 0);
}
inline int xSemaphoreTake(HostQueue* q, unsigned timeout) {
  return xQueueReceive(q, nullptr, timeout);
}
inline void vSemaphoreDelete(HostQueue* q) {
  delete q;
}
struct HostTask {
  std::thread thread;
  std::mutex mutex;
  std::condition_variable cv;
  unsigned notifications{0};
};
using TaskHandle_t = HostTask*;
inline thread_local HostTask* current_task = nullptr;
struct TaskSuspended {};
inline int xTaskCreate(void (*entry)(void*), const char*, unsigned stack, void* arg, unsigned priority,
                       TaskHandle_t* out) {
  if (fault == 30)
    return 0;
  if (stack != 6144 || priority != 6)
    return 0;
  *out = new HostTask;
  auto* task = *out;
  task->thread = std::thread([=] {
    current_task = task;
    try {
      entry(arg);
    } catch (TaskSuspended&) {
    }
  });
  return 1;
}
inline void xTaskNotifyGive(HostTask* task) {
  std::lock_guard<std::mutex> lock(task->mutex);
  task->notifications++;
  task->cv.notify_all();
}
inline void vTaskNotifyGiveFromISR(HostTask* task, int*) {
  xTaskNotifyGive(task);
}
inline unsigned ulTaskNotifyTake(int, unsigned timeout) {
  auto* task = current_task;
  std::unique_lock<std::mutex> lock(task->mutex);
  if (timeout == portMAX_DELAY)
    task->cv.wait(lock, [&] { return task->notifications != 0; });
  else
    task->cv.wait_for(lock, std::chrono::milliseconds(timeout), [&] { return task->notifications != 0; });
  unsigned result = task->notifications;
  task->notifications = 0;
  return result;
}
inline unsigned uxTaskGetStackHighWaterMark(void*) {
  return 4096;
}
inline void vTaskSuspend(void*) {
  throw TaskSuspended();
}
inline void vTaskDelete(HostTask* task) {
  task->thread.join();
  delete task;
}

using esp_err_t = int;
using gpio_num_t = int;
using rmt_channel_handle_t = void*;
constexpr int RMT_CLK_SRC_DEFAULT = 0;
struct rmt_symbol_word_t {
  unsigned duration0 : 15;
  unsigned level0 : 1;
  unsigned duration1 : 15;
  unsigned level1 : 1;
};
struct rmt_rx_done_event_data_t {
  rmt_symbol_word_t* received_symbols;
  size_t num_symbols;
  struct {
    bool is_last;
  } flags;
};
struct rmt_rx_channel_config_t {
  gpio_num_t gpio_num;
  int clk_src;
  uint32_t resolution_hz;
  size_t mem_block_symbols;
  int intr_priority;
  struct {
    bool invert_in, with_dma, allow_pd, io_loop_back;
  } flags;
};
struct rmt_rx_event_callbacks_t {
  bool (*on_recv_done)(rmt_channel_handle_t, const rmt_rx_done_event_data_t*, void*);
};
struct rmt_receive_config_t {
  uint32_t signal_range_min_ns, signal_range_max_ns;
  struct {
    bool en_partial_rx;
  } flags;
};
inline std::mutex driver_mutex;
inline bool (*done_callback)(rmt_channel_handle_t, const rmt_rx_done_event_data_t*, void*) = nullptr;
inline void* done_argument = nullptr;
inline int rmt_new_rx_channel(const rmt_rx_channel_config_t* c, void** out) {
  if (fault == 3 || !c->flags.with_dma || c->mem_block_symbols != 4096)
    return 9;
  *out = reinterpret_cast<void*>(1);
  return 0;
}
inline int rmt_rx_register_event_callbacks(void*, const rmt_rx_event_callbacks_t* cb, void* arg) {
  if (fault == 5)
    return 9;
  done_callback = cb->on_recv_done;
  done_argument = arg;
  return 0;
}
inline int rmt_enable(void*) {
  std::lock_guard<std::mutex> lock(driver_mutex);
  if (fault == 6)
    return 9;
  driver_enabled = true;
  return 0;
}
inline int rmt_disable(void*) {
  std::lock_guard<std::mutex> lock(driver_mutex);
  if (fault == 7)
    return 9;
  driver_enabled = false;
  return 0;
}
inline int rmt_del_channel(void*) {
  return fault == 8 ? 9 : 0;
}
inline int rmt_receive(void*, void*, size_t, const rmt_receive_config_t*) {
  receive_calls++;
  return fault == 9 || (fail_rearm && receive_calls > 1) ? 9 : 0;
}
