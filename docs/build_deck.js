// build_deck.js — RAG Has a Relationship Problem
// Run: node build_deck.js
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");

// ── Icons ──────────────────────────────────────────────────────────────────────
const { FaExclamationTriangle, FaDatabase, FaProjectDiagram, FaCheckCircle,
        FaTimesCircle, FaBookOpen, FaToggleOn, FaCodeBranch } = require("react-icons/fa");
const { MdAccountTree } = require("react-icons/md");

async function iconPng(IconComp, color, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(IconComp, { color, size: String(size) })
  );
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

// ── Palette ────────────────────────────────────────────────────────────────────
const C = {
  bg:     "0D1B2A",
  panel:  "152235",
  code:   "0A2540",
  blue:   "018BFF",
  green:  "00CC76",
  white:  "E8EDF2",
  muted:  "7A8FA6",
  dimmed: "3A5068",
  black:  "000000",
  pureWhite: "FFFFFF",
};

// ── Helpers ────────────────────────────────────────────────────────────────────
function bg(slide) { slide.background = { color: C.bg }; }

function heading(slide, text, y = 0.25) {
  slide.addText(text, {
    x: 0.5, y, w: 9.0, h: 0.55,
    fontSize: 26, fontFace: "Calibri", bold: true,
    color: C.blue, align: "left", valign: "middle", margin: 0,
  });
}

function arrowsPlaceholder(slide, x, y, w, h, label, caption) {
  slide.addShape("roundRect", {
    x, y, w, h,
    fill: { color: C.pureWhite },
    line: { color: "DDDDDD", width: 1 },
    rectRadius: 0.12,
  });
  slide.addText(`[ ARROWS.APP DIAGRAM ]`, {
    x, y: y + h * 0.3, w, h: 0.4,
    fontSize: 13, fontFace: "Calibri", italic: true, bold: true,
    color: C.muted, align: "center", margin: 0,
  });
  slide.addText(label, {
    x, y: y + h * 0.3 + 0.42, w, h: 0.6,
    fontSize: 11, fontFace: "Calibri", italic: true,
    color: "888888", align: "center", margin: 0,
  });
  if (caption) {
    slide.addText(caption, {
      x, y: y + h + 0.05, w, h: 0.3,
      fontSize: 10, fontFace: "Calibri", color: C.green,
      align: "left", italic: true, margin: 0,
    });
  }
}

function dividerSlide(pres, mainText, subText, noteText) {
  const slide = pres.addSlide();
  bg(slide);
  // Subtle horizontal accent band behind text
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 2.1, w: 10, h: 1.4,
    fill: { color: C.panel }, line: { color: C.panel },
  });
  slide.addText(mainText, {
    x: 0.5, y: 2.15, w: 9, h: 0.8,
    fontSize: 40, fontFace: "Calibri", bold: true,
    color: C.white, align: "center", valign: "middle", margin: 0,
  });
  if (subText) {
    slide.addText(subText, {
      x: 0.5, y: 3.0, w: 9, h: 0.4,
      fontSize: 20, fontFace: "Calibri",
      color: C.muted, align: "center", margin: 0,
    });
  }
  if (noteText) {
    slide.addText(noteText, {
      x: 0.5, y: 5.1, w: 9, h: 0.35,
      fontSize: 13, fontFace: "Calibri",
      color: C.blue, align: "center", italic: true, margin: 0,
    });
  }
  return slide;
}

// ── Build ──────────────────────────────────────────────────────────────────────
async function build() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.title = "RAG Has a Relationship Problem";
  pres.author = "Nathan Smith";

  // ── Pre-render icons ────────────────────────────────────────────────────────
  const iconWarn   = await iconPng(FaExclamationTriangle, "#" + C.blue);
  const iconDB     = await iconPng(FaDatabase,            "#" + C.blue);
  const iconTree   = await iconPng(MdAccountTree,         "#" + C.green);
  const iconCheck  = await iconPng(FaCheckCircle,         "#" + C.green);
  const iconTimes  = await iconPng(FaTimesCircle,         "#" + C.muted);
  const iconBook   = await iconPng(FaBookOpen,            "#" + C.blue);
  const iconToggle = await iconPng(FaToggleOn,            "#" + C.green);
  const iconBranch = await iconPng(FaCodeBranch,          "#" + C.blue);

  // ════════════════════════════════════════════════════════════════
  // SLIDE 1 — TITLE
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    // Large decorative node circles (background)
    const nodes = [
      { x: 0.4,  y: 0.5,  r: 0.22, c: "018BFF" },
      { x: 8.8,  y: 0.9,  r: 0.16, c: "00CC76" },
      { x: 9.2,  y: 4.8,  r: 0.20, c: "018BFF" },
      { x: 0.6,  y: 4.9,  r: 0.14, c: "00CC76" },
      { x: 4.8,  y: 0.3,  r: 0.12, c: "3A5068" },
      { x: 2.0,  y: 5.1,  r: 0.10, c: "3A5068" },
    ];
    for (const n of nodes) {
      s.addShape(pres.shapes.OVAL, {
        x: n.x - n.r, y: n.y - n.r, w: n.r * 2, h: n.r * 2,
        fill: { color: n.c, transparency: 60 },
        line: { color: n.c, width: 1.5 },
      });
    }
    // Main title
    s.addText("RAG Has a", {
      x: 0.6, y: 1.4, w: 8.8, h: 0.9,
      fontSize: 52, fontFace: "Calibri", bold: true,
      color: C.white, align: "center", margin: 0,
    });
    s.addText("Relationship Problem", {
      x: 0.6, y: 2.25, w: 8.8, h: 0.9,
      fontSize: 52, fontFace: "Calibri", bold: true,
      color: C.blue, align: "center", margin: 0,
    });
    s.addText("Nathan Smith  ·  Neo4j  ·  Community Days KC", {
      x: 0.6, y: 3.45, w: 8.8, h: 0.4,
      fontSize: 17, fontFace: "Calibri",
      color: C.muted, align: "center", margin: 0,
    });
    s.addText("30-minute session", {
      x: 0.6, y: 5.0, w: 8.8, h: 0.3,
      fontSize: 12, fontFace: "Calibri",
      color: C.blue, align: "center", margin: 0,
    });
    s.addNotes("Welcome. No prior graph experience needed. We'll start from zero on graph concepts, see a live demo, and end with a framework for deciding whether your use case is a good fit.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 2 — THE CONFIDENT WRONG ANSWER
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "You've built a RAG pipeline. It mostly works.");

    // Three cards
    const cards = [
      { icon: "⚖️", label: "Legal AI",     body: "Cites a superseded policy — both versions scored high for the query" },
      { icon: "💊", label: "Medical chatbot", body: "Finds chunks about Drug A and Drug B separately; misses their interaction" },
      { icon: "🏢", label: "Internal KB",  body: '"Michael S." and "Mike Sullivan" are the same person — the system never knew' },
    ];
    const cardY = [1.05, 2.35, 3.65];
    for (let i = 0; i < 3; i++) {
      s.addShape(pres.shapes.RECTANGLE, {
        x: 0.5, y: cardY[i], w: 9.0, h: 1.05,
        fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
      });
      // Blue left accent bar
      s.addShape(pres.shapes.RECTANGLE, {
        x: 0.5, y: cardY[i], w: 0.07, h: 1.05,
        fill: { color: C.blue }, line: { color: C.blue },
      });
      // Label
      s.addText(cards[i].label, {
        x: 0.75, y: cardY[i] + 0.1, w: 2.5, h: 0.35,
        fontSize: 15, fontFace: "Calibri", bold: true,
        color: C.blue, align: "left", valign: "middle", margin: 0,
      });
      // Body
      s.addText(cards[i].body, {
        x: 0.75, y: cardY[i] + 0.48, w: 8.5, h: 0.45,
        fontSize: 15, fontFace: "Calibri",
        color: C.white, align: "left", valign: "top", margin: 0,
      });
    }
    s.addNotes("These aren't hypotheticals — all three have happened in production systems. The common thread: the retrieval system found relevant content, but missed relevant connections. Ask the audience if any of these sound familiar. Pause before the next slide.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 3 — THE PUNCHLINE
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    s.addText('"The problem usually isn\'t your embeddings or your model."', {
      x: 0.8, y: 0.9, w: 8.4, h: 1.0,
      fontSize: 26, fontFace: "Calibri", italic: true,
      color: C.white, align: "center", valign: "middle", margin: 0,
    });
    s.addText([
      { text: "Vector search finds things that look ", options: {} },
      { text: "similar", options: { bold: true, color: C.white } },
      { text: " to your question.\nNot things that are ", options: {} },
      { text: "connected", options: { bold: true, color: C.blue } },
      { text: " to each other.", options: {} },
    ], {
      x: 0.8, y: 2.15, w: 8.4, h: 1.1,
      fontSize: 24, fontFace: "Calibri",
      color: C.muted, align: "center", valign: "middle", margin: 0,
    });
    s.addText("Graphs fix the relationship problem — without replacing your existing stack.", {
      x: 1.0, y: 3.7, w: 8.0, h: 0.5,
      fontSize: 19, fontFace: "Calibri", bold: true,
      color: C.green, align: "center", margin: 0,
    });
    s.addNotes("Let this land. Wait a beat. The goal is for people to nod — they've felt this. Then: \"We're going to look at what happens when you give your retrieval pipeline connective tissue.\"");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 4 — WHAT IS A GRAPH?
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "Nodes and relationships. That's it.");

    // Left text
    s.addText("A graph is a set of", {
      x: 0.5, y: 1.0, w: 4.5, h: 0.4,
      fontSize: 17, fontFace: "Calibri", color: C.white, margin: 0,
    });
    s.addText([
      { text: "nodes", options: { bold: true, color: C.blue } },
      { text: " (things)", options: { color: C.white } },
    ], {
      x: 0.5, y: 1.4, w: 4.5, h: 0.38,
      fontSize: 17, fontFace: "Calibri", color: C.white, margin: 0,
    });
    s.addText("connected by", {
      x: 0.5, y: 1.78, w: 4.5, h: 0.35,
      fontSize: 17, fontFace: "Calibri", color: C.white, margin: 0,
    });
    s.addText([
      { text: "relationships", options: { bold: true, color: C.green } },
      { text: " (how they're connected).", options: { color: C.white } },
    ], {
      x: 0.5, y: 2.13, w: 4.5, h: 0.38,
      fontSize: 17, fontFace: "Calibri", color: C.white, margin: 0,
    });
    s.addText("Each relationship has a type —\nnot just \"connected to,\" but how.", {
      x: 0.5, y: 2.75, w: 4.5, h: 0.7,
      fontSize: 15, fontFace: "Calibri", color: C.muted,
      italic: true, margin: 0,
    });
    // Key point box
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 3.7, w: 4.4, h: 1.55,
      fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
    });
    s.addText([
      { text: "Vector search", options: { bold: true, color: C.white } },
      { text: " → finds similar content\n", options: { color: C.muted } },
      { text: "Graph traversal", options: { bold: true, color: C.blue } },
      { text: " → follows connections\n", options: { color: C.muted } },
      { text: "Both are useful. Neither replaces the other.", options: { color: C.green, italic: true } },
    ], {
      x: 0.65, y: 3.8, w: 4.1, h: 1.3,
      fontSize: 13, fontFace: "Calibri", color: C.muted,
      valign: "top", margin: 0,
    });

    // Right: arrows.app placeholder
    arrowsPlaceholder(s, 5.2, 0.85, 4.5, 4.0,
      "(Meriwether Lewis :Person)-[:OBSERVED]->(Grizzly Bear :AnimalSpecies)\n-[:BELONGS_TO]->(Carnivora :Order)-[:BELONGS_TO]->(Mammalia :Class)",
      "→ Create this diagram in arrows.app"
    );
    s.addNotes("No prior graph experience required. If you know what a row in a database is, you know what a node is. If you know what a foreign key is, you know what a relationship is. The difference is that relationships are first-class citizens — stored with the data, not computed at query time.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 5 — SQL ANALOGY
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "If you know SQL, you already understand graphs");

    // Table — left half
    const tableData = [
      [
        { text: "SQL",   options: { bold: true, color: C.white, fill: { color: C.blue   }, align: "center" } },
        { text: "Graph", options: { bold: true, color: C.white, fill: { color: "005FAF" }, align: "center" } },
      ],
      [{ text: "Table",        options: { color: C.white } }, { text: "Node label",  options: { color: C.blue  } }],
      [{ text: "Row",          options: { color: C.white } }, { text: "Node",        options: { color: C.blue  } }],
      [{ text: "Foreign key",  options: { color: C.white } }, { text: "Relationship",options: { color: C.green } }],
      [{ text: "JOIN",         options: { color: C.white } }, { text: "Traversal",   options: { color: C.green } }],
    ];
    s.addTable(tableData, {
      x: 0.5, y: 1.05, w: 4.5, h: 2.7,
      fontSize: 16, fontFace: "Calibri",
      colW: [2.25, 2.25],
      fill: { color: C.panel },
      border: { pt: 1, color: C.dimmed },
      rowH: 0.45,
    });

    // Right side text
    s.addText("The key difference:", {
      x: 5.4, y: 1.05, w: 4.2, h: 0.4,
      fontSize: 16, fontFace: "Calibri", bold: true,
      color: C.white, margin: 0,
    });
    s.addText([
      { text: "In SQL", options: { bold: true, color: C.white } },
      { text: ", relationships are ", options: { color: C.muted } },
      { text: "implicit", options: { bold: true, color: C.muted } },
      { text: " — computed at query time by matching IDs across tables.", options: { color: C.muted } },
    ], {
      x: 5.4, y: 1.55, w: 4.2, h: 0.85,
      fontSize: 15, fontFace: "Calibri", margin: 0,
    });
    s.addText([
      { text: "In a graph", options: { bold: true, color: C.white } },
      { text: ", relationships are ", options: { color: C.muted } },
      { text: "stored explicitly", options: { bold: true, color: C.blue } },
      { text: " with the data.", options: { color: C.muted } },
    ], {
      x: 5.4, y: 2.5, w: 4.2, h: 0.85,
      fontSize: 15, fontFace: "Calibri", margin: 0,
    });

    // Bottom full-width callout
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 4.15, w: 9.0, h: 1.1,
      fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
    });
    s.addText([
      { text: "Vector search", options: { bold: true, color: C.white } },
      { text: " answers: what content looks similar to my question?\n", options: { color: C.muted } },
      { text: "Graph traversal", options: { bold: true, color: C.blue } },
      { text: " answers: what is connected to this thing?\n", options: { color: C.muted } },
      { text: "Both are useful. Neither replaces the other.", options: { italic: true, color: C.green } },
    ], {
      x: 0.7, y: 4.22, w: 8.6, h: 0.9,
      fontSize: 13, fontFace: "Calibri", valign: "top", margin: 0,
    });
    s.addNotes("Meet the audience where they are. The analogy holds up well for core concepts. Where graphs earn their keep — variable-depth traversal, schema flexibility during extraction, and text-to-query accuracy — we'll get to all of that.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 6 — HOW IT'S BUILT (PIPELINE)
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "The pipeline");

    // Vertical flow — center column
    const boxW = 3.2, boxH = 0.48, cx = 1.8;
    const steps = [
      { y: 1.0,  label: "User Question",                    color: C.panel },
      { y: 1.75, label: "Embed  (text-embedding-3-small)",  color: C.panel },
      { y: 2.5,  label: "Vector Search — Neo4j",            color: C.panel },
      { y: 3.55, label: "LLM + Context",                    color: C.panel },
      { y: 4.3,  label: "Answer",                           color: C.panel },
    ];
    for (const step of steps) {
      s.addShape(pres.shapes.RECTANGLE, {
        x: cx, y: step.y, w: boxW, h: boxH,
        fill: { color: step.color }, line: { color: C.blue, width: 1 },
      });
      s.addText(step.label, {
        x: cx, y: step.y, w: boxW, h: boxH,
        fontSize: 13, fontFace: "Calibri", bold: false,
        color: C.white, align: "center", valign: "middle", margin: 0,
      });
    }
    // Down arrows between main steps
    const arrowXc = cx + boxW / 2;
    const arrowYs = [[1.48, 1.75], [2.23, 2.5], [3.03, 3.55], [4.03, 4.3]];
    for (const [y1, y2] of arrowYs) {
      s.addShape(pres.shapes.LINE, {
        x: arrowXc, y: y1, w: 0, h: y2 - y1 - 0.0,
        line: { color: C.muted, width: 1.5 },
      });
    }
    // Fork box — vector to graph traversal branch (between y=2.5 and y=3.55)
    const branchX = 5.8;
    s.addShape(pres.shapes.RECTANGLE, {
      x: branchX, y: 2.5, w: 3.2, h: 0.48,
      fill: { color: "0A2A18" }, line: { color: C.green, width: 1.5 },
    });
    s.addText("Graph Traversal", {
      x: branchX, y: 2.5, w: 3.2, h: 0.48,
      fontSize: 13, fontFace: "Calibri", bold: true,
      color: C.green, align: "center", valign: "middle", margin: 0,
    });
    s.addText("entities · relationships · hierarchy · sequence", {
      x: branchX, y: 3.0, w: 3.2, h: 0.3,
      fontSize: 10, fontFace: "Calibri",
      color: C.muted, align: "center", margin: 0,
    });
    // Horizontal line from vector search box to branch
    s.addShape(pres.shapes.LINE, {
      x: cx + boxW, y: 2.5 + boxH / 2, w: branchX - (cx + boxW), h: 0,
      line: { color: C.green, width: 1.5, dashType: "dash" },
    });
    // Green label
    s.addText("graph mode ON", {
      x: branchX, y: 2.27, w: 3.2, h: 0.22,
      fontSize: 10, fontFace: "Calibri", italic: true,
      color: C.green, align: "center", margin: 0,
    });
    // Line from branch down to merge with LLM box
    s.addShape(pres.shapes.LINE, {
      x: branchX + 3.2 / 2, y: 2.98, w: 0, h: 0.57,
      line: { color: C.green, width: 1.5, dashType: "dash" },
    });
    // Horizontal merge line
    s.addShape(pres.shapes.LINE, {
      x: cx + boxW / 2, y: 3.55, w: branchX + 3.2 / 2 - (cx + boxW / 2), h: 0,
      line: { color: C.muted, width: 1.5 },
    });

    // Bottom note
    s.addText("Vector index and graph live in the same Neo4j database. One query, two retrieval modes.", {
      x: 0.5, y: 5.1, w: 9.0, h: 0.3,
      fontSize: 11, fontFace: "Calibri", italic: true,
      color: C.muted, align: "center", margin: 0,
    });
    s.addNotes("Key insight: the Chunk nodes that hold your text and embeddings are also nodes in the graph. The same database serves both the vector index and the graph traversal. You're not maintaining two separate systems — you're adding a graph layer on top of what you'd build anyway for vector RAG.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 7 — WHAT'S IN THE GRAPH
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "The Lewis & Clark knowledge graph");

    // Left: nodes
    s.addText("NODES", {
      x: 0.5, y: 1.05, w: 4.3, h: 0.3,
      fontSize: 11, fontFace: "Calibri", bold: true,
      color: C.blue, charSpacing: 3, margin: 0,
    });
    const nodeItems = [
      { t: "Person", c: C.blue },
      { t: "Place · WaterBody", c: C.white },
      { t: "AnimalSpecies · PlantSpecies", c: C.green },
      { t: "NativeNation · Chunk", c: C.white },
    ];
    let ny = 1.42;
    for (const n of nodeItems) {
      s.addShape(pres.shapes.OVAL, { x: 0.52, y: ny + 0.08, w: 0.18, h: 0.18,
        fill: { color: n.c, transparency: 30 }, line: { color: n.c, width: 1 } });
      s.addText(n.t, {
        x: 0.82, y: ny, w: 3.8, h: 0.35,
        fontSize: 14, fontFace: "Calibri", color: n.c, margin: 0,
      });
      ny += 0.42;
    }

    // Left: relationships
    s.addText("RELATIONSHIPS", {
      x: 0.5, y: 3.3, w: 4.3, h: 0.3,
      fontSize: 11, fontFace: "Calibri", bold: true,
      color: C.green, charSpacing: 3, margin: 0,
    });
    const relItems = [
      "MENTIONED_IN · NEXT_CHUNK",
      "OBSERVED · DESCRIBED · CAMPED_AT",
      "TRADED_WITH · MET_WITH",
      "BELONGS_TO  (taxonomy)",
    ];
    let ry = 3.68;
    for (const r of relItems) {
      s.addText("→  " + r, {
        x: 0.5, y: ry, w: 4.5, h: 0.35,
        fontSize: 13, fontFace: "Calibri", color: C.muted, margin: 0,
      });
      ry += 0.38;
    }

    // Right: arrows.app placeholder
    arrowsPlaceholder(s, 5.1, 0.85, 4.6, 4.4,
      "Schema diagram: 7 node types as colored circles with key relationships.\nPerson=blue, Place=amber, WaterBody=cyan,\nAnimalSpecies=green, PlantSpecies=lime,\nNativeNation=purple, Chunk=gray",
      "→ Create the data model in arrows.app"
    );
    s.addNotes("This graph was built entirely from raw journal text — no manual annotation. An LLM extracted entity mentions and relationships from each chunk. Hardest steps: disambiguation (CLARK, CAPTAIN CLARK, WILLIAM CLARK → one node) and extraction quality control (added schema enforcement to reject invalid relationship types).");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 8 — HONEST BUILD COST
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "What it actually took");

    const steps = [
      { n: "1", title: "Ingest",        body: "Chunk by journal entry, embed, load Chunk nodes   ~1 hr · ~$5 embeddings" },
      { n: "2", title: "Extract",       body: "LLM entity + relationship extraction per chunk   ~$8  (gpt-4o-mini, full corpus)" },
      { n: "3", title: "Disambiguate",  body: "Two-pass LLM merge of CLARK / CAPTAIN CLARK / WILLIAM CLARK → one node" },
      { n: "4", title: "Enrich",        body: "GBIF taxonomy lookups · Sacagawea reference matching" },
      { n: "5", title: "Flag",          body: "Tag generic place names (:GenericLocation) so queries return real places" },
    ];
    let sy = 1.05;
    for (const step of steps) {
      // Number circle
      s.addShape(pres.shapes.OVAL, {
        x: 0.5, y: sy + 0.03, w: 0.4, h: 0.4,
        fill: { color: C.blue }, line: { color: C.blue },
      });
      s.addText(step.n, {
        x: 0.5, y: sy + 0.03, w: 0.4, h: 0.4,
        fontSize: 13, fontFace: "Calibri", bold: true,
        color: C.pureWhite, align: "center", valign: "middle", margin: 0,
      });
      // Title
      s.addText(step.title, {
        x: 1.07, y: sy, w: 1.6, h: 0.46,
        fontSize: 15, fontFace: "Calibri", bold: true,
        color: C.white, valign: "middle", margin: 0,
      });
      // Body
      s.addText(step.body, {
        x: 2.75, y: sy, w: 6.7, h: 0.46,
        fontSize: 13, fontFace: "Calibri",
        color: C.muted, valign: "middle", margin: 0,
      });
      sy += 0.64;
    }

    // Green bottom callout
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 4.5, w: 9.0, h: 0.82,
      fill: { color: "0A2A18" }, line: { color: C.green, width: 1.5 },
    });
    s.addText([
      { text: "Greenfield corpus this size: ", options: { color: C.muted } },
      { text: "2–3 days.", options: { bold: true, color: C.green } },
      { text: "  Existing chunked+embedded corpus: ", options: { color: C.muted } },
      { text: "~1 day to add the graph layer.", options: { bold: true, color: C.green } },
    ], {
      x: 0.7, y: 4.55, w: 8.6, h: 0.7,
      fontSize: 15, fontFace: "Calibri",
      valign: "middle", align: "center", margin: 0,
    });
    s.addNotes("Be honest. The audience will respect specifics over hand-waving. Extraction is the expensive step — but gpt-4o-mini at $8 for a 700K-word corpus is cheap. Disambiguation is the intellectually interesting step — the LLM is remarkably good at it with the right prompt. All code is in the repo.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 9 — BLOOM CUE
  // ════════════════════════════════════════════════════════════════
  {
    const s = dividerSlide(pres,
      "Let's look at the graph",
      "Neo4j Bloom",
      "[ switch to Bloom ]"
    );
    s.addNotes(`BLOOM WALKTHROUGH — keep to 2–3 minutes.
1. Start at Meriwether Lewis. Expand one hop. Every edge has a label — not just "connected to," but OBSERVED, CAMPED_AT, MET_WITH. The label is the meaning.
2. Click an AnimalSpecies node. Expand BELONGS_TO chain. Taxonomy tree appears: species → genus → family → order. This is the hierarchy the chatbot will query in a moment.
3. Zoom out to show a place cluster — multiple Person nodes connected to the same Place via CAMPED_AT. Invisible in a list of documents.
Goal: one "oh, that's what a knowledge graph looks like" moment. Don't linger. Move to the demo as soon as they have the picture.`);
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 10 — DEMO CUE
  // ════════════════════════════════════════════════════════════════
  {
    const s = dividerSlide(pres,
      "Live Demo",
      "Lewis & Clark GraphRAG  ·  localhost:3000",
      "Toggle: Vector only  ↔  Vector + Graph"
    );
    s.addNotes(`DEMO SCRIPT — four beats, ~10 minutes total.
BEAT 1 (1 min): "What did Lewis write about grizzly bears?" Toggle OFF then ON. Answer is similar either way — intentional. Show you're being honest: vector works fine for simple lookups.
BEAT 2 (3 min): "What species did Lewis observe near the Columbia River?" — see next slide.
BEAT 3 (3 min): "What places did the corps visit before Sergeant Floyd died?" — see following slide.
BEAT 4 (2 min): "What bird families did the expedition encounter?" — see following slide.`);
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 11 — DEMO: THE GAP
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, '"What species did Lewis observe near the Columbia River?"');

    // Left column — vector only
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 1.05, w: 4.35, h: 3.6,
      fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
    });
    s.addText("VECTOR ONLY", {
      x: 0.65, y: 1.15, w: 4.05, h: 0.3,
      fontSize: 11, fontFace: "Calibri", bold: true,
      color: C.muted, charSpacing: 3, margin: 0,
    });
    s.addText([
      { text: "LLM synthesises from text\n", options: { color: C.white, bold: true } },
      { text: "Gets ", options: { color: C.muted } },
      { text: "some", options: { italic: true, color: C.muted } },
      { text: " species — can't tell you if it got them all\n\n", options: { color: C.muted } },
      { text: "Ask the audience:\n", options: { color: C.white, bold: true } },
      { text: '"How confident are you in this list?\nHow would you verify it?"', options: { italic: true, color: C.muted } },
    ], {
      x: 0.65, y: 1.55, w: 4.05, h: 2.95,
      fontSize: 14, fontFace: "Calibri", valign: "top", margin: 0,
    });

    // Right column — graph
    s.addShape(pres.shapes.RECTANGLE, {
      x: 5.15, y: 1.05, w: 4.35, h: 3.6,
      fill: { color: "0A2A18" }, line: { color: C.green, width: 1.5 },
    });
    s.addText("+ GRAPH", {
      x: 5.3, y: 1.15, w: 4.05, h: 0.3,
      fontSize: 11, fontFace: "Calibri", bold: true,
      color: C.green, charSpacing: 3, margin: 0,
    });
    s.addText([
      { text: "Sources panel shows explicit relationships:\n\n", options: { color: C.white, bold: true } },
      { text: "Lewis ", options: { color: C.blue } },
      { text: "-[OBSERVED]→ ", options: { color: C.muted } },
      { text: "Salmon\n", options: { color: C.green } },
      { text: "Lewis ", options: { color: C.blue } },
      { text: "-[OBSERVED]→ ", options: { color: C.muted } },
      { text: "Condor\n", options: { color: C.green } },
      { text: "Lewis ", options: { color: C.blue } },
      { text: "-[OBSERVED]→ ", options: { color: C.muted } },
      { text: "...\n\n", options: { color: C.green } },
      { text: "These aren't inferred. They're stored.", options: { italic: true, color: C.muted } },
    ], {
      x: 5.3, y: 1.55, w: 4.05, h: 2.95,
      fontSize: 14, fontFace: "Calibri", valign: "top", margin: 0,
    });

    s.addText("The graph turns a fuzzy synthesis into a verifiable lookup.", {
      x: 0.5, y: 4.9, w: 9.0, h: 0.38,
      fontSize: 15, fontFace: "Calibri", italic: true, bold: true,
      color: C.white, align: "center", margin: 0,
    });
    s.addNotes("The difference isn't just answer quality — it's epistemic. With vector only, you have no way to know if the LLM missed something. With the graph, you can inspect OBSERVED relationships directly. The answer is grounded in evidence you can audit. This matters enormously in enterprise use cases where 'the AI said so' isn't good enough.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 12 — DEMO: SEQUENCE & HIERARCHY
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "Two questions vector RAG can't answer");

    // Top half
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 1.05, w: 9.0, h: 1.85,
      fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
    });
    s.addText('"What places did the corps visit before Sergeant Floyd died?"', {
      x: 0.7, y: 1.12, w: 8.6, h: 0.4,
      fontSize: 16, fontFace: "Calibri", bold: true,
      color: C.white, margin: 0,
    });
    s.addText([
      { text: "Vector only:", options: { bold: true, color: C.muted } },
      { text: " finds chunks about Floyd's death — no concept of \"the chunk before this one\"\n", options: { color: C.muted } },
      { text: "+ Graph:", options: { bold: true, color: C.green } },
      { text: " walks NEXT_CHUNK backward → sequenced timeline   ", options: { color: C.muted } },
      { text: "The answer is in the ordering of documents.", options: { italic: true, color: C.white } },
    ], {
      x: 0.7, y: 1.57, w: 8.6, h: 1.2,
      fontSize: 14, fontFace: "Calibri", valign: "top", margin: 0,
    });

    // Thin divider
    s.addShape(pres.shapes.LINE, {
      x: 0.5, y: 3.1, w: 9.0, h: 0,
      line: { color: C.blue, width: 1 },
    });

    // Bottom half
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 3.2, w: 9.0, h: 1.85,
      fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
    });
    s.addText('"What bird families did the expedition encounter?"', {
      x: 0.7, y: 3.27, w: 8.6, h: 0.4,
      fontSize: 16, fontFace: "Calibri", bold: true,
      color: C.white, margin: 0,
    });
    s.addText([
      { text: "Vector only:", options: { bold: true, color: C.muted } },
      { text: " LLM recalls taxonomy from training data — not from your corpus\n", options: { color: C.muted } },
      { text: "+ Graph:", options: { bold: true, color: C.green } },
      { text: " BELONGS_TO* links species → genus → family → order   ", options: { color: C.muted } },
      { text: "The answer comes from evidence, not parametric memory.", options: { italic: true, color: C.white } },
    ], {
      x: 0.7, y: 3.72, w: 8.6, h: 1.2,
      fontSize: 14, fontFace: "Calibri", valign: "top", margin: 0,
    });
    s.addNotes(`NEXT_CHUNK = document ordering. Any system that chunks documents throws away sequence. A graph stores it explicitly. Matters for process docs, legal filings, anything where "before/after X" is meaningful.
BELONGS_TO* = variable-depth hierarchy. The asterisk means "any number of hops." Try writing that in SQL without a recursive CTE. One clause in Cypher; an awkward WITH RECURSIVE in SQL.`);
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 13 — TAXONOMY CHAIN (ARROWS.APP)
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "What BELONGS_TO* looks like");

    arrowsPlaceholder(s, 0.5, 0.95, 9.0, 3.5,
      "(Trumpeter Swan :AnimalSpecies)-[:BELONGS_TO]->(Cygnus :Genus)\n-[:BELONGS_TO]->(Anatidae :Family)-[:BELONGS_TO]->(Anseriformes :Order)-[:BELONGS_TO]->(Aves :Class)\nEach node a lighter shade of green/teal going up the hierarchy. White background.",
      "→ Build this taxonomy chain in arrows.app · arrows.app"
    );

    // Cypher code box
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 4.65, w: 9.0, h: 0.68,
      fill: { color: C.code }, line: { color: C.dimmed, width: 1 },
    });
    s.addText("MATCH (s:AnimalSpecies)-[:BELONGS_TO*]->(ancestor)  RETURN ancestor.rank, ancestor.canonicalName", {
      x: 0.7, y: 4.72, w: 8.6, h: 0.52,
      fontSize: 13, fontFace: "Courier New",
      color: C.green, valign: "middle", margin: 0,
    });
    s.addNotes("The asterisk in [:BELONGS_TO*] means 'follow this relationship type any number of times.' The graph doesn't need to know how deep the taxonomy goes — it just follows edges. In SQL you'd need a recursive CTE with a maximum depth. In Cypher it's one asterisk.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 14 — WHY NOT POSTGRES?
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "Four things a property graph does that SQL does awkwardly");

    const items = [
      {
        n: "1", title: "Variable-depth traversal",
        body: "[:BELONGS_TO*] is one token. SQL needs WITH RECURSIVE — awkward, rarely optimized, requires a known max depth.",
        code: null,
      },
      {
        n: "2", title: "Schema-free relationships",
        body: "New relationship type in Neo4j: just write it. In SQL: new junction table, migration, foreign keys everywhere.",
        code: null,
      },
      {
        n: "3", title: "Multi-hop at scale",
        body: "Each SQL JOIN compounds in cost. Graph traversal is O(1) per hop — relationships stored as direct pointers.",
        code: null,
      },
      {
        n: "4", title: "Text-to-query accuracy",
        body: "(Lewis)-[:OBSERVED]->(Bear) reads like English. A recursive CTE does not. LLMs generate correct Cypher more reliably on the queries that matter.",
        code: null,
      },
    ];
    let iy = 1.05;
    for (const item of items) {
      s.addShape(pres.shapes.OVAL, {
        x: 0.5, y: iy + 0.04, w: 0.38, h: 0.38,
        fill: { color: C.blue }, line: { color: C.blue },
      });
      s.addText(item.n, {
        x: 0.5, y: iy + 0.04, w: 0.38, h: 0.38,
        fontSize: 13, fontFace: "Calibri", bold: true,
        color: C.pureWhite, align: "center", valign: "middle", margin: 0,
      });
      s.addText(item.title, {
        x: 1.06, y: iy, w: 3.0, h: 0.46,
        fontSize: 14, fontFace: "Calibri", bold: true,
        color: C.white, valign: "middle", margin: 0,
      });
      s.addText(item.body, {
        x: 4.15, y: iy, w: 5.3, h: 0.46,
        fontSize: 12, fontFace: "Calibri",
        color: C.muted, valign: "middle", margin: 0,
      });
      iy += 0.74;
    }

    // Bottom note
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 4.92, w: 9.0, h: 0.43,
      fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
    });
    s.addText('If your "graph" is really metadata filtering on vector results — use pgvector. Graphs earn their keep when you need to traverse.', {
      x: 0.7, y: 4.95, w: 8.6, h: 0.37,
      fontSize: 12, fontFace: "Calibri", italic: true,
      color: C.muted, align: "center", valign: "middle", margin: 0,
    });
    s.addNotes("Pre-empt the skeptic. pgvector is excellent for metadata-filtered vector search — use it if that's all you need. The graph earns its keep when: queries need arbitrary-depth traversal, schema evolves during LLM extraction, and you need a natural-language query interface that generates correct queries on complex traversals. Points 1 and 4 compound: the queries hardest for text2SQL to generate correctly are exactly the queries hardest for SQL to express at all.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 15 — DECISION FRAMEWORK
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "Is your use case a good fit?");

    // Left column — green
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 1.05, w: 4.35, h: 3.05,
      fill: { color: "0A2A18" }, line: { color: C.green, width: 1.5 },
    });
    s.addText("Strong signal ✓", {
      x: 0.65, y: 1.12, w: 4.05, h: 0.38,
      fontSize: 15, fontFace: "Calibri", bold: true,
      color: C.green, margin: 0,
    });
    const greenItems = [
      "Answers require traversal, not just retrieval",
      "Same entity named differently across documents",
      "Hierarchy or sequence is essential",
      "You need completeness, not just similarity",
    ];
    s.addText(greenItems.map(t => ({ text: t, options: { bullet: true, breakLine: true, color: C.white } })), {
      x: 0.65, y: 1.6, w: 4.0, h: 2.35,
      fontSize: 13, fontFace: "Calibri", valign: "top", margin: 0,
    });

    // Right column — muted
    s.addShape(pres.shapes.RECTANGLE, {
      x: 5.15, y: 1.05, w: 4.35, h: 3.05,
      fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
    });
    s.addText("Probably overkill ✗", {
      x: 5.3, y: 1.12, w: 4.05, h: 0.38,
      fontSize: 15, fontFace: "Calibri", bold: true,
      color: C.muted, margin: 0,
    });
    const grayItems = [
      "All questions are factual lookups",
      "Corpus is small (< a few hundred docs)",
      "No meaningful entities or relationships",
      "Need a prototype by Friday",
    ];
    s.addText(grayItems.map(t => ({ text: t, options: { bullet: true, breakLine: true, color: C.muted } })), {
      x: 5.3, y: 1.6, w: 4.0, h: 2.35,
      fontSize: 13, fontFace: "Calibri", valign: "top", margin: 0,
    });

    // Bottom diagnostic question
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 4.35, w: 9.0, h: 0.95,
      fill: { color: C.panel }, line: { color: C.blue, width: 1.5 },
    });
    s.addText('"What does my user need to know that isn\'t in any single document?"', {
      x: 0.7, y: 4.42, w: 8.6, h: 0.8,
      fontSize: 17, fontFace: "Calibri", italic: true, bold: true,
      color: C.white, align: "center", valign: "middle", margin: 0,
    });
    s.addNotes("The bottom question is the one to write down. If the answer is 'nothing — each question can be answered by a single passage' — vector RAG is sufficient. If the answer involves aggregation, sequence, hierarchy, or cross-document entity identity, a graph will pay for itself quickly.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 16 — CLOSE
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    s.addText("Three things to take home", {
      x: 0.6, y: 0.4, w: 8.8, h: 0.6,
      fontSize: 32, fontFace: "Calibri", bold: true,
      color: C.white, align: "center", margin: 0,
    });
    const takeaways = [
      { n: "1", label: "Mental model",       body: "Graphs store connections explicitly. Vectors find similarity. Neither replaces the other — they cover different failure modes." },
      { n: "2", label: "Realistic cost",     body: "Adding a graph layer to an existing RAG pipeline is 1–3 days, not a rewrite. The extraction step is the interesting hard problem." },
      { n: "3", label: "Decision signal",    body: "If the answer lives in connections between documents rather than in documents themselves, a graph will pay for itself quickly." },
    ];
    let ty = 1.2;
    for (const t of takeaways) {
      s.addShape(pres.shapes.OVAL, {
        x: 0.5, y: ty + 0.12, w: 0.5, h: 0.5,
        fill: { color: C.blue }, line: { color: C.blue },
      });
      s.addText(t.n, {
        x: 0.5, y: ty + 0.12, w: 0.5, h: 0.5,
        fontSize: 18, fontFace: "Calibri", bold: true,
        color: C.pureWhite, align: "center", valign: "middle", margin: 0,
      });
      s.addText(t.label, {
        x: 1.2, y: ty + 0.05, w: 2.4, h: 0.6,
        fontSize: 16, fontFace: "Calibri", bold: true,
        color: C.blue, valign: "middle", margin: 0,
      });
      s.addText(t.body, {
        x: 3.7, y: ty, w: 5.8, h: 0.75,
        fontSize: 13, fontFace: "Calibri",
        color: C.muted, valign: "middle", margin: 0,
      });
      ty += 0.92;
    }
    // Repo link
    s.addShape(pres.shapes.LINE, {
      x: 0.5, y: 4.18, w: 9.0, h: 0,
      line: { color: C.blue, width: 1 },
    });
    s.addText("[ github.com/YOUR_HANDLE/community-days ]", {
      x: 0.5, y: 4.28, w: 9.0, h: 0.4,
      fontSize: 16, fontFace: "Calibri", bold: true,
      color: C.blue, align: "center", margin: 0,
    });
    s.addText("Fork it  ·  swap in your corpus  ·  see what becomes answerable", {
      x: 0.5, y: 4.75, w: 9.0, h: 0.3,
      fontSize: 13, fontFace: "Calibri",
      color: C.muted, align: "center", margin: 0,
    });
    s.addNotes("Thank the audience. Remind them the repo has every build script as a standalone Python file and the demo UI as a Next.js app. Encourage them to swap in their own corpus — the extraction pipeline is general-purpose. Questions welcome.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 17 — APPENDIX DIVIDER
  // ════════════════════════════════════════════════════════════════
  dividerSlide(pres, "Appendix", "Resources & Further Reading", null)
    .addNotes("Appendix slides — use for Q&A or share after the talk.");

  // ════════════════════════════════════════════════════════════════
  // SLIDE 18 — GRAPHRAG.COM + ARROWS.APP
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "Where to go next");

    // Left column — graphrag.com
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: 1.05, w: 4.35, h: 4.25,
      fill: { color: C.panel }, line: { color: C.blue, width: 1 },
    });
    s.addText("graphrag.com", {
      x: 0.65, y: 1.15, w: 4.05, h: 0.42,
      fontSize: 18, fontFace: "Calibri", bold: true,
      color: C.blue, margin: 0,
    });
    s.addText("Concept guides, how-to walkthroughs, and the full research bibliography.", {
      x: 0.65, y: 1.62, w: 4.05, h: 0.55,
      fontSize: 13, fontFace: "Calibri",
      color: C.muted, margin: 0,
    });
    const gcSections = ["Concepts — What is GraphRAG? What is a knowledge graph?",
                        "How-to Guides — data prep through retrieval techniques",
                        "Research Appendix — 17+ papers with summaries"];
    s.addText(gcSections.map(t => ({ text: t, options: { bullet: true, breakLine: true, color: C.muted } })), {
      x: 0.65, y: 2.3, w: 4.05, h: 1.6,
      fontSize: 12, fontFace: "Calibri", valign: "top", margin: 0,
    });

    // Right column — arrows.app
    s.addShape(pres.shapes.RECTANGLE, {
      x: 5.15, y: 1.05, w: 4.35, h: 4.25,
      fill: { color: C.panel }, line: { color: C.green, width: 1 },
    });
    s.addText("arrows.app", {
      x: 5.3, y: 1.15, w: 4.05, h: 0.42,
      fontSize: 18, fontFace: "Calibri", bold: true,
      color: C.green, margin: 0,
    });
    s.addText("Free browser-based graph data modeler from Neo4j.", {
      x: 5.3, y: 1.62, w: 4.05, h: 0.55,
      fontSize: 13, fontFace: "Calibri",
      color: C.muted, margin: 0,
    });
    const arrowsSections = [
      "Sketch your graph schema before writing any code",
      "Visualize node types, relationship types, and properties",
      "Export your model as Cypher CREATE statements",
      "Share diagrams with your team",
    ];
    s.addText(arrowsSections.map(t => ({ text: t, options: { bullet: true, breakLine: true, color: C.muted } })), {
      x: 5.3, y: 2.3, w: 4.05, h: 1.8,
      fontSize: 12, fontFace: "Calibri", valign: "top", margin: 0,
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x: 5.15, y: 4.35, w: 4.35, h: 0.65,
      fill: { color: "0A2A18" }, line: { color: C.green, width: 1 },
    });
    s.addText("Pro tip: model your graph in arrows.app before writing your extraction prompt. A clear schema = better LLM extraction.", {
      x: 5.28, y: 4.39, w: 4.09, h: 0.57,
      fontSize: 11, fontFace: "Calibri", italic: true,
      color: C.green, valign: "middle", margin: 0,
    });
    s.addNotes("graphrag.com is the canonical community resource — maintained independently, comprehensive research index, good beginner guides. arrows.app is a free tool from Neo4j that's become the standard for graph data modeling sketches. Model your schema in arrows.app before writing the LLM extraction prompt — a clear schema produces better extraction results.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 19 — KEY RESEARCH PAPERS
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "Research backing the hybrid approach");

    const papers = [
      {
        title: "HybridRAG",
        arxiv: "arXiv 2408.04948",
        body:  "Empirically measures the improvement of combining graph and vector retrieval. The closest paper to what this demo shows.",
        color: C.blue,
      },
      {
        title: "From Local to Global: A Graph RAG Approach to Query-Focused Summarization",
        arxiv: "arXiv 2404.16130",
        body:  "Microsoft Research. Graph structure enables global summarization queries that vector search fundamentally cannot answer.",
        color: C.blue,
      },
      {
        title: "Knowledge Graphs on Enterprise SQL Databases",
        arxiv: "arXiv 2311.07509",
        body:  "Empirical evidence that KGs improve LLM accuracy even when structured SQL data is already available. Direct answer to the Postgres question.",
        color: C.blue,
      },
    ];
    let py = 1.05;
    for (const p of papers) {
      s.addShape(pres.shapes.RECTANGLE, {
        x: 0.5, y: py, w: 9.0, h: 1.2,
        fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
      });
      s.addShape(pres.shapes.RECTANGLE, {
        x: 0.5, y: py, w: 0.07, h: 1.2,
        fill: { color: C.blue }, line: { color: C.blue },
      });
      s.addText(p.title, {
        x: 0.72, y: py + 0.1, w: 6.5, h: 0.38,
        fontSize: 14, fontFace: "Calibri", bold: true,
        color: C.white, margin: 0,
      });
      s.addText(p.arxiv, {
        x: 7.3, y: py + 0.13, w: 2.0, h: 0.3,
        fontSize: 11, fontFace: "Calibri",
        color: C.muted, align: "right", margin: 0,
      });
      s.addText(p.body, {
        x: 0.72, y: py + 0.56, w: 8.6, h: 0.55,
        fontSize: 12, fontFace: "Calibri",
        color: C.muted, margin: 0,
      });
      py += 1.38;
    }
    s.addText("Full bibliography at  graphrag.com/appendices/research/", {
      x: 0.5, y: 5.15, w: 9.0, h: 0.25,
      fontSize: 11, fontFace: "Calibri",
      color: C.muted, align: "center", italic: true, margin: 0,
    });
    s.addNotes("HybridRAG (2408.04948) is the most directly relevant — cite it to justify this architecture. The Microsoft Local-to-Global paper (2404.16130) has the highest mainstream visibility. The SQL benchmark paper (2311.07509) is the empirical answer to Postgres skepticism.");
  }

  // ════════════════════════════════════════════════════════════════
  // SLIDE 20 — GOING DEEPER
  // ════════════════════════════════════════════════════════════════
  {
    const s = pres.addSlide();
    bg(s);
    heading(s, "For the curious");

    const papers = [
      {
        title: "Graph RAG: A Survey",
        arxiv: "arXiv 2408.08921",
        body:  "Map of the whole landscape. Start here to understand the full taxonomy of GraphRAG approaches.",
      },
      {
        title: "HybGRAG",
        arxiv: "arXiv 2412.16311",
        body:  "Specifically addresses queries spanning both unstructured text and relational knowledge — the toggle this demo demonstrates.",
      },
      {
        title: "Customer Service QA with Knowledge Graphs",
        arxiv: "arXiv 2404.17723",
        body:  "Enterprise use case. Relatable for teams building internal tooling.",
      },
      {
        title: "Think-on-Graph",
        arxiv: "arXiv 2307.07697",
        body:  "LLM reasoning over graphs — the next step beyond graph-enriched context.",
      },
    ];
    let py = 1.05;
    for (const p of papers) {
      s.addShape(pres.shapes.RECTANGLE, {
        x: 0.5, y: py, w: 9.0, h: 0.9,
        fill: { color: C.panel }, line: { color: C.dimmed, width: 1 },
      });
      s.addText(p.title, {
        x: 0.68, y: py + 0.08, w: 6.5, h: 0.32,
        fontSize: 14, fontFace: "Calibri", bold: true,
        color: C.white, margin: 0,
      });
      s.addText(p.arxiv, {
        x: 7.3, y: py + 0.1, w: 2.0, h: 0.28,
        fontSize: 11, fontFace: "Calibri",
        color: C.muted, align: "right", margin: 0,
      });
      s.addText(p.body, {
        x: 0.68, y: py + 0.48, w: 8.6, h: 0.34,
        fontSize: 12, fontFace: "Calibri",
        color: C.muted, margin: 0,
      });
      py += 1.05;
    }
    s.addText("All links and summaries at  graphrag.com/appendices/research/", {
      x: 0.5, y: 5.18, w: 9.0, h: 0.25,
      fontSize: 11, fontFace: "Calibri",
      color: C.muted, align: "center", italic: true, margin: 0,
    });
    s.addNotes("These four papers represent different threads: survey (landscape overview), HybGRAG (hybrid retrieval), customer service (enterprise case study), Think-on-Graph (LLM-native graph reasoning). Good recommendations depending on what thread the audience member wants to pull.");
  }

  // ── Write ────────────────────────────────────────────────────────────────────
  await pres.writeFile({ fileName: "/Users/nathansmith/community-days/rag_relationship_problem.pptx" });
  console.log("Written: rag_relationship_problem.pptx");
}

build().catch(err => { console.error(err); process.exit(1); });
