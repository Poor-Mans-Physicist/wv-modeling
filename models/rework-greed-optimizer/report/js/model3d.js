/* Minecraft item model renderer (CSS 3D). Blockbench JSON: cubes from/to in 0-16 units, face UVs in 0-16 space,
   per-element rotation about one axis, and the model's "gui" display pose. MC axes (x east, y up, z south) map to CSS
   (x right, y down, z toward the viewer); only y flips, so rotations about x and z change sign. Faces are single-sided
   like the game (backface culling). Flat sprites are extruded one texel thick, as held items are in game. */
const MODEL3D = (() => {
  const FACE = {
    south: (w, h, d) => ({W: w, H: h, t: `translateZ(${d / 2}px)`}),
    north: (w, h, d) => ({W: w, H: h, t: `rotateY(180deg) translateZ(${d / 2}px)`}),
    east: (w, h, d) => ({W: d, H: h, t: `rotateY(90deg) translateZ(${w / 2}px)`}),
    west: (w, h, d) => ({W: d, H: h, t: `rotateY(-90deg) translateZ(${w / 2}px)`}),
    up: (w, h, d) => ({W: w, H: d, t: `rotateX(90deg) translateZ(${h / 2}px)`}),
    down: (w, h, d) => ({W: w, H: d, t: `rotateX(-90deg) translateZ(${h / 2}px)`}),
  };
  const AX = {x: a => `rotateX(${-a}deg)`, y: a => `rotateY(${a}deg)`, z: a => `rotateZ(${-a}deg)`};

  function bounds(m) {
    const mn = [1e9, 1e9, 1e9], mx = [-1e9, -1e9, -1e9];
    for (const e of m.elements) for (let i = 0; i < 3; i++) {
      mn[i] = Math.min(mn[i], e.from[i], e.to[i]);
      mx[i] = Math.max(mx[i], e.from[i], e.to[i]);
    }
    return {c: mn.map((v, i) => (v + mx[i]) / 2), dim: Math.max(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2], 1)};
  }

  function faceDiv(tex, uv, rot, W, H) {
    const face = document.createElement("div");
    face.className = "m3-face";
    if (!tex || !uv) return face;
    const [u1, v1, u2, v2] = uv;
    const U1 = u1 / 16 * tex.w, U2 = u2 / 16 * tex.w, V1 = v1 / 16 * tex.h, V2 = v2 / 16 * tex.h;
    const uw = Math.abs(U2 - U1), vh = Math.abs(V2 - V1);
    if (uw < 1e-6 || vh < 1e-6) return face;
    const r = ((rot || 0) % 360 + 360) % 360;
    const iw = r % 180 ? H : W, ih = r % 180 ? W : H;
    const sx = iw / uw, sy = ih / vh;
    const inner = document.createElement("div");
    inner.className = "m3-tex";
    inner.style.cssText = `width:${iw}px;height:${ih}px;left:${(W - iw) / 2}px;top:${(H - ih) / 2}px;`
      + `background-image:url(${tex.src});background-size:${tex.w * sx}px ${tex.h * sy}px;`
      + `background-position:${-Math.min(U1, U2) * sx}px ${-Math.min(V1, V2) * sy}px;`
      + `transform:rotate(${r}deg) scale(${U1 > U2 ? -1 : 1}, ${V1 > V2 ? -1 : 1});`;
    face.appendChild(inner);
    return face;
  }

  /* model: {elements, textures:{key:{src,w,h}}, gui:{rotation}}; opts: {fill, spin, perspective, pose} */
  function render(model, size, opts = {}) {
    const root = document.createElement("div");
    root.className = "m3" + (opts.spin ? " m3-spin" : "");
    root.style.width = root.style.height = size + "px";
    if (opts.perspective) root.style.perspective = opts.perspective + "px";
    const b = bounds(model);
    const S = size * (opts.fill || 0.82) / b.dim;
    const P = (x, y, z) => [(x - b.c[0]) * S, -(y - b.c[1]) * S, (z - b.c[2]) * S];
    const spin = document.createElement("div");
    spin.className = "m3-spinner";
    const scene = document.createElement("div");
    scene.className = "m3-scene";
    const g = (model.gui && model.gui.rotation) || [0, 0, 0];
    if (opts.pose !== false) scene.style.transform = `rotateX(${-g[0]}deg) rotateY(${g[1]}deg) rotateZ(${-g[2]}deg)`;
    for (const e of model.elements) {
      const grp = document.createElement("div");
      grp.className = "m3-grp";
      const rot = e.rotation;
      if (rot && rot.angle && AX[rot.axis]) {
        const O = P(rot.origin[0], rot.origin[1], rot.origin[2]);
        grp.style.transform = `translate3d(${O[0]}px,${O[1]}px,${O[2]}px) ${AX[rot.axis](rot.angle)} translate3d(${-O[0]}px,${-O[1]}px,${-O[2]}px)`;
      }
      const w = Math.abs(e.to[0] - e.from[0]) * S, h = Math.abs(e.to[1] - e.from[1]) * S, d = Math.abs(e.to[2] - e.from[2]) * S;
      const C = P((e.from[0] + e.to[0]) / 2, (e.from[1] + e.to[1]) / 2, (e.from[2] + e.to[2]) / 2);
      for (const [name, f] of Object.entries(e.faces)) {
        if (!FACE[name]) continue;
        const {W, H, t} = FACE[name](w, h, d);
        if (W < 0.05 || H < 0.05) continue;
        const div = faceDiv(model.textures[f.texture], f.uv, f.rotation, W, H);
        div.style.width = W + "px";
        div.style.height = H + "px";
        div.style.transform = `translate3d(${C[0] - W / 2}px,${C[1] - H / 2}px,${C[2]}px) ${t}`;
        grp.appendChild(div);
      }
      scene.appendChild(grp);
    }
    spin.appendChild(scene);
    root.appendChild(spin);
    return root;
  }

  /* Flat item sprite extruded opts.depth texels thick (default 3) as stacked layers about a pixel apart, so the
     edge reads as solid while it spins. */
  function sprite(src, size, opts = {}) {
    const root = document.createElement("div");
    root.className = "m3" + (opts.spin ? " m3-spin" : "");
    root.style.width = root.style.height = size + "px";
    if (opts.perspective) root.style.perspective = opts.perspective + "px";
    const spin = document.createElement("div");
    spin.className = "m3-spinner";
    const scene = document.createElement("div");
    scene.className = "m3-scene";
    const s = size * (opts.fill || 0.82), texel = s / 16, depth = texel * (opts.depth || 3);
    const layers = Math.max(12, Math.ceil(depth) + 1);
    if (opts.pose !== false) scene.style.transform = "rotateX(-12deg) rotateY(-28deg)";
    for (let i = 0; i < layers; i++) {
      const lay = document.createElement("div");
      lay.className = "m3-layer";
      const z = depth / 2 - i * depth / (layers - 1);
      const tint = i === 0 ? "" : i === layers - 1 ? "filter:brightness(0.8);" : "filter:brightness(0.5);";
      lay.style.cssText = `width:${s}px;height:${s}px;background:url(${src}) center/100% 100% no-repeat;`
        + `transform:translate3d(${-s / 2}px,${-s / 2}px,${z}px);${tint}`;
      scene.appendChild(lay);
    }
    spin.appendChild(scene);
    root.appendChild(spin);
    return root;
  }
  return {render, sprite};
})();
