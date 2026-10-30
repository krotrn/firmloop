"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { HealthStatus } from "@/components/feedback/health-status";
import { siteConfig } from "@/config/site";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") {
    return pathname === "/" || pathname.startsWith("/sessions");
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Header() {
  const pathname = usePathname();
  return (
    <header className="border-border/40 bg-background/95 supports-[backdrop-filter]:bg-background/60 sticky top-0 z-50 w-full border-b backdrop-blur">
      <div className="container mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 px-4 sm:px-8">
        <Link
          href="/"
          aria-label={siteConfig.name}
          className="flex items-center gap-2 text-lg font-bold"
        >
          <span className="bg-primary text-primary-foreground flex size-8 items-center justify-center rounded-lg font-extrabold shadow-sm">
            FL
          </span>
          <span aria-hidden="true" className="hidden sm:inline">
            {siteConfig.name}
          </span>
        </Link>
        <nav
          aria-label="Main"
          className="mr-auto ml-2 flex min-w-0 flex-wrap items-center gap-3 gap-y-1 sm:ml-6 sm:gap-4"
        >
          {siteConfig.mainNav.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "hover:text-foreground text-sm font-medium transition-colors",
                  active ? "text-foreground" : "text-muted-foreground"
                )}
              >
                {item.title}
              </Link>
            );
          })}
        </nav>
        <div className="min-w-0 shrink">
          <HealthStatus />
        </div>
      </div>
    </header>
  );
}
