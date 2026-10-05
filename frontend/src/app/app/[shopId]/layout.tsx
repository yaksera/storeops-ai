import { AppShell } from "@/components/app-shell";

export default async function ShopLayout({ children, params }: LayoutProps<"/app/[shopId]">) {
  const { shopId } = await params;
  return <AppShell shopId={shopId}>{children}</AppShell>;
}
