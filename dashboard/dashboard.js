/* CCA Precinct — dispatch board.
 *
 * One file, two hosts. In the VS Code webview it receives events by
 * postMessage; served standalone by scripts/dashboard.py it polls
 * events.jsonl. Everything below the transport is identical, which is the
 * point: the board is skinned once.
 *
 * Every animation encodes real data. An officer is a critic, a perp is a
 * finding, the stamp is the vote. Nothing moves that does not mean something.
 */

/* ── Sprites ──────────────────────────────────────────────────────────────
 * Pixel grids. '.' is transparent; every other character indexes a palette
 * supplied at draw time, so one officer sprite serves every critic.
 */

const OFFICER = [
  "....####....",
  "...######...",
  "..########..",
  "..########..",
  "..#oooooo#..",
  "..o@oooo@o..",
  "..oooooooo..",
  "...oooooo...",
  "....####....",
  ".##########.",
  ".####**####.",
  ".##########.",
  ".##########.",
  ".###....###.",
  "####....####",
];

const ARM_REST = [
  "###",
  "###",
  "##.",
  "##.",
];

const ARM_AIM = [
  "###......",
  "###======",
  "###======",
  "###.=....",
];

const MUZZLE = [
  ".%%.",
  "%%%%",
  ".%%.",
];

const BADGE = [
  "....###....",
  "..#######..",
  ".#########.",
  "####***####",
  "###*****###",
  "####***####",
  ".#########.",
  ".#########.",
  "..#######..",
  "...#####...",
  "....###....",
  ".....#.....",
];

const BADGE_PAL = { "#": "#f0b429", "*": "#0d1b3e" };

const PERP = [
  "..#....#..",
  "...#..#...",
  "..######..",
  ".#@####@#.",
  "##########",
  "##.####.##",
  ".#......#.",
  "..#....#..",
];

const PERP_DOWN = [
  "..........",
  "..........",
  "..........",
  "..........",
  ".#......#.",
  "..######..",
  ".#@####@#.",
  "##########",
];

const UNIFORM = { "#": "#24407f", o: "#e8b98f", "@": "#0d1b3e", "*": "#f0b429", "=": "#5d6b8f", "%": "#fff6c4" };
const UNIFORM_B = { "#": "#7a2450", o: "#d8a97c", "@": "#0d1b3e", "*": "#f0b429", "=": "#5d6b8f", "%": "#fff6c4" };
const UNIFORM_C = { "#": "#1d5c4f", o: "#e8b98f", "@": "#0d1b3e", "*": "#f0b429", "=": "#5d6b8f", "%": "#fff6c4" };
const COATS = [UNIFORM, UNIFORM_B, UNIFORM_C];

const PERP_COLOR = {
  critical: { "#": "#ff2d4a", "@": "#2b0910" },
  major: { "#": "#ff2d4a", "@": "#2b0910" },
  minor: { "#": "#f0b429", "@": "#2b1c02" },
};
const PERP_FLASH = { "#": "#ffffff", "@": "#ffffff" };

function drawSprite(ctx, rows, x, y, scale, palette) {
  for (let row = 0; row < rows.length; row += 1) {
    const line = rows[row];
    for (let col = 0; col < line.length; col += 1) {
      const key = line[col];
      if (key === ".") continue;
      const color = palette[key];
      if (!color) continue;
      ctx.fillStyle = color;
      ctx.fillRect((x + col) * scale, (y + row) * scale, scale, scale);
    }
  }
}

/* ── The gallery: one canvas, one timeline ───────────────────────────── */

const SCENE = {
  canvas: null,
  ctx: null,
  scale: 3,
  width: 0,
  height: 0,
  officers: [],
  perps: [],
  start: 0,
  running: false,
  outcome: "",
  frame: 0,
};

const BEATS = { ENTER: 520, AIM: 700, FIRE: 860, HIT: 980, VERDICT: 1120, DONE: 2800 };

function reducedMotion() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function sizeScene() {
  const canvas = SCENE.canvas;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (rect.width < 2) return;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  // integer CSS scale keeps every pixel square; clamped so a wide board does
  // not turn a 12px officer into a billboard.
  const scaleCss = Math.max(3, Math.min(6, Math.floor(rect.width / 160)));
  SCENE.scale = scaleCss * dpr;
  SCENE.width = Math.floor(canvas.width / SCENE.scale);
  SCENE.height = Math.floor(canvas.height / SCENE.scale);
}

function paintScene(now) {
  const ctx = SCENE.ctx;
  if (!ctx) return;
  const scale = SCENE.scale;
  const LW = SCENE.width;
  const LH = SCENE.height;
  ctx.clearRect(0, 0, SCENE.canvas.width, SCENE.canvas.height);

  const ground = LH - 17;
  const t = SCENE.running ? now - SCENE.start : -1;
  const still = reducedMotion();

  // the sidewalk
  ctx.fillStyle = "rgba(43,58,104,.55)";
  ctx.fillRect(0, (ground + 15) * scale, SCENE.canvas.width, scale);

  // a lamp post at the far end of the beat, standing ON the sidewalk
  const lampTop = ground - 6;
  const lampBase = ground + 15;
  // No light pool. Every version of it read as a grey slab over the hatching;
  // the post and head already anchor the end of the beat.
  ctx.fillStyle = "rgba(240,180,41,.22)";
  ctx.fillRect((LW - 11) * scale, lampTop * scale, scale, (lampBase - lampTop) * scale);
  ctx.fillStyle = "rgba(240,180,41,.55)";
  ctx.fillRect((LW - 14) * scale, lampTop * scale, 7 * scale, 2 * scale);

  SCENE.officers.forEach((officer, index) => {
    const bob = still || SCENE.running ? 0 : (Math.sin(now / 520 + index * 1.7) > 0 ? 0 : 1);
    const x = 9 + index * 18;
    const y = ground + bob;
    const aiming = t >= BEATS.AIM && t < BEATS.DONE;
    const recoil = t >= BEATS.FIRE && t < BEATS.FIRE + 90 ? 2 : 0;
    // an out-of-service officer is greyed out, not recoloured at random
    const palette = officer.degraded
      ? { ...COATS[index % 3], "#": "#46507a", o: "#9aa2b8" }
      : COATS[index % 3];

    drawSprite(ctx, OFFICER, x, y, scale, palette);
    if (aiming && !officer.degraded) {
      drawSprite(ctx, ARM_AIM, x + 9 - recoil, y + 9, scale, palette);
      if (t >= BEATS.FIRE && t < BEATS.FIRE + 80) {
        drawSprite(ctx, MUZZLE, x + 18 - recoil, y + 9, scale, palette);
      }
    } else {
      drawSprite(ctx, ARM_REST, x + 9, y + 9, scale, palette);
    }
  });

  // tracers
  if (t >= BEATS.FIRE && t < BEATS.HIT) {
    const travel = (t - BEATS.FIRE) / (BEATS.HIT - BEATS.FIRE);
    ctx.fillStyle = "#fff6c4";
    SCENE.officers.forEach((officer, index) => {
      if (officer.degraded) return;
      const from = 9 + index * 18 + 19;
      SCENE.perps.forEach((perp) => {
        const to = perp.x;
        const px = from + (to - from) * travel;
        ctx.fillRect(Math.round(px) * scale, (ground + 9) * scale, scale * 3, scale);
      });
    });
  }

  SCENE.perps.forEach((perp) => {
    let x = perp.x;
    if (t >= 0 && t < BEATS.ENTER) {
      const entering = t / BEATS.ENTER;
      x = LW + 12 - (LW + 12 - perp.x) * entering;
    }
    if (t >= BEATS.HIT && t < BEATS.HIT + 120) x += 2;
    if (SCENE.outcome === "pass" && t >= BEATS.VERDICT) {
      x -= ((t - BEATS.VERDICT) / 420) * 40;
    }
    const hit = t >= BEATS.HIT && t < BEATS.HIT + 90;
    const down = SCENE.outcome === "block" && t >= BEATS.VERDICT;
    const palette = hit ? PERP_FLASH : (PERP_COLOR[perp.severity] || PERP_COLOR.minor);
    drawSprite(ctx, down ? PERP_DOWN : PERP, Math.round(x), ground + 7, scale, palette);
  });

  if (SCENE.running && t > BEATS.DONE) {
    SCENE.running = false;
    SCENE.perps = SCENE.outcome === "block" ? SCENE.perps : [];
  }
}

function loop(now) {
  paintScene(now);
  SCENE.frame = window.requestAnimationFrame(loop);
}

function playIncident(vote, findings, critics) {
  if (!SCENE.ctx) return;
  sizeScene();
  SCENE.officers = critics.length
    ? critics
    : [{ name: "opus", degraded: false }, { name: "fable", degraded: false }];
  const shown = (findings && findings.length ? findings : [{ severity: "minor" }]).slice(0, 3);
  SCENE.perps = shown.map((finding, index) => ({
    severity: String(finding.severity || "minor"),
    x: Math.max(52, Math.round(SCENE.width * 0.56) - index * 14),
  }));
  SCENE.outcome = vote;
  SCENE.start = performance.now();
  SCENE.running = true;

  const gallery = document.getElementById("gallery");
  const stamp = document.getElementById("stamp");
  const label = { block: "BUSTED!", queue: "CITED", pass: "CLEARED" }[vote] || "CLEARED";
  const tone = { block: "busted", queue: "cited", pass: "cleared" }[vote] || "cleared";

  stamp.className = "stamp";
  stamp.textContent = "";
  window.setTimeout(() => {
    stamp.textContent = label;
    stamp.className = "stamp show " + tone;
    if (vote === "block" && !reducedMotion()) {
      gallery.classList.add("shake");
      window.setTimeout(() => gallery.classList.remove("shake"), 360);
    }
  }, reducedMotion() ? 0 : BEATS.VERDICT);

  window.setTimeout(() => { stamp.className = "stamp"; }, BEATS.DONE);
}

/* ── Reading the log ─────────────────────────────────────────────────── */

function esc(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function digest(events) {
  const votes = [];
  const perFile = {};
  const critics = {};
  const findings = [];
  const changes = [];
  const fixState = {};
  const live = {};
  const dispatches = [];
  const verdicts = [];
  const asked = [];
  let edits = 0;
  let phase = { current: null, total: null, checks: {}, fenced: 0 };

  events.forEach((event, at) => {
    const p = event.payload || {};
    switch (event.type) {
      case "edit_started":
        edits += 1;
        changes.push({ ...p, ts: event.ts, at });
        break;
      case "critic_dispatched":
        asked.push({ ts: event.ts, at, critic: p.critic, file: p.file });
        critics[p.critic] = critics[p.critic] || { name: p.critic, model: p.model, found: 0, degraded: false, beat: p.critic };
        critics[p.critic].model = p.model || critics[p.critic].model;
        break;
      case "critic_dispatched_at": break;
      case "critic_verdict": {
        verdicts.push({ ts: event.ts, at, critic: p.critic, verdict: p.verdict,
                        file: p.file, degraded: Boolean(p.degraded),
                        count: (p.findings || []).length });
        const entry = critics[p.critic] || (critics[p.critic] = { name: p.critic, model: "", found: 0, degraded: false });
        entry.found += (p.findings || []).length;
        entry.degraded = Boolean(p.degraded);
        (perFile[p.file] = perFile[p.file] || []).push(p.verdict);
        for (const f of p.findings || []) {
          findings.push({ ...f, critic: p.critic, at: event.ts, idx: at });
        }
        break;
      }
      case "vote": votes.push({ decision: p.decision, file: p.file, ts: event.ts, at }); break;
      case "arbiter_pending":
        live[String(p.id)] = { id: String(p.id), kind: p.kind, file: p.file,
                              options: p.options || [], deadline: p.deadline || 0 };
        break;
      case "arbitration":
        delete live[String(p.id)];
        break;
      case "fix_skipped":
        if (p.key) fixState[String(p.key)] = "skipped";
        break;
      case "fix_accepted":
        if (p.key) fixState[String(p.key)] = "todo";
        break;
      case "fix_dispatched":
        for (const key of p.keys || []) fixState[String(key)] = "sent";
        dispatches.push({ ts: event.ts, at, keys: p.keys || [], via: p.via || "" });
        break;
      case "fix_resolved":
        for (const key of p.keys || []) fixState[String(key)] = "done";
        break;
      case "phase_context": phase.current = p.current; phase.total = p.total; break;
      case "phase_gate":
        phase.checks = p.checks || {};
        if (phase.current === null || phase.current === undefined) phase.current = p.phase;
        break;
      case "phase_fenced": if (!p.allowed) phase.fenced += 1; break;
      default: break;
    }
  });

  const compared = Object.values(perFile).filter((v) => v.length >= 2);
  const agreed = compared.filter((v) => new Set(v).size === 1).length;

  return {
    edits,
    findings,
    changes,
    fixState,
    live: Object.values(live).filter((r) => !r.deadline || r.deadline * 1000 > Date.now()),
    votes,
    currentCase: buildCase(changes, findings, votes),
    progress: fixProgress(dispatches, changes, asked, verdicts, votes),
    phase,
    critics: Object.values(critics),
    blocked: votes.filter((v) => v.decision === "block").length,
    queued: votes.filter((v) => v.decision === "queue").length,
    cleared: votes.filter((v) => v.decision === "pass").length,
    sync: compared.length ? Math.round((agreed / compared.length) * 100) : null,
  };
}

/* The three questions the board has to answer about the last edit: what did
 * the coder do, what did the critics say about it, and what is the next thing
 * to do. They are one object because they are one story. */
function buildCase(changes, findings, votes) {
  const vote = votes[votes.length - 1];
  if (!vote) {
    const last = changes[changes.length - 1];
    return last ? { change: last, findings: [], decision: "", file: last.file } : null;
  }
  const change = changes.filter((c) => c.file === vote.file && c.at <= vote.at).pop()
    || changes[changes.length - 1]
    || null;
  // Scope to the verdicts that produced THIS vote. Matching on the file name
  // instead pulled in every finding ever recorded against that path, so a
  // second edit to the same file showed the first edit's complaints too.
  const since = change ? change.at : -1;
  const mine = findings.filter((f) => f.idx > since && f.idx < vote.at);
  return { change, findings: mine, decision: vote.decision, file: vote.file };
}

/* Where a dispatched fix has got to. Every stage is read from events that
 * already existed -- the board was simply not showing them, so the operator
 * saw "handed to Claude" and then nothing until it was over. */
function fixProgress(dispatches, changes, asked, verdicts, votes) {
  const sent = dispatches[dispatches.length - 1];
  if (!sent) return null;

  // the key is "file|line|suggestion", so the file is everything before the first bar
  const files = [...new Set((sent.keys || []).map((key) => String(key).split("|")[0]))];

  // Position in an append-only log, not wall-clock: a timestamp can be equal,
  // skewed, or written out of order, and "did this happen after the dispatch"
  // is the one question the whole strip depends on.
  const since = sent.at;
  const edit = changes.filter((c) => c.at > since && files.includes(c.file)).pop();
  const after = edit ? edit.at : since;
  const vote = votes.filter((v) => v.at > after && files.includes(v.file)).pop();
  const seen = verdicts.filter((v) => v.at > after && files.includes(v.file));
  const called = asked.filter((a) => a.at > after && files.includes(a.file));

  const officers = [...new Set(called.map((a) => a.critic))].map((name) => {
    const said = seen.filter((v) => v.critic === name).pop();
    return { name, verdict: said ? said.verdict : "", degraded: said ? said.degraded : false };
  });

  let stage = "waiting";
  if (vote) stage = vote.decision === "pass" ? "cleared" : "rejected";
  else if (called.length || seen.length) stage = "reviewing";
  else if (edit) stage = "working";

  return { stage, files, officers, count: (sent.keys || []).length, via: sent.via };
}

const STATE_COPY = {
  todo: "Accepted",
  sent: "Handed to Claude",
  done: "Done \u2014 this file passed review",
  skipped: "Skipped \u2014 not worth fixing",
};

let LIVE = [];
let PROGRESS = null;

let FIX_STATE = {};

/* Identity matches ccalib.fixes.fix_key exactly: "file|line|suggestion".
 * The hash stays on the Python side; the board never reimplements it. */
function stepKey(step) {
  return step.file + "|" + step.line + "|" + step.text;
}

function state_of(step) {
  return FIX_STATE[stepKey(step)] || "open";
}

const VERDICT_COPY = {
  block: {
    label: "BLOCKED",
    tone: "bad",
    what: "Claude was stopped. Fix the findings, then make the edit again.",
  },
  queue: {
    label: "CITED",
    tone: "warn",
    what: "The edit stands, but these are now open in CF.md and the session cannot finish until you clear each one.",
  },
  pass: {
    label: "CLEARED",
    tone: "good",
    what: "Nothing to do. Both critics found nothing worth reporting.",
  },
};

/* ── Painting the board ──────────────────────────────────────────────── */

const RUNGS = [["tests", "TESTS"], ["critics", "CRITICS"], ["commit", "BOOKED"], ["tree", "SCENE"]];

function rungMark(value) {
  if (value === true) return ["yes", "✔"];
  if (value === false) return ["no", "✕"];
  return ["", "–"];
}

function paintBoard(state, pending) {
  const alert = state.blocked > 0 && state.votes.length > 0
    && state.votes[state.votes.length - 1].decision === "block";

  const siren = document.getElementById("siren");
  siren.classList.toggle("alert", alert || pending.length > 0);

  const dispatch = document.getElementById("dispatch");
  if (pending.length) {
    dispatch.textContent = "AWAITING YOUR CALL";
    dispatch.className = "dispatch alert";
  } else if (alert) {
    dispatch.textContent = "SHOTS FIRED";
    dispatch.className = "dispatch busy";
  } else {
    dispatch.textContent = "ALL QUIET";
    dispatch.className = "dispatch";
  }

  const watch = document.getElementById("watch");
  watch.innerHTML = state.phase.current
    ? "WATCH <b>" + esc(state.phase.current) + (state.phase.total ? " OF " + esc(state.phase.total) : "") + "</b>"
    : "NO WATCH ASSIGNED";

  document.getElementById("roster").innerHTML = state.critics.length
    ? state.critics.map((critic, index) => {
        const code = critic.degraded ? ["off", "10-7 OUT"] : ["on", "10-8 PATROL"];
        return '<div class="officer">'
          + '<canvas width="36" height="46" data-coat="' + index + '"></canvas>'
          + '<div><div class="who">' + esc(String(critic.name).toUpperCase()) + "</div>"
          + '<div class="beat">' + esc(critic.model || "unassigned") + "</div></div>"
          + '<span class="tencode ' + code[0] + '">' + code[1] + "</span>"
          + '<div class="tally"><b>' + critic.found + "</b><span>COLLARS</span></div>"
          + "</div>";
      }).join("")
    : '<div class="empty">No officers have reported for duty.</div>';

  document.querySelectorAll("#roster canvas").forEach((canvas) => {
    const ctx = canvas.getContext("2d");
    const index = Number(canvas.dataset.coat || 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawSprite(ctx, OFFICER, 0, 0, 3, COATS[index % 3]);
    drawSprite(ctx, ARM_REST, 9, 9, 3, COATS[index % 3]);
  });

  document.getElementById("rungs").innerHTML = RUNGS.map(([key, label]) => {
    const [cls, mark] = rungMark(state.phase.checks[key]);
    return '<div class="rung ' + cls + '"><b>' + mark + "</b><span>" + label + "</span></div>";
  }).join("");

  document.getElementById("chips").innerHTML = [
    ["EDITS", state.edits],
    ["BUSTS", state.blocked],
    ["CITATIONS", state.queued],
    ["PARTNER SYNC", state.sync === null ? "—" : state.sync + "%"],
    ["FENCED", state.phase.fenced],
  ].map(([label, value]) =>
    '<div class="chip"><b>' + esc(value) + "</b><span>" + label + "</span></div>").join("");

  FIX_STATE = state.fixState;
  LIVE = state.live;
  PROGRESS = state.progress;
  paintCase(state.currentCase);

  paintSheet(state);

  const waiting = document.getElementById("waiting");
  if (!pending.length) {
    waiting.hidden = true;
  } else {
    waiting.hidden = false;
    waiting.innerHTML = "<h2>THE CAPTAIN WANTS A WORD</h2>"
      + pending.map((request) => {
          if (request.kind === "commit" && request.phase) {
            return '<div class="q">Watch ' + esc(request.phase.number) + " of "
              + esc(request.phase.total) + " — " + esc(request.phase.title)
              + " is ready to book.</div>";
          }
          return '<div class="q">Officers want to stop the change to <b>'
            + esc(request.file) + "</b>.</div>";
        }).join("")
      + '<div class="hint">Answer in the dialog. If you ignore it, the block stands.</div>';
  }
}

const SEV_RANK = { minor: 1, major: 2, critical: 3 };

const ROW_STATE = {
  open:       ["open", "OPEN"],
  queued:     ["queued", "QUEUED FOR CLAUDE"],
  fixed:      ["fixed", "FIXED"],
  skipped:    ["skipped", "SKIPPED"],
  superseded: ["superseded", "SUPERSEDED"],
};

/* One row per defect, not one per critic.
 *
 * Two critics reaching the same conclusion is corroboration, which is worth
 * showing; printing it twice as if two things were wrong is not. Rows are
 * merged on file+line+issue, so differently-worded observations stay separate
 * -- they really are different observations. */
function mergeFindings(findings) {
  const rows = new Map();
  for (const finding of findings) {
    const key = [finding.file, finding.line, finding.issue].join("|");
    const row = rows.get(key) || {
      file: finding.file, line: finding.line, issue: finding.issue,
      severity: "minor", kind: finding.kind, critics: [], evidence: "",
      suggestion: "", idx: -1,
    };
    if (!row.critics.includes(finding.critic)) row.critics.push(finding.critic);
    if (!row.evidence && finding.evidence) row.evidence = finding.evidence;
    if (!row.suggestion && finding.suggestion) row.suggestion = finding.suggestion;
    if ((SEV_RANK[finding.severity] || 1) > (SEV_RANK[row.severity] || 1)) {
      row.severity = finding.severity;
    }
    row.idx = Math.max(row.idx, finding.idx ?? -1);
    rows.set(key, row);
  }
  return [...rows.values()];
}

function rowState(row, fixState, passes) {
  const queued = row.suggestion
    ? fixState[row.file + "|" + row.line + "|" + row.suggestion]
    : undefined;
  if (queued === "skipped") return "skipped";
  if (queued === "done") return "fixed";
  if (queued === "todo" || queued === "sent") return "queued";
  // the file passed review after this was raised, so it no longer describes
  // the code -- but nobody acted on it, which is a different thing from fixed
  if (passes.some((vote) => vote.file === row.file && vote.at > row.idx)) return "superseded";
  return "open";
}

function sheetRow(row, state) {
  const [cls, label] = ROW_STATE[state] || ROW_STATE.open;
  const sev = String(row.severity || "minor").toLowerCase();
  const corroborated = row.critics.length > 1;
  return '<div class="bust ' + esc(sev) + " " + esc(cls) + '">'
    + '<span class="sev">' + esc(sev.toUpperCase()) + "</span>"
    + '<div class="body">'
    + '<div class="what">' + esc(row.issue) + "</div>"
    + '<div class="where">' + esc(row.file) + ":" + esc(row.line)
    + "  \u00b7  " + (corroborated ? "both officers agreed: " : "collared by ")
    + esc(row.critics.join(" + ")) + "</div>"
    + (row.evidence ? '<div class="why">evidence: ' + esc(row.evidence) + "</div>" : "")
    + "</div>"
    + '<span class="rowstate">' + esc(label) + "</span>"
    + "</div>";
}

let SHEET_OPEN = false;

function paintSheet(state) {
  const sheet = document.getElementById("sheet");
  const passes = state.votes.filter((vote) => vote.decision === "pass");
  const rows = mergeFindings(state.findings)
    .map((row) => ({ row, state: rowState(row, state.fixState, passes) }))
    .sort((a, b) => b.row.idx - a.row.idx);

  if (!rows.length) {
    sheet.innerHTML = '<div class="empty">Rap sheet is clean. Nobody has been collared yet.</div>';
    document.getElementById("sheet-count").textContent = "";
    return;
  }

  const open = rows.filter((entry) => entry.state === "open" || entry.state === "queued");
  const closed = rows.filter((entry) => entry.state !== "open" && entry.state !== "queued");

  document.getElementById("sheet-count").textContent =
    open.length + " open \u00b7 " + closed.length + " closed";

  sheet.innerHTML =
    (open.length
      ? open.map((entry) => sheetRow(entry.row, entry.state)).join("")
      : '<div class="empty">Nothing outstanding.</div>')
    + (closed.length
        ? "<details class=\"closed\"" + (SHEET_OPEN ? " open" : "") + "><summary>"
          + closed.length + " closed</summary>"
          + closed.map((entry) => sheetRow(entry.row, entry.state)).join("")
          + "</details>"
        : "");

  // The board repaints on every poll. Without this the disclosure snaps shut
  // twice a second and the closed findings are unreadable.
  const details = sheet.querySelector("details.closed");
  if (details) {
    details.addEventListener("toggle", () => { SHEET_OPEN = details.open; });
  }
}

function paintCase(current) {
  const panel = document.getElementById("casefile");
  if (!current || !current.change) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const change = current.change;
  const rows = (change.excerpt || []).map((row) => {
    const cls = { "+": "add", "-": "del", "@": "hunk" }[row.sign] || "ctx";
    return '<div class="line ' + cls + '">' + esc(row.text) + "</div>";
  }).join("");

  const verdict = VERDICT_COPY[current.decision] || null;

  // THE FIX: a critic's `suggestion` is the actionable half of a finding and
  // was being thrown away. Deduplicated, because both critics often land on
  // the same remedy.
  const seen = new Set();
  const steps = [];
  for (const finding of current.findings) {
    const text = String(finding.suggestion || "").trim();
    if (!text || seen.has(text.toLowerCase())) continue;
    seen.add(text.toLowerCase());
    steps.push({
      text,
      file: finding.file,
      line: finding.line,
      severity: finding.severity,
      kind: finding.kind,
      critic: finding.critic,
      issue: finding.issue,
      evidence: finding.evidence,
    });
  }

  panel.innerHTML = ''
    + '<h2>CASE FILE</h2>'
    + '<div class="case">'

    + '  <section class="col">'
    + '    <h3>WHAT CLAUDE DID</h3>'
    + '    <div class="filename">' + esc(change.file) + "</div>"
    + '    <div class="meta">' + esc(change.tool || "Edit") + " &nbsp;·&nbsp; "
    + '<span class="plus">+' + esc(change.added || 0) + "</span> "
    + '<span class="minus">−' + esc(change.removed || 0) + "</span>"
    + (change.truncated ? " &nbsp;·&nbsp; excerpt" : "") + "</div>"
    + '    <div class="diff">' + (rows || '<div class="line ctx">(no textual change)</div>') + "</div>"
    + "  </section>"

    + '  <section class="col">'
    + '    <h3>WHAT THE CRITICS SAID</h3>'
    + (current.findings.length
        ? current.findings.map((finding) => {
            const sev = String(finding.severity || "minor").toLowerCase();
            return '<div class="claim ' + esc(sev) + '">'
              + '<span class="sev">' + esc(sev.toUpperCase()) + "</span> "
              // "correctness \u00b7 correctness" reads as a stutter when the critic
              // is named after the thing it is looking for
              + '<span class="by">' + esc(finding.critic)
              + (finding.kind && String(finding.kind) !== String(finding.critic)
                  ? " \u00b7 " + esc(finding.kind) : "")
              + "</span>"
              + '<div class="what">' + esc(finding.issue) + "</div>"
              + (finding.evidence
                  ? '<div class="why">proof: ' + esc(finding.evidence) + "</div>"
                  : '<div class="why weak">no evidence given — cannot block</div>')
              + "</div>";
          }).join("")
        : '<div class="none">Both critics passed it.</div>')
    + "  </section>"

    + '  <section class="col">'
    + '    <h3>WHAT TO DO NEXT</h3>'
    + (verdict
        ? '<div class="verdict ' + verdict.tone + '">' + verdict.label + "</div>"
          + '<p class="says">' + esc(verdict.what) + "</p>"
        : "")
    + (steps.length
        ? '<ol class="steps">' + steps.map((step, index) => {
            const state = state_of(step);
            return '<li class="step ' + esc(state) + '" data-step="' + index + '">'
              + "<b>" + esc(step.text) + "</b>"
              + '<span class="at">' + esc(step.file) + ":" + esc(step.line) + "</span>"
              + (state === "open"
                  ? '<div class="acts">'
                    + '<button class="doit" data-step="' + index + '">Fix this</button>'
                    + '<button class="skipit" data-step="' + index + '">Skip</button>'
                    + "</div>"
                  : '<span class="state">' + esc(STATE_COPY[state] || state) + "</span>")
              + "</li>";
          }).join("")
          + "</ol>"
        : (verdict && current.decision === "pass"
            ? ""
            : '<div class="none">No critic offered a concrete fix. Read the findings and decide.</div>'))
    + progressStrip(PROGRESS)
    + sendBar(steps)
    + "  </section>"
    + "</div>";

  panel.querySelectorAll("button.doit").forEach((button) => {
    button.addEventListener("click", () => {
      const step = steps[Number(button.dataset.step)];
      if (step) void decideFix(step, button, "fix");
    });
  });
  panel.querySelectorAll("button.skipit").forEach((button) => {
    button.addEventListener("click", () => {
      const step = steps[Number(button.dataset.step)];
      if (step) void decideFix(step, button, "skip");
    });
  });
  const send = panel.querySelector("button.send");
  if (send) send.addEventListener("click", () => void sendNow(send, steps));
}

/* Claude is only actually stopped while a block request is open. That is the
 * one moment a fix can be carried out before anything else happens, so the
 * board says so plainly rather than leaving "queued" to be read as "now". */
const STAGE_COPY = {
  waiting: ["waiting", "Sent. Waiting for Claude to start."],
  working: ["working", "Claude is working on it."],
  reviewing: ["reviewing", "The officers are checking the fix."],
  cleared: ["cleared", "Satisfied. Claude may carry on."],
  rejected: ["rejected", "Still not satisfied. Read what they said above."],
};

function progressStrip(progress) {
  if (!progress) return "";
  const [tone, copy] = STAGE_COPY[progress.stage] || STAGE_COPY.waiting;
  const officers = progress.officers.map((officer) => {
    const mark = officer.verdict === "pass" ? "\u2714"
      : officer.verdict === "warn" ? "!"
      : officer.verdict ? "\u2716" : "\u2026";
    const cls = officer.verdict === "pass" ? "yes"
      : officer.verdict ? "no" : "pending";
    return '<span class="officer-call ' + cls + '">' + mark + " "
      + esc(String(officer.name).toUpperCase()) + "</span>";
  }).join("");

  return '<div class="progress ' + tone + '">'
    + '<div class="stage">' + esc(copy) + "</div>"
    + (officers ? '<div class="calls">' + officers + "</div>" : "")
    + (progress.stage === "working" && progress.files.length
        ? '<div class="on">' + esc(progress.files.join(", ")) + "</div>" : "")
    + "</div>";
}

function sendBar(steps) {
  const blocking = LIVE.find((request) => request.kind === "block");
  const accepted = steps.filter((step) => state_of(step) === "todo").length;
  const undecided = steps.filter((step) => state_of(step) === "open").length;

  if (blocking) {
    const label = accepted
      ? "Send " + accepted + " fix" + (accepted === 1 ? "" : "es") + " to Claude now"
      : "Let the edit stand";
    return '<div class="sendbar live">'
      + '<div class="held">Claude is stopped, waiting on you.</div>'
      + '<button class="send" data-choice="' + (accepted ? "fix" : "overrule") + '">'
      + label + "</button>"
      + (undecided
          ? '<div class="hint">' + undecided + " still undecided</div>"
          : "")
      + "</div>";
  }
  if (accepted) {
    return '<div class="sendbar">'
      + '<div class="hint">Claude is not stopped right now. '
      + accepted + " accepted fix" + (accepted === 1 ? "" : "es")
      + " will be handed over the moment it next tries to finish, or on your next message.</div>"
      + "</div>";
  }
  return "";
}

async function sendNow(button, steps) {
  const choice = button.dataset.choice || "fix";
  button.disabled = true;
  button.textContent = choice === "fix" ? "Sent" : "Overruled";
  try {
    await ANSWER(choice);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Retry";
    return;
  }
  for (const step of steps) {
    if (state_of(step) === "todo") FIX_STATE[stepKey(step)] = "sent";
  }
}

/* Accepting a fix is the one thing the board writes. In the editor it goes
 * through the extension, which can also nudge a terminal; served standalone it
 * POSTs to the local server. Either way the queue is the same file, and the
 * Stop gate is what makes the coder act on it. */
let ACCEPT = null;
let ANSWER = null;

async function decideFix(step, button, action) {
  const acts = button.closest(".acts");
  acts.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  try {
    await ACCEPT(step, action);
  } catch (error) {
    acts.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    button.textContent = "Retry";
    return;
  }
  const state = action === "skip" ? "skipped" : "todo";
  FIX_STATE[stepKey(step)] = state;
  const row = button.closest(".step");
  row.classList.remove("open");
  row.classList.add(state);
  acts.replaceWith(Object.assign(document.createElement("span"),
    { className: "state", textContent: STATE_COPY[state] }));
  refresh(ALL, PENDING, true);
}

/* ── Wiring ──────────────────────────────────────────────────────────── */

const SHELL = ''
  + '<div class="wrap">'
  + '  <div class="siren" id="siren">'
  + '    <canvas class="shield" id="shield" width="33" height="36"></canvas>'
  + '    <span class="brand">CCA PRECINCT</span>'
  + '    <span class="watch" id="watch"></span>'
  + '    <span class="dispatch" id="dispatch">ALL QUIET</span>'
  + "  </div>"
  + '  <div class="grid">'
  + '    <div class="panel"><h2>DUTY ROSTER</h2><div id="roster"></div></div>'
  + '    <div class="panel"><h2>BOOKING</h2><div class="rungs" id="rungs"></div>'
  + '      <div class="tally-row" id="chips"></div></div>'
  + "  </div>"
  + '  <div class="gallery" id="gallery">'
  + '    <canvas id="scene"></canvas>'
  + '    <div class="stamp" id="stamp"></div>'
  + '    <div class="caption" id="caption">Standing by.</div>'
  + "  </div>"
  + '  <div class="panel casefile" id="casefile" hidden></div>'
  + '  <div class="panel waiting" id="waiting" hidden></div>'
  + '  <div class="panel sheet"><h2>RAP SHEET <span id="sheet-count"></span></h2>'
  + '    <div id="sheet"></div></div>'
  + "</div>";

let ALL = [];
let PENDING = [];
let lastVoteTs = 0;

function refresh(events, pending, silent) {
  ALL = events;
  if (pending) PENDING = pending;
  const state = digest(ALL);
  paintBoard(state, PENDING);

  // The beat is always staffed. Officers are not conjured for an incident and
  // then deleted -- they stand post, which is what makes the panel readable
  // when nothing is happening.
  if (!SCENE.running) {
    SCENE.officers = state.critics.length
      ? state.critics.map((critic) => ({ name: critic.name, degraded: critic.degraded }))
      : [{ name: "design", degraded: false }, { name: "correctness", degraded: false }];
  }

  const latest = state.votes[state.votes.length - 1];
  if (silent) {
    // replaying the backlog on open would fire dozens of incidents at once
    if (latest) lastVoteTs = latest.ts;
    return;
  }
  if (latest && latest.ts > lastVoteTs) {
    lastVoteTs = latest.ts;
    const near = state.findings.filter((f) => Math.abs((f.at || 0) - latest.ts) < 8);
    document.getElementById("caption").innerHTML = "Last incident: <b>" + esc(latest.file) + "</b>";
    playIncident(latest.decision, near, state.critics);
  }
}

function boot() {
  document.body.innerHTML = SHELL;
  const shield = document.getElementById("shield").getContext("2d");
  drawSprite(shield, BADGE, 0, 0, 3, BADGE_PAL);

  SCENE.canvas = document.getElementById("scene");
  SCENE.ctx = SCENE.canvas.getContext("2d");
  sizeScene();
  window.addEventListener("resize", sizeScene);
  SCENE.frame = window.requestAnimationFrame(loop);
  refresh([], []);

  if (typeof acquireVsCodeApi === "function") {
    const vscode = acquireVsCodeApi();
    const asFix = (step) => ({
      file: step.file, line: step.line, severity: step.severity,
      kind: step.kind, critic: step.critic, issue: step.issue,
      suggestion: step.text, evidence: step.evidence,
    });
    ACCEPT = (step, action) => {
      vscode.postMessage({ kind: "decideFix", action: action || "fix", fix: asFix(step) });
      return Promise.resolve();
    };
    ANSWER = (choice) => {
      vscode.postMessage({ kind: "answerArbiter", choice });
      return Promise.resolve();
    };
    window.addEventListener("message", (message) => {
      const data = message.data || {};
      if (data.kind === "reset") refresh(data.events.slice(), PENDING, true);
      else if (data.kind === "events") refresh(ALL.concat(data.events), PENDING);
      else if (data.kind === "pending") refresh(ALL, data.requests);
    });
    vscode.postMessage({ kind: "ready" });
    return;
  }

  const post = async (route, body) => {
    const response = await fetch(route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error("the dashboard server refused " + route);
    return response.json();
  };

  ACCEPT = (step, action) => post(action === "skip" ? "fixes/skip" : "fixes", {
    file: step.file, line: step.line, severity: step.severity,
    kind: step.kind, critic: step.critic, issue: step.issue,
    suggestion: step.text, evidence: step.evidence,
  });

  ANSWER = (choice) => post("answer", { choice });

  const poll = async () => {
    try {
      const response = await fetch("events.jsonl?t=" + Date.now());
      if (!response.ok) return;
      const text = await response.text();
      const events = [];
      for (const line of text.split("\n")) {
        if (!line.trim()) continue;
        try { events.push(JSON.parse(line)); } catch (_) { /* corrupt line */ }
      }
      refresh(events, PENDING, first);
      first = false;
    } catch (_) { /* log not created yet */ }
  };
  let first = true;
  poll();
  window.setInterval(poll, 500);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
