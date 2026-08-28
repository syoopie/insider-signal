"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

const ORDER = ["light", "dark", "system"] as const;
const ICON = { light: Sun, dark: Moon, system: Monitor } as const;
const LABEL = { light: "Light", dark: "Dark", system: "System" } as const;

export function ModeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  // next-themes can't know the resolved theme until the client mounts; this one
  // effect flips a flag so the first paint matches the server ("system").
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMounted(true), []);

  // Render a stable placeholder until mounted so server and client markup agree.
  const current = mounted ? ((theme ?? "system") as (typeof ORDER)[number]) : "system";
  const Icon = ICON[current] ?? Monitor;

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Theme: ${LABEL[current]}. Click to change.`}
      title={`Theme: ${LABEL[current]}`}
      onClick={() => {
        const idx = ORDER.indexOf(current);
        setTheme(ORDER[(idx + 1) % ORDER.length]);
      }}
    >
      <Icon className="size-4" />
    </Button>
  );
}
