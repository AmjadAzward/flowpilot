import type { Edge, Node } from '@xyflow/react';
import { catalog } from './catalog';

/** Tiny static preview of a workflow graph, drawn from the node positions. */
export function MiniFlow({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) {
  const W = 260, H = 96, PAD = 14, NW = 30, NH = 18;
  const xs = nodes.map(n => n.position.x), ys = nodes.map(n => n.position.y);
  const minX = Math.min(...xs), minY = Math.min(...ys);
  const spanX = Math.max(1, Math.max(...xs) - minX), spanY = Math.max(1, Math.max(...ys) - minY);
  const scale = Math.min((W - PAD * 2 - NW) / spanX, (H - PAD * 2 - NH) / spanY, 0.2);
  const offX = (W - (spanX * scale + NW)) / 2, offY = (H - (spanY * scale + NH)) / 2;
  const pos = (n: Node) => ({ x: offX + (n.position.x - minX) * scale, y: offY + (n.position.y - minY) * scale });
  const byId = new Map(nodes.map(n => [n.id, n]));
  return (
    <svg className="mini-flow" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Workflow preview" preserveAspectRatio="xMidYMid meet">
      {edges.map(e => {
        const s = byId.get(e.source), t = byId.get(e.target);
        if (!s || !t) return null;
        const a = pos(s), b = pos(t);
        const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
        return <path key={e.id} d={`M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`} className="mini-edge" />;
      })}
      {nodes.map(n => {
        const p = pos(n);
        const color = catalog.find(c => c.type === n.type)?.color ?? '#94a3b8';
        return <rect key={n.id} x={p.x} y={p.y} width={NW} height={NH} rx={5} fill={color} />;
      })}
    </svg>
  );
}
