#include "delayed_tx_retry.h"
#include <array>
#include <cassert>

using namespace esphome::proflame2_tembed;

struct Command {
  uint32_t generation{0};
  std::array<uint8_t, 64> payload{};
  uint32_t bits{0};
  uint8_t repeats{5};
};

int main() {
  DelayedTxRetry<Command> retry;
  Command command, output;
  assert(!retry.enabled() && !retry.pending());
  command.generation = retry.next_generation();
  retry.accepted(command);
  assert(!retry.complete(command.generation, true, false, 100));
  assert(!retry.take_due(99999, output));
  retry.set_enabled(true);
  assert(!retry.pending()); // Enabling never replays an earlier command.

  for (uint32_t bits : {182U, 260U}) {
    command.bits = bits;
    command.payload.fill(0xAB);
    command.generation = retry.next_generation();
    retry.accepted(command);
    const auto token = command.generation;
    assert(!retry.complete(token, true, true, 0));
    command.payload.fill(0);                 // Scheduler owns its copy, not caller memory.
    assert(!retry.take_due(100000, output)); // Must wait for worker completion.
    assert(retry.complete(token, true, false, 400));
    assert(!retry.take_due(2399, output));
    assert(retry.take_due(2400, output));
    assert(output.bits == bits && output.repeats == 5 && output.payload[0] == 0xAB);
    assert(!retry.take_due(10000, output));
    assert(!retry.complete(token, true, true, 2800)); // Never a retry chain.
    assert(!retry.take_due(10000, output));
  }

  command.generation = retry.next_generation();
  retry.accepted(command);
  const auto stale_token = command.generation;
  assert(retry.complete(stale_token, true, false, 0));
  command.generation = retry.next_generation();
  command.bits = 260;
  retry.accepted(command); // New queue acceptance cancels old burst immediately.
  assert(!retry.take_due(2000, output));
  assert(!retry.complete(stale_token, true, false, 0));
  assert(retry.complete(command.generation, true, false, 500));
  assert(!retry.take_due(2499, output));
  assert(retry.take_due(2500, output) && output.bits == 260);

  command.generation = retry.next_generation();
  retry.accepted(command);
  retry.cancel(); // Accepted native/HA state while TX is still in flight.
  assert(!retry.complete(command.generation, true, false, 100));
  assert(!retry.take_due(10000, output));
  command.generation = retry.next_generation();
  retry.accepted(command);
  assert(retry.complete(command.generation, true, false, 100));
  retry.cancel(); // Same cancellation after completion, during the two seconds.
  assert(!retry.take_due(10000, output));

  command.generation = retry.next_generation();
  retry.accepted(command);
  retry.set_enabled(false);
  retry.set_enabled(true);
  assert(!retry.complete(command.generation, true, false, 0));
  command.generation = retry.next_generation();
  retry.accepted(command);
  assert(!retry.complete(command.generation, false, false, 0));
  assert(!retry.take_due(10000, output));

  command.generation = retry.next_generation();
  retry.accepted(command);
  assert(retry.complete(command.generation, true, false, UINT32_MAX - 999));
  assert(!retry.take_due(999, output));
  assert(retry.take_due(1000, output)); // millis() rollover.
}
