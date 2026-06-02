import avatar from "../assets/flowcity-avatar.png";

export function Avatar() {
  return (
    <div className="avatar-shell" aria-label="FlowCity">
      <img src={avatar} alt="FlowCity" />
    </div>
  );
}
