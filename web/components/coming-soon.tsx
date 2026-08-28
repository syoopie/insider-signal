import { Hammer } from "lucide-react";
import { EmptyState } from "@/components/empty-state";

export function ComingSoon({ note }: { note: string }) {
  return (
    <EmptyState
      icon={Hammer}
      title="Landing in a later phase of the migration"
      description={note}
    />
  );
}
