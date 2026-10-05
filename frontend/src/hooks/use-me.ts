"use client";

import { useQuery } from "@tanstack/react-query";

import { api, type Me } from "@/lib/api";

export const meQueryKey = ["me"] as const;

export function useMe() {
  return useQuery({ queryKey: meQueryKey, queryFn: () => api<Me>("/api/auth/me") });
}
