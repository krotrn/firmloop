import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type TestCase = components["schemas"]["TestCase"];
export type TestResult = components["schemas"]["TestResult"];

export async function listRunTests(runId: string): Promise<TestCase[]> {
  const list = await fetcher<components["schemas"]["TestCaseList"]>(
    `${env.NEXT_PUBLIC_API_URL}/api/runs/${encodeURIComponent(runId)}/tests`
  );
  return list.items;
}
