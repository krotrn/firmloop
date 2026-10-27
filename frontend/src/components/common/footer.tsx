import { siteConfig } from "@/config/site";

export function Footer() {
  return (
    <footer className="border-border/40 bg-muted/30 border-t py-8">
      <div className="text-muted-foreground container mx-auto max-w-7xl px-4 text-center text-sm sm:px-8">
        &copy; {new Date().getFullYear()} {siteConfig.name}
      </div>
    </footer>
  );
}
