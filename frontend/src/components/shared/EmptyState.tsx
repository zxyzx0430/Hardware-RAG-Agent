import React, { memo } from "react";

type EmptyStateSize = "sm" | "md";

export interface EmptyStateProps {
  /** Optional icon (usually an inline SVG). Omit to hide. */
  icon?: React.ReactNode;
  /** Primary text (required). */
  title: string;
  /** Secondary description. */
  desc?: string;
  /** Optional action node (e.g. a button). */
  action?: React.ReactNode;
  /** Compact (sm) or standard (md). Default: md. */
  size?: EmptyStateSize;
}

const SIZE_CLASS: Record<EmptyStateSize, string> = {
  sm: "emptystate-sm",
  md: "emptystate-md",
};

function EmptyStateImpl({ icon, title, desc, action, size = "md" }: EmptyStateProps) {
  return (
    <div className={`emptystate ${SIZE_CLASS[size]}`}>
      {icon ? <div className="emptystate-icon">{icon}</div> : null}
      <div className="emptystate-text">
        <p className="emptystate-title">{title}</p>
        {desc ? <p className="emptystate-desc">{desc}</p> : null}
      </div>
      {action ? <div className="emptystate-action">{action}</div> : null}
    </div>
  );
}

/** Generic empty-state for lists / panels. Pure presentational, memoized. */
export const EmptyState = memo(EmptyStateImpl);
