type Props = {
  onNewSession?: () => void;
  onHistoryClick?: () => void;
  hasHistory?: boolean;
  disabled?: boolean;
};

export function Header({ onNewSession, onHistoryClick, hasHistory, disabled }: Props) {
  return (
    <header className="app-header">
      <div className="status-bar">
        <span aria-hidden="true" />
        <span className="dynamic-island" />
        <span className="signal">▮▮▮</span>
      </div>
      <div className="nav-bar">
        <div className="brand-row">
          <span className="brand-mark">FlowCity</span>
          <span className="city-text">西安</span>
        </div>
        <div className="nav-actions">
          <span>周末闲时规划</span>
          {hasHistory && onHistoryClick && (
            <button type="button" className="history-button" onClick={onHistoryClick} disabled={disabled}>
              历史
            </button>
          )}
          {onNewSession && (
            <button type="button" className="new-session-button" onClick={onNewSession} disabled={disabled}>
              新规划
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
