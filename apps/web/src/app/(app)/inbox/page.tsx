import { Inbox } from "lucide-react";

import { AutoRefresh } from "@/components/auto-refresh";
import { Card, Empty, PageHeader } from "@/components/ui";
import { TaskList } from "@/features/inbox/task-list";
import { apiGet } from "@/lib/api";
import type { TaskOut } from "@/lib/types";

export default async function InboxPage() {
  const tasks = await apiGet<TaskOut[]>("/tasks");
  return (
    <div className="mx-auto max-w-5xl">
      <AutoRefresh active ms={5000} />
      <PageHeader title="Inbox" sub="Work the engine routed to a human: material issues, low-confidence extractions and failed steps." />
      <Card className="overflow-hidden">
        {tasks.length === 0 ? (
          <Empty icon={<Inbox className="size-8" />} title="Inbox zero">
            Clean documents complete on their own. Anything that needs judgement shows up here.
          </Empty>
        ) : (
          <TaskList tasks={tasks} />
        )}
      </Card>
    </div>
  );
}
