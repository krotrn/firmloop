"use client";

import { Loader2Icon } from "lucide-react";

import { CodeTab } from "@/components/code/code-tab";
import { DevicePanel } from "@/components/device/device-panel";
import { EmptyState } from "@/components/feedback/empty-state";
import { AttemptsList } from "@/components/run/attempts-list";
import { ChatWaitStep } from "@/components/run/chat-wait-step";
import { RunLog } from "@/components/run/run-log";
import { RunTimeline } from "@/components/run/run-timeline";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { TestsTab } from "@/components/tests/tests-tab";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VersionsTab } from "@/components/versions/versions-tab";
import { runLabel } from "@/components/workspace/run-history";
import { useCancelRun } from "@/hooks/queries/use-runs";
import { useChatWait } from "@/hooks/use-chat-wait";
import { useRunLog } from "@/hooks/use-run-log";
import { isTerminal } from "@/lib/run-status";
import { relativeTime } from "@/lib/time";
import type { DeviceStatus } from "@/services/device.service";
import type { Run } from "@/services/runs.service";

type DeviceMode = DeviceStatus["mode"];

function When({ label, iso }: { label: string; iso: string }) {
  return (
    <div className="flex flex-wrap gap-x-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>
        <time dateTime={iso} title={new Date(iso).toLocaleString()}>
          {relativeTime(iso)}
        </time>
      </dd>
    </div>
  );
}

function RunSummary({ run, sessionId }: { run: Run; sessionId: string }) {
  const cancel = useCancelRun(sessionId);
  const version = run.result_version?.number;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {version != null && (
            <span className="text-muted-foreground text-sm tabular-nums">
              v{version}
            </span>
          )}
        </div>
        {!isTerminal(run.status) && (
          <Button
            variant="outline"
            onClick={() => cancel.mutate(run.id)}
            disabled={cancel.isPending}
          >
            {cancel.isPending && (
              <Loader2Icon className="animate-spin" data-icon="inline-start" />
            )}
            Cancel
          </Button>
        )}
      </div>
      {cancel.isError && (
        <p role="alert" className="text-destructive text-sm">
          {cancel.error.message}
        </p>
      )}
      <p className="text-sm break-words whitespace-pre-wrap">{runLabel(run)}</p>
      {run.failure && (
        <p role="alert" className="text-destructive text-sm break-words">
          {run.failure.message}
        </p>
      )}
      <dl className="flex flex-col gap-1 text-sm">
        <When label="Started" iso={run.started_at} />
        {run.finished_at && <When label="Finished" iso={run.finished_at} />}
      </dl>
    </div>
  );
}

export function RunPanel({
  run,
  sessionId,
  deviceMode,
}: {
  run: Run | null;
  sessionId: string;
  deviceMode: DeviceMode | null;
}) {
  const logLines = useRunLog(sessionId, run?.id ?? null);
  const chatWait = useChatWait(sessionId, run?.id ?? null);
  return (
    <Tabs defaultValue="run" className="min-w-0">
      <TabsList className="w-full justify-start overflow-x-auto overflow-y-hidden">
        <TabsTrigger value="run">Run</TabsTrigger>
        <TabsTrigger value="code">Code</TabsTrigger>
        <TabsTrigger value="tests">Tests</TabsTrigger>
        <TabsTrigger value="versions">Versions</TabsTrigger>
        <TabsTrigger value="device">Device</TabsTrigger>
      </TabsList>
      <TabsContent value="run" className="pt-4">
        {run ? (
          <div className="flex flex-col gap-8">
            <RunSummary run={run} sessionId={sessionId} />
            <RunTimeline run={run} deviceMode={deviceMode} />
            <ChatWaitStep state={chatWait} runStatus={run.status} />
            <AttemptsList run={run} />
            <RunLog key={run.id} lines={logLines} />
          </div>
        ) : (
          <EmptyState
            title="No run selected"
            description="Send a request to start a run."
          />
        )}
      </TabsContent>
      <TabsContent value="code" className="pt-4">
        {run ? (
          <CodeTab key={run.id} run={run} sessionId={sessionId} />
        ) : (
          <EmptyState
            title="No code yet"
            description="Code appears here once a run writes it."
          />
        )}
      </TabsContent>
      <TabsContent value="tests" className="pt-4">
        {run ? (
          <TestsTab run={run} sessionId={sessionId} />
        ) : (
          <EmptyState
            title="No tests yet"
            description="Tests appear here once a run writes them."
          />
        )}
      </TabsContent>
      <TabsContent value="versions" className="pt-4">
        <VersionsTab sessionId={sessionId} />
      </TabsContent>
      <TabsContent value="device" className="pt-4">
        {deviceMode ? (
          <DevicePanel mode={deviceMode} />
        ) : (
          <EmptyState
            title="No device details"
            description="Board details appear here once they are available."
          />
        )}
      </TabsContent>
    </Tabs>
  );
}
