export interface NavItem {
  title: string;
  href: string;
  disabled?: boolean;
  external?: boolean;
}

export const siteConfig = {
  name: "FirmLoop",
  description: "Prompt to firmware, in a loop.",
  url: process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000",
  mainNav: [
    {
      title: "Sessions",
      href: "/",
    },
    {
      title: "Hardware",
      href: "/hardware",
    },
    {
      title: "Settings",
      href: "/settings",
    },
  ] as NavItem[],
};
