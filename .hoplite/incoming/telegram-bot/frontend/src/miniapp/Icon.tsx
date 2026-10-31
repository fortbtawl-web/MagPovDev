import type { ReactNode } from "react";

const P: Record<string, ReactNode> = {
  home: <><path d="M3 11l9-8 9 8" /><path d="M5 10v10h14V10" /><path d="M10 20v-6h4v6" /></>,
  device: <><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18.5h2" /></>,
  plans: <><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z" /><path d="M4 7.5l8 4.5 8-4.5M12 12v9" /></>,
  gift: <><rect x="3" y="8" width="18" height="4" rx="1" /><path d="M5 12v8h14v-8M12 8v12" /><path d="M12 8c-1.5-4-5-4-5-1.5C7 8 9 8 12 8zM12 8c1.5-4 5-4 5-1.500C17 8 15 8 12 8z" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.5-6.5 8-6.500s8 2.5 8 6.5" /></>,
  shield: <path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6l8-3z" />,
  check: <path d="M5 12.500l4.5 4.500L19 7.5" />,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2.5" /><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2" /></>,
  share: <><path d="M12 15V3M7.5 7.500L12 3l4.5 4.5" /><path d="M5 12v7a2 2 0 002 2h10a2 2 0 002-2v-7" /></>,
  link: <><path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.700l-1 1" /><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.700l1-1" /></>,
  chat: <path d="M4 5h16v11H9l-5 4V5z" />,
  admin: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.900l2.1 2.100M17 17l2.1 2.100M4.9 19.100L7 17M17 7l2.1-2.1" /></>,
  settings: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  refresh: <><path d="M20 11a8 8 0 00-14.5-4M4 5v3.500h3.5" /><path d="M4 13a8 8 0 0014.5 4M20 19v-3.500h-3.5" /></>,
  trash: <><path d="M4 7h16M10 11v6M14 11v6" /><path d="M6 7l1 13h10l1-13M9 7V4h6v3" /></>,
  doc: <><path d="M6 3h8l4 4v14H6V3z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></>,
  megaphone: <><path d="M3 10v4h4l9 5V5L7 10H3z" /><path d="M19 9a4 4 0 010 6" /></>,
  card: <><rect x="3" y="5" width="18" height="14" rx="3" /><path d="M3 10h18M7 15h3" /></>,
  ticket: <><path d="M4 8a2 2 0 002-2h12a2 2 0 002 2v2a2 2 0 000 4v2a2 2 0 00-2 2H6a2 2 0 00-2-2v-2a2 2 0 000-4V8z" /></>,
  power: <><path d="M12 3v8" /><path d="M7 6.500a8 8 0 1010 0" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9.500a2.5 2.5 0 114 2c-.9.6-1.5 1-1.5 2M12 17h.01" /></>,
  sound: <><path d="M4 10v4h4l5 4V6l-5 4H4z" /><path d="M16.5 9a4 4 0 010 6M19 6.500a8 8 0 010 11" /></>,
  mute: <><path d="M4 10v4h4l5 4V6l-5 4H4z" /><path d="M17 9.500l4 5M21 9.500l-4 5" /></>,
  sparkle: <path d="M12 3l1.8 5.200L19 10l-5.2 1.800L12 17l-1.8-5.200L5 10l5.2-1.800L12 3zM19 17l.7 1.800L21.5 19.500l-1.8.7L19 22l-.7-1.8-1.8-.7 1.8-.7L19 17z" />,
  chevron: <path d="M9 6l6 6-6 6" />,
  alert: <><path d="M12 3l10 18H2L12 3z" /><path d="M12 10v5M12 18h.01" /></>,
  ban: <><circle cx="12" cy="12" r="9" /><path d="M5.6 5.600l12.8 12.8" /></>,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" /></>,
  edit: <><path d="M4 20h4L19 9l-4-4L4 16v4z" /><path d="M13.5 6.500l4 4" /></>,
  attach: <path d="M20 11l-8 8a5 5 0 01-7-7l8.5-8.500a3.5 3.5 0 015 5L10 17a2 2 0 01-3-3l7-7" />,
  zap: <path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />,
};

export type IconName = keyof typeof P;

export function Icon({ name, size = 20, className = "" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={`ic ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {P[name]}
    </svg>
  );
}

/** SpirittVPN mark: same shield as assets/logo.png (violet frame, cyan fill, dark check). */
export function Logo({ size = 64, className = "" }: { size?: number; className?: string }) {
  return (
    <svg className={`logo-mark ${className}`} width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="SpirittVPN">
      <path d="M32 4 55 12v20.5C55 46 45.5 55.5 32 60 18.500 55.500 9 46 9 32.500V12L32 4Z" fill="#7C5CFF" />
      <path d="M32 9.500 50 15.800v16.500c0 10.500-7.400 18.200-18 22-10.600-3.800-18-11.500-18-22V15.800L32 9.500Z" fill="#22D3EE" />
      <path d="M22.500 32.500l7 7L43 24.500" fill="none" stroke="#0A1020" strokeWidth="4.500" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
