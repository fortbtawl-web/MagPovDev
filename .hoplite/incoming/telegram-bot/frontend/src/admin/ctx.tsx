import { createContext, useContext } from "react";
import type { AdminMe } from "../shared/types";

export interface Field {
  name: string; label: string; type?: "text" | "number" | "datetime-local" | "select" | "checkbox" | "textarea";
  value?: string; options?: { value: string; label: string }[]; required?: boolean; hint?: string; readOnly?: boolean;
}
export interface FormOpts { reasonOptional?: boolean }
export type FormResult = Record<string, string>;
export interface AdminCtx {
  /** Модальная форма: содержит поле «Причина» (reason), обязательное, если не задан reasonOptional. null = отмена. */
  form: (title: string, fields?: Field[], opts?: FormOpts) => Promise<FormResult | null>;
  notify: (msg: string, kind?: "ok" | "err") => void;
  go: (page: string, param?: string | number) => void;
  me: AdminMe | null;
  isOwner: boolean;
}
export const Ctx = createContext<AdminCtx>(null as unknown as AdminCtx);
export const useAdmin = () => useContext(Ctx);
