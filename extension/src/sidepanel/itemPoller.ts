export interface PollerOptions<T> {
  fetch(): Promise<T>;
  /** Called with every successful read; `stop` ends polling. */
  onResult(value: T): "continue" | "stop";
  /** Called when a read fails; `continue` keeps polling after the back-off delay. */
  onError(error: unknown): "continue" | "stop";
  /** Called once when the deadline passes; polling then stops. */
  onDeadline(): void;
  intervalMs: number;
  /** Delay before the next read after a failed one. */
  backoffMs: number;
  deadlineAt: number;
  now(): number;
  /** Reads are skipped while this is false and resume on the next tick or `nudge()`. */
  isVisible(): boolean;
}

export interface Poller {
  stop(): void;
  /** Reads now instead of waiting for the next tick, unless a read is already running. */
  nudge(): void;
}

/** Reads once straight away, then every `intervalMs` until stopped, the deadline, or `onResult` says stop. */
export function startPolling<T>(options: PollerOptions<T>): Poller {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let reading = false;

  const clear = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  const schedule = (delayMs: number) => {
    if (stopped) return;
    clear();
    timer = setTimeout(() => { void tick(); }, delayMs);
  };

  async function tick(): Promise<void> {
    timer = null;
    if (stopped || reading) return;
    if (options.now() >= options.deadlineAt) {
      stopped = true;
      options.onDeadline();
      return;
    }
    if (!options.isVisible()) {
      schedule(options.intervalMs);
      return;
    }
    reading = true;
    let nextDelay = options.intervalMs;
    try {
      const value = await options.fetch();
      if (stopped) return;
      if (options.onResult(value) === "stop") {
        stopped = true;
        return;
      }
    } catch (error) {
      if (stopped) return;
      if (options.onError(error) === "stop") {
        stopped = true;
        return;
      }
      nextDelay = options.backoffMs;
    } finally {
      reading = false;
    }
    schedule(nextDelay);
  }

  schedule(0);
  return {
    stop() {
      stopped = true;
      clear();
    },
    nudge() {
      if (stopped || reading) return;
      schedule(0);
    },
  };
}
