import { promptGroups } from "./prompts";
import type { DataInfo } from "./types";
import { BrandLogo } from "./BrandLogo";
import {
  ChevronIcon,
  CloseIcon,
  DatabaseIcon,
  ExitIcon,
  PlusIcon,
} from "./icons";

type SidebarProps = {
  open: boolean;
  dataInfo: DataInfo | null;
  onClose: () => void;
  onPrompt: (prompt: string) => void;
  onNewChat: () => void;
  onLogout: () => void;
};

function compactNumber(value?: number) {
  if (!value) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function Sidebar({
  open,
  dataInfo,
  onClose,
  onPrompt,
  onNewChat,
  onLogout,
}: SidebarProps) {
  return (
    <>
      <button
        className={`sidebar-scrim ${open ? "is-visible" : ""}`}
        onClick={onClose}
        aria-label="Close question library"
        tabIndex={open ? 0 : -1}
      />
      <aside className={`sidebar ${open ? "is-open" : ""}`} aria-label="Question library">
        <div className="sidebar__top">
          <div className="brand-lockup">
            <BrandLogo />
            <span>
              <strong>VayuChat</strong>
              <small>Sustainability Lab</small>
            </span>
          </div>
          <button className="icon-button sidebar__close" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </div>

        <button className="new-chat-button" onClick={onNewChat}>
          <PlusIcon />
          New analysis
        </button>

        <nav className="prompt-library" aria-label="Suggested questions">
          <p className="sidebar-label">Question library</p>
          {promptGroups.map((group, groupIndex) => (
            <details key={group.name} open={groupIndex === 0}>
              <summary>
                <span>{group.label}</span>
                <small>{group.prompts.length}</small>
                <ChevronIcon />
              </summary>
              <div className="prompt-list">
                {group.prompts.map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => {
                      onPrompt(prompt);
                      onClose();
                    }}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </details>
          ))}
        </nav>

        <div className="sidebar__footer">
          <div className="data-card">
            <DatabaseIcon />
            <div>
              <span>Air-quality dataset</span>
              <strong>
                {compactNumber(dataInfo?.records)} records ·{" "}
                {dataInfo?.cities ?? "—"} cities · {dataInfo?.stations ?? "—"} stations
              </strong>
            </div>
          </div>
          {dataInfo?.sources?.length ? (
            <details className="source-catalog">
              <summary>
                <span>Sources & coverage</span>
                <small>{dataInfo.sources.length}</small>
                <ChevronIcon />
              </summary>
              <div className="source-catalog__list">
                {dataInfo.sources.map((source) => (
                  <article key={source.id}>
                    <strong>{source.title}</strong>
                    <span>{source.provider}</span>
                    <time>
                      {source.date_start || "Static"}
                      {source.date_end ? ` → ${source.date_end}` : ""}
                    </time>
                    <small>{source.temporal_granularity}</small>
                    <small>{source.geographic_granularity}</small>
                    <p>{source.quality}</p>
                  </article>
                ))}
              </div>
            </details>
          ) : null}
          <button className="logout-button" onClick={onLogout}>
            <ExitIcon />
            Sign out
          </button>
        </div>
      </aside>
    </>
  );
}
