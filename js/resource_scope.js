/** Latest resource operations belong to a live editor/node scope, not a node ID. */
export class ResourceScope {
  /** @param {() => boolean} alive */
  constructor(alive = () => true) { this.alive = alive; this.operations = new Map(); this.disposed = false; }
  /** @param {string} channel */
  start(channel) {
    this.cancel(channel);
    const controller = new AbortController();
    if(this.disposed||!this.alive())controller.abort();
    this.operations.set(channel, controller);
    return {
      signal: controller.signal,
      current: () => !this.disposed && !controller.signal.aborted && this.alive()
        && this.operations.get(channel) === controller,
    };
  }
  /** @param {string} channel */
  cancel(channel) { this.operations.get(channel)?.abort(); this.operations.delete(channel); }
  invalidate() { for (const key of this.operations.keys()) this.cancel(key); }
  dispose() { this.invalidate(); this.disposed = true; }
}
