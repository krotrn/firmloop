"use client";

import * as React from "react";
import { CancelWaitingProvider } from "./cancel-waiting-provider";
import { EventsProvider } from "./events-provider";
import { QueryProvider } from "./query-provider";

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  return (
    <QueryProvider>
      <EventsProvider>
        <CancelWaitingProvider>{children}</CancelWaitingProvider>
      </EventsProvider>
    </QueryProvider>
  );
}
