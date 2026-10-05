"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import {
  applyEvent,
  fromDashboard,
  LIVE_EVENT_TYPES,
  type Dashboard,
  type LiveEvent,
  type LiveState,
} from "@/lib/live";

export type ConnectionStatus = "connecting" | "live" | "reconnecting" | "polling" | "offline";

const POLL_INTERVAL_MS = 3000;
const MAX_SSE_ERRORS = 3;

/**
 * Live Ops data: a REST snapshot, then a Server-Sent Events stream resumed from the snapshot's
 * last event id (the browser re-sends `Last-Event-ID` on reconnect). If SSE keeps failing — e.g.
 * a proxy that buffers streams — it degrades to polling `/events?after=…`.
 */
export function useLiveOps(shopId: string, timeZone: string) {
  const snapshot = useQuery({
    queryKey: ["shop", shopId, "dashboard"],
    queryFn: () => api<Dashboard>(`/api/shops/${shopId}/dashboard`),
    staleTime: Infinity,
    refetchOnMount: "always",
  });
  const { refetch } = snapshot;
  // Local state is seeded from each fresh snapshot (adjusted during render, not in an effect),
  // then advanced by streamed events.
  const [live, setLive] = useState<{ source: Dashboard | null; state: LiveState | null }>({
    source: null,
    state: null,
  });
  if (snapshot.data && live.source !== snapshot.data) {
    setLive({ source: snapshot.data, state: fromDashboard(snapshot.data) });
  }
  const source = live.source;
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const lastIdRef = useRef<string>("0-0");

  const setState = useCallback((update: (s: LiveState | null) => LiveState | null) => {
    setLive((current) => ({ ...current, state: update(current.state) }));
  }, []);

  const handle = useCallback(
    (event: LiveEvent) => {
      lastIdRef.current = event.id;
      setState((current) => (current ? applyEvent(current, event, timeZone) : current));
    },
    [timeZone, setState],
  );

  useEffect(() => {
    if (!source) return;
    lastIdRef.current = source.last_event_id;
    let stream: EventSource | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let errors = 0;
    let closed = false;

    const poll = async () => {
      if (closed) return;
      try {
        const res = await api<{ events: LiveEvent[]; reset: boolean }>(
          `/api/shops/${shopId}/events?after=${encodeURIComponent(lastIdRef.current)}`,
        );
        if (res.reset) {
          void refetch();
          return;
        }
        res.events.forEach(handle);
        setStatus(navigator.onLine ? "polling" : "offline");
      } catch {
        setStatus("offline");
      }
      pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    const connect = () => {
      stream = new EventSource(
        `/api/shops/${shopId}/events/stream?since=${encodeURIComponent(lastIdRef.current)}`,
      );
      stream.onopen = () => {
        errors = 0;
        setStatus("live");
      };
      stream.onerror = () => {
        errors += 1;
        if (errors >= MAX_SSE_ERRORS || stream?.readyState === EventSource.CLOSED) {
          stream?.close();
          stream = null;
          void poll();
        } else {
          setStatus(navigator.onLine ? "reconnecting" : "offline");
        }
      };
      for (const type of LIVE_EVENT_TYPES) {
        stream.addEventListener(type, (message) => {
          const msg = message as MessageEvent<string>;
          handle({ id: msg.lastEventId, type, data: JSON.parse(msg.data), at: new Date().toISOString() });
        });
      }
      stream.addEventListener("reset", () => void refetch());
    };

    connect();
    const goOffline = () => setStatus("offline");
    const goOnline = () => {
      if (!stream && !pollTimer) connect();
    };
    window.addEventListener("offline", goOffline);
    window.addEventListener("online", goOnline);
    return () => {
      closed = true;
      stream?.close();
      if (pollTimer) clearTimeout(pollTimer);
      window.removeEventListener("offline", goOffline);
      window.removeEventListener("online", goOnline);
    };
  }, [shopId, source, handle, refetch]);

  return { snapshot, state: live.state, status, setState };
}

/** A clock that re-renders once per second, for relative times and status decay. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
