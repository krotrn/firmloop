type Listener = (event: { data?: string }) => void;

/** Minimal stand-in for the browser WebSocket; the test drives open, message and close. */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  static reset() {
    FakeWebSocket.instances = [];
  }

  static get last(): FakeWebSocket {
    const socket = FakeWebSocket.instances.at(-1);
    if (!socket) throw new Error("no WebSocket was created");
    return socket;
  }

  url: string;
  sent: string[] = [];
  closed = false;
  onopen: Listener | null = null;
  onmessage: Listener | null = null;
  onclose: Listener | null = null;
  onerror: Listener | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  /** Messages the client sent, parsed. */
  get sentMessages(): Record<string, unknown>[] {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.({});
  }

  receive(message: unknown) {
    this.onmessage?.({
      data: typeof message === "string" ? message : JSON.stringify(message),
    });
  }

  /** The connection drops (or fails to open). */
  drop() {
    this.onclose?.({});
  }
}
