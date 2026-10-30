import { SessionWorkspace } from "@/components/workspace/session-workspace";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SessionWorkspace key={id} id={id} />;
}
