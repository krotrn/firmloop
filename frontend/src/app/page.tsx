import { HealthStatus } from "@/components/feedback/health-status";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { siteConfig } from "@/config/site";

export default function Home() {
  return (
    <div className="flex items-center justify-center p-8">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{siteConfig.name}</CardTitle>
          <CardDescription>{siteConfig.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <HealthStatus />
        </CardContent>
      </Card>
    </div>
  );
}
