type Props = {
  onNewSession?: () => void;
  disabled?: boolean;
};

export function Header({ onNewSession, disabled }: Props) {
  return (
    <header className="app-header">
      <div className="status-bar">
        <span>9:41</span>
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
