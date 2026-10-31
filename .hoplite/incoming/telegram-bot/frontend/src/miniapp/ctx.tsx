import { createContext, useContext } from "react";
import type { Me } from "../shared/types";

export interface Ctx {
  me: Me | null;
  isAdmin: boolean;
  reloadMe: () => Promise<void>;
  toast: (msg: string, kind?: "ok" | "err") => void;
  goTab: (t: Tab) => void;
  open: (v: View) => void;
}
export type Tab = "home" | "devices" | "plans" | "bonus" | "profile";
export type View =
  | { type: "device"; id: number } | { type: "order"; id: number }
  | { type: "support" } | { type: "settings" } | { type: "tasks" } | { type: "support_new" } | { type: "ticket"; id: number };

export const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);
