import Link from "next/link";

import { HealthStatus } from "@/components/feedback/health-status";
import { siteConfig } from "@/config/site";

export function Header() {
  return (
    <header className="border-border/40 bg-background/95 supports-[backdrop-filter]:bg-background/60 sticky top-0 z-50 w-full border-b backdrop-blur">
      <div className="container mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-8">
        <Link href="/" className="flex items-center gap-2 text-lg font-bold">
          <span className="bg-primary text-primary-foreground flex size-8 items-center justify-center rounded-lg font-extrabold shadow-sm">
            FL
          </span>
          <span>{siteConfig.name}</span>
        </Link>
        <nav className="mr-auto ml-6 flex items-center gap-4">
          {siteConfig.mainNav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-muted-foreground hover:text-foreground text-sm font-medium transition-colors"
            >
              {item.title}
            </Link>
          ))}
        </nav>
        <HealthStatus />
      </div>
    </header>
  );
}
