"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { useMe } from "@/hooks/use-me";
import { ApiError } from "@/lib/api";

export default function AppIndex() {
  const router = useRouter();
  const { data, error } = useMe();

  useEffect(() => {
    if (data) router.replace(data.shops[0] ? `/app/${data.shops[0].id}` : "/onboarding");
    if (error instanceof ApiError && error.status === 401) router.replace("/login");
  }, [data, error, router]);

  return <FullPageSpinner label="Loading your stores" />;
}
