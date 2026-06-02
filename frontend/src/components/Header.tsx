import { ChevronDown, Clock3, Menu } from "lucide-react";

export function Header() {
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
          <ChevronDown size={14} />
        </div>
        <div className="nav-actions">
          <Clock3 size={24} />
          <Menu size={26} />
        </div>
      </div>
    </header>
  );
}
