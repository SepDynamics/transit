const roadmapItems = [
  {
    title: "TSP Impact View",
    badge: "Highest ROI",
    body:
      "Overlay treated corridors and show before-and-after reliability, including Excess Trip Time where public or partner data supports it.",
  },
  {
    title: "Curb Conflict Layer",
    badge: "Curb Lab Ready",
    body:
      "Future integration path: blocked stops, loading pressure, illegal parking, and curb sensor signals correlated with bus delay.",
  },
  {
    title: "Event & Disruption Mode",
    badge: "Pilot friendly",
    body:
      "Quick toggle for Fenway, Longwood Medical Area, and Seaport event days using public calendars plus live route health.",
  },
];

export default function RoadmapPanel() {
  return (
    <section className="section panel roadmap-panel" aria-labelledby="roadmap-title">
      <div className="section__header section__header--wide">
        <div>
          <span className="section-eyebrow">Next 30 to 60 days</span>
          <h2 id="roadmap-title" className="section__title">
            Build what helps the first pilot conversation close.
          </h2>
          <p className="section__hint">
            These are the highest-return additions after BTD or another transit
            hub reacts to the first live demo.
          </p>
        </div>
      </div>
      <div className="roadmap-grid">
        {roadmapItems.map((item) => (
          <article className="roadmap-card" key={item.title}>
            <span className="roadmap-card__badge">{item.badge}</span>
            <h3>{item.title}</h3>
            <p>{item.body}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
