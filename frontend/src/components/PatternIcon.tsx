/** Lab chip icons keyed by GET /api/patterns `icon`, not by pattern id. */

function svgProps(className?: string) {
  return {
    width: 14,
    height: 14,
    viewBox: "0 0 16 16",
    fill: "none" as const,
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
    className,
  };
}

function BreakoutUpIcon(props: { className?: string }) {
  return (
    <svg {...svgProps(props.className)}>
      <line x1="2" y1="11" x2="14" y2="11" />
      <path d="M8 13 V3" />
      <path d="M5 6.5 L8 3 L11 6.5" />
    </svg>
  );
}

/** Support zone (lower line) plus a break through resistance (upper line). */
function SupportBreakoutIcon(props: { className?: string }) {
  return (
    <svg {...svgProps(props.className)}>
      <line x1="2" y1="13" x2="14" y2="13" />
      <line x1="2" y1="7" x2="14" y2="7" />
      <path d="M8 14 V3" />
      <path d="M5 5.5 L8 2.5 L11 5.5" />
    </svg>
  );
}

/** Support line with a tracker watching the zone (no breakout arrow). */
function SupportTrackerIcon(props: { className?: string }) {
  return (
    <svg {...svgProps(props.className)}>
      <line x1="2" y1="13" x2="14" y2="13" />
      <line x1="3" y1="9" x2="13" y2="9" strokeDasharray="1.5 1.5" />
      <circle cx="8" cy="11" r="1.6" />
    </svg>
  );
}

export function PatternIcon(props: { icon?: string; className?: string }) {
  if (props.icon === "breakout_up") return <BreakoutUpIcon className={props.className} />;
  if (props.icon === "support_breakout") return <SupportBreakoutIcon className={props.className} />;
  if (props.icon === "support_tracker") return <SupportTrackerIcon className={props.className} />;
  return null;
}
