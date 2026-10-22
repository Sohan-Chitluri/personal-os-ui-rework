import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import * as THREE from "three";


const CAGE_RADIUS = 1.12;

function createCage() {
  const rest: THREE.Vector3[] = [new THREE.Vector3(0.02, CAGE_RADIUS, 0)];
  const starts = [1, 9, 17];
  const yBands = [0.58, 0, -0.58];
  for (let band = 0; band < yBands.length; band++) {
    const y = yBands[band]!;
    const ringRadius = CAGE_RADIUS * Math.sqrt(1 - y ** 2);
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2 + band * 0.19 + Math.sin(i * 5 + band) * 0.025;
      const uneven = 1 + Math.sin(i * 2.7 + band * 3) * 0.018;
      rest.push(new THREE.Vector3(
        Math.cos(angle) * ringRadius * uneven,
        y * CAGE_RADIUS + Math.sin(i * 3.4 + band) * 0.012,
        Math.sin(angle) * ringRadius * uneven,
      ));
    }
  }
  rest.push(new THREE.Vector3(-0.018, -CAGE_RADIUS, 0.01));

  const edges: [number, number][] = [];
  const link = (a: number, b: number) => edges.push(a < b ? [a, b] : [b, a]);
  for (const start of starts) for (let i = 0; i < 8; i++) link(start + i, start + ((i + 1) % 8));
  for (let band = 0; band < 2; band++) {
    const upper = starts[band]!;
    const lower = starts[band + 1]!;
    for (let i = 0; i < 8; i++) {
      link(upper + i, lower + i);
      if (i % 2 === band % 2) link(upper + i, lower + ((i + 1) % 8));
    }
  }
  for (let i = 0; i < 8; i += 2) {
    link(0, starts[0]! + i);
    link(rest.length - 1, starts[2]! + i + 1);
  }
  return { rest, edges };
}

function RaphaelFigure() {
  const cage = useMemo(createCage, []);
  const positions = useMemo(() => new Float32Array(cage.rest.length * 3), [cage]);
  const linePositions = useMemo(() => new Float32Array(cage.edges.length * 6), [cage]);
  const pointAttribute = useMemo(() => new THREE.BufferAttribute(positions, 3), [positions]);
  const lineAttribute = useMemo(() => new THREE.BufferAttribute(linePositions, 3), [linePositions]);
  const pointsGeometry = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", pointAttribute);
    return geometry;
  }, [pointAttribute]);
  const linesGeometry = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", lineAttribute);
    return geometry;
  }, [lineAttribute]);
  const figure = useRef<THREE.Group>(null);

  useFrame(({ clock }) => {
    const time = clock.elapsedTime;
    if (!figure.current) return;
    figure.current.rotation.y = Math.sin(time * 0.16) * 0.1 + time * 0.035;
    figure.current.rotation.x = Math.sin(time * 0.23) * 0.035;
    for (let i = 0; i < cage.rest.length; i++) {
      const rest = cage.rest[i]!;
      const nx = rest.x / CAGE_RADIUS;
      const ny = rest.y / CAGE_RADIUS;
      const nz = rest.z / CAGE_RADIUS;
      const radial = Math.sin(time * 0.82 + i * 0.63) * 0.012;
      const sway = Math.sin(time * 0.51 + i * 1.4) * 0.009;
      const offset = i * 3;
      positions[offset] = rest.x + nx * radial - nz * sway;
      positions[offset + 1] = rest.y + ny * radial + Math.sin(time * 0.7 + i) * 0.006;
      positions[offset + 2] = rest.z + nz * radial + nx * sway;
    }
    for (let i = 0; i < cage.edges.length; i++) {
      const [a, b] = cage.edges[i]!;
      const from = i * 6;
      const aOffset = a * 3;
      const bOffset = b * 3;
      linePositions[from] = positions[aOffset]!;
      linePositions[from + 1] = positions[aOffset + 1]!;
      linePositions[from + 2] = positions[aOffset + 2]!;
      linePositions[from + 3] = positions[bOffset]!;
      linePositions[from + 4] = positions[bOffset + 1]!;
      linePositions[from + 5] = positions[bOffset + 2]!;
    }
    pointAttribute.needsUpdate = true;
    lineAttribute.needsUpdate = true;
    figure.current.position.y = Math.sin(time * 1.05) * 0.025;
  });

  return (
    <group ref={figure}>
      <mesh position={[0, 0, 0]}>
        <sphereGeometry args={[0.42, 32, 32]} />
        <meshBasicMaterial color="#ffffff" />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.57, 24, 24]} />
        <meshBasicMaterial color="#eef3ff" transparent opacity={0.07} depthWrite={false} blending={THREE.AdditiveBlending} />
      </mesh>
      <lineSegments geometry={linesGeometry}>
        <lineBasicMaterial color="#e8efff" transparent opacity={0.62} blending={THREE.AdditiveBlending} />
      </lineSegments>
      <points geometry={pointsGeometry}>
        <pointsMaterial color="#f4f7ff" size={0.047} sizeAttenuation transparent opacity={0.96} />
      </points>
    </group>
  );
}

export function RaphaelOverlay() {
  const [open, setOpen] = useState(true);
  return (
    <aside aria-label="Raphael companion preview" style={{ position: "fixed", zIndex: 50, left: 18, bottom: 18, width: 178, height: 198, pointerEvents: "none" }}>
      {open ? (
        <div style={{ position: "relative", width: "100%", height: "100%", borderRadius: 14, background: "radial-gradient(ellipse at 50% 46%, rgba(17,21,29,.82), rgba(9,12,17,.65) 64%, rgba(7,9,13,.3))", border: "1px solid rgba(195,209,238,.16)", boxShadow: "0 14px 42px rgba(10,14,24,.2), inset 0 1px rgba(255,255,255,.04)", backdropFilter: "blur(8px)", overflow: "hidden" }}>
          <div style={{ position: "absolute", top: 12, left: 14, zIndex: 1, color: "rgba(232,238,249,.74)", font: "9px/1 monospace", letterSpacing: ".16em" }}>◦ RAPHAEL (M2 PREVIEW)</div>
          <button type="button" aria-label="Hide Raphael companion" onClick={() => setOpen(false)} style={{ position: "absolute", top: 6, right: 8, zIndex: 2, width: 24, height: 24, padding: 0, border: 0, color: "rgba(232,238,249,.66)", background: "transparent", fontSize: 17, cursor: "pointer", pointerEvents: "auto" }}>×</button>
          <Canvas camera={{ position: [0, 0, 4.4], fov: 43 }} dpr={[1, 1.35]} gl={{ alpha: true, antialias: true }} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            <RaphaelFigure />
          </Canvas>
          <span style={{ position: "absolute", bottom: 11, left: 14, color: "rgba(195,207,227,.54)", font: "8px/1 monospace", letterSpacing: ".12em" }}>IDLE <span style={{ color: "#c8d7f3" }}>●</span></span>
          <Link to="/raphael" style={{ position: "absolute", bottom: 8, right: 10, zIndex: 3, color: "#c8d7f3", font: "9px/1 monospace", textDecoration: "none", pointerEvents: "auto", border: "1px solid rgba(195,209,238,.3)", padding: "3px 6px", borderRadius: 4, background: "rgba(17,21,29,.6)" }}>CHAT ↗</Link>
        </div>
      ) : (

        <button type="button" onClick={() => setOpen(true)} aria-label="Show Raphael companion" style={{ width: 38, height: 38, borderRadius: "50%", border: "1px solid rgba(195,209,238,.2)", color: "#eef1f8", background: "rgba(15,18,24,.85)", boxShadow: "0 8px 24px rgba(10,14,24,.22)", cursor: "pointer", pointerEvents: "auto" }}>R</button>
      )}
    </aside>
  );
}
