"use client";

import * as React from "react";
import { CancelWaitingProvider } from "./cancel-waiting-provider";
import { QueryProvider } from "./query-provider";

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  return (
    <QueryProvider>
      <CancelWaitingProvider>{children}</CancelWaitingProvider>
    </QueryProvider>
  );
}
