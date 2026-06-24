/**
 * Lemma WebSocket concurrent-subscriber load test.
 *
 * Measures how many simultaneous WebSocket connections a resource-constrained
 * server (target: 2 CPU / 2 GB RAM) can sustain, and how event delivery
 * latency degrades as subscriber count grows.
 *
 * Two concurrent scenarios:
 *
 *   subscribers  — ramp from 0 → MAX_SUBSCRIBERS VUs, each holding one open
 *                  WebSocket connection. Measures connection establishment time
 *                  and event-receive latency.
 *
 *   writer       — a fixed pool of VUs that continuously insert records into
 *                  the pod, generating events that subscribers receive. Without
 *                  a writer, subscribers are idle and latency cannot be measured.
 *
 * Required env vars (written by load_tests/setup.py):
 *   LEMMA_API_URL    — HTTP base URL, e.g. http://localhost:8000
 *   LEMMA_WS_URL     — WS base URL,   e.g. ws://localhost:8000
 *   LEMMA_TOKEN      — SuperTokens access token
 *   LEMMA_POD_ID     — UUID of the pre-created pod
 *   LEMMA_TABLE_NAME — Name of the pre-created table
 *
 * Run:
 *   docker run --rm --network host \
 *     --env-file load_tests/.env.load_test \
 *     -v ./load_tests:/scripts \
 *     grafana/k6:latest run /scripts/ws_concurrent.js
 *
 * Override capacity ceiling:
 *   docker run ... -e MAX_SUBSCRIBERS=200 ...
 */

import { check, sleep } from "k6";
import http from "k6/http";
import ws from "k6/ws";
import { Counter, Gauge, Rate, Trend } from "k6/metrics";

// --------------------------------------------------------------------------
// Config
// --------------------------------------------------------------------------

const API_URL = __ENV.LEMMA_API_URL    || "http://localhost:8000";
const WS_URL  = __ENV.LEMMA_WS_URL    || "ws://localhost:8000";
const TOKEN   = __ENV.LEMMA_TOKEN     || "";
const POD_ID  = __ENV.LEMMA_POD_ID    || "";
const TABLE   = __ENV.LEMMA_TABLE_NAME || "load_test_events";

const MAX_SUBSCRIBERS = parseInt(__ENV.MAX_SUBSCRIBERS || "100", 10);
const WRITER_VUS      = parseInt(__ENV.WRITER_VUS      || "2",   10);

if (!TOKEN || !POD_ID) {
  throw new Error(
    "LEMMA_TOKEN and LEMMA_POD_ID must be set. Run load_tests/setup.py first."
  );
}

// --------------------------------------------------------------------------
// Custom metrics
// --------------------------------------------------------------------------

/** Time (ms) from ws.connect() call to receiving the 'ready' frame. */
const wsConnectDuration = new Trend("ws_connect_duration_ms", true);

/**
 * End-to-end event delivery latency (ms): time between the record being
 * created on the server (occurred_at in the event frame) and the subscriber
 * receiving the frame.  Clock skew on the same host is sub-millisecond so
 * this is a reliable signal.
 */
const wsEventLatency = new Trend("ws_event_latency_ms", true);

/** Total WS frames successfully received. */
const wsEventsReceived = new Counter("ws_events_received");

/** WS sessions that failed to connect or closed with an error. */
const wsErrors = new Counter("ws_errors");

/** Point-in-time count of VUs with a live WebSocket connection. */
const wsLiveConnections = new Gauge("ws_live_connections");

/** HTTP record-write success rate. */
const writeSuccessRate = new Rate("write_success_rate");

// --------------------------------------------------------------------------
// Scenario configuration
// --------------------------------------------------------------------------

// Total test wall-clock: ramp (1m) + half (2m) + peak ramp (1m) + sustained
// (3m) + ramp-down (30s) = ~7m30s.  Writer starts 5 s late and runs until
// subscribers finish so events keep flowing throughout.
const TOTAL_DURATION = "7m30s";

export const options = {
  scenarios: {
    subscribers: {
      executor: "ramping-vus",
      exec: "subscriberVU",
      startVUs: 0,
      stages: [
        { duration: "1m",  target: Math.round(MAX_SUBSCRIBERS * 0.25) },
        { duration: "2m",  target: Math.round(MAX_SUBSCRIBERS * 0.50) },
        { duration: "1m",  target: MAX_SUBSCRIBERS },
        { duration: "3m",  target: MAX_SUBSCRIBERS },
        { duration: "30s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },

    writer: {
      executor: "constant-vus",
      exec: "writerVU",
      vus: WRITER_VUS,
      duration: TOTAL_DURATION,
      startTime: "5s",
    },
  },

  thresholds: {
    // Fewer than 5 WS errors expected at any sustainable load.
    ws_errors: ["count<5"],
    // P95 connection setup under 5 s (server may queue under very high load).
    ws_connect_duration_ms: ["p(95)<5000"],
    // P95 event latency under 5 s (Redis poll ceiling is ~1.1 s; higher values
    // indicate back-pressure or event drops under subscriber saturation).
    ws_event_latency_ms: ["p(95)<5000"],
    // The writer must succeed for the latency signal to be meaningful.
    write_success_rate: ["rate>0.95"],
  },
};

// --------------------------------------------------------------------------
// Subscriber VU — one WebSocket connection per VU
// --------------------------------------------------------------------------

export function subscriberVU() {
  const endpoint =
    `${WS_URL}/pods/${POD_ID}/datastore/changes` +
    `?access_token=${encodeURIComponent(TOKEN)}`;

  const connectStart = Date.now();
  let connected = false;

  const res = ws.connect(endpoint, {}, (socket) => {
    socket.on("open", () => {
      connected = true;
      wsLiveConnections.add(1);
    });

    socket.on("message", (raw) => {
      let frame;
      try {
        frame = JSON.parse(raw);
      } catch (_) {
        return;
      }

      if (frame.type === "ready") {
        wsConnectDuration.add(Date.now() - connectStart);
        return;
      }

      if (frame.type && frame.type.startsWith("datastore.record.") && frame.occurred_at) {
        const serverMs = new Date(frame.occurred_at).getTime();
        const latencyMs = Date.now() - serverMs;
        if (latencyMs >= 0) {
          wsEventLatency.add(latencyMs);
        }
        wsEventsReceived.add(1);
      }
    });

    socket.on("close", () => {
      if (connected) {
        wsLiveConnections.add(-1);
        connected = false;
      }
    });

    socket.on("error", () => {
      wsErrors.add(1);
      if (connected) {
        wsLiveConnections.add(-1);
        connected = false;
      }
      socket.close();
    });

    // Hold the connection for the full scenario duration.
    // k6 cancels the iteration when the scenario ramps down.
    socket.setTimeout(() => socket.close(), 8 * 60 * 1000);
  });

  check(res, { "WS handshake 101": (r) => r && r.status === 101 });
  if (!res || res.status !== 101) {
    wsErrors.add(1);
  }
}

// --------------------------------------------------------------------------
// Writer VU — continuously inserts records to drive event throughput
// --------------------------------------------------------------------------

export function writerVU() {
  let seq = 0;

  // k6 stops the iteration when the scenario's duration expires.
  for (;;) {
    seq++;
    const t0 = Date.now();

    const resp = http.post(
      `${API_URL}/pods/${POD_ID}/datastore/tables/${TABLE}/records`,
      JSON.stringify({ data: { body: "load-test-event", seq } }),
      {
        headers: {
          Authorization: `Bearer ${TOKEN}`,
          "Content-Type": "application/json",
        },
        timeout: "10s",
      }
    );

    const ok = resp.status === 201;
    writeSuccessRate.add(ok);

    if (!ok) {
      console.warn(`[writer] HTTP ${resp.status}: ${resp.body.slice(0, 120)}`);
      sleep(1);
    } else {
      // ~500 ms between writes per writer VU → ~2 events/s per VU.
      const wait = 0.5 - (Date.now() - t0) / 1000;
      if (wait > 0) sleep(wait);
    }
  }
}
