"use client";

import { useEffect, useRef } from "react";

/**
 * Lightweight subscription to a shop's live stream for screens that only need to know *that*
 * something changed (they refetch their own data). Live Ops uses the richer `useLiveOps`.
 */
export function useShopEvents(shopId: string, types: readonly string[], onEvent: (type: string) => void) {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });
  const key = types.join(",");

  useEffect(() => {
    const source = new EventSource(`/api/shops/${shopId}/events/stream`);
    for (const type of key.split(",")) {
      source.addEventListener(type, () => handler.current(type));
    }
    return () => source.close();
  }, [shopId, key]);
}
