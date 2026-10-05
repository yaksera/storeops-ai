"use client";

import { createContext, useContext } from "react";

import type { Role, ShopSummary } from "@/lib/api";

const ShopContext = createContext<ShopSummary | null>(null);

export const ShopProvider = ShopContext.Provider;

export function useShop(): ShopSummary {
  const shop = useContext(ShopContext);
  if (!shop) throw new Error("useShop must be used inside the app shell");
  return shop;
}

const rank: Record<Role, number> = { viewer: 0, admin: 1, owner: 2 };

export function hasRole(role: Role, minimum: Role): boolean {
  return rank[role] >= rank[minimum];
}
